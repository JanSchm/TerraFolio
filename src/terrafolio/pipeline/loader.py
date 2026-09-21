"""Reading a directory of analyst statement files into something a run can use.

Three kinds of outcome, and keeping them apart is most of the job.

**A file is rejected, by name, with a reason.** It failed to parse, failed 1A's
schema, carried a field §9 forbids, or broke a §7 tie-out. The rest of the pipeline
still loads. A rejected file is never half-loaded and never silently dropped: it is in
``LoadResult.rejected`` with the check, the year and the signed residual, so 3A can
serve it and 4A can show a count with what failed.

**The load fails as a whole.** ``docs/pipeline-schema.md`` §7.9 has two of these, and
issue #6's ordering rule adds a third. Each breaks the *index* rather than a file, and
no subset of the pipeline is usable once one does:

- two files declaring the same ``id`` — the error names both, because silently
  preferring one would make the snapshot hash a lie;
- files disagreeing about ``baseYear`` — series are summed by position, so a 2027 file
  and a 2028 file would have different calendar years added together (A-13);
- ids that are not zero-padded to a uniform width — canonical order is lexicographic,
  so ``"P10" < "P9"``, and the GA's PRNG draws are indexed by position. A mixed-width
  pipeline changes every result silently (decision C-5, ``domain/conventions.py``).

**A file loads with a warning.** §10's plausibility bands and §11's dispersion report
say a file looks unusual, never that it is wrong (A-7).

This module is also the **€m to euro boundary**. Files are €m; ``ProjectArrays`` and
everything downstream is euros. The only other conversion in the system is the API's,
on the way back out.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
from pydantic import ValidationError

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.domain.conventions import EUR_PER_EUR_MILLION, YEARS, canonical_order
from terrafolio.domain.errors import render_validation_error
from terrafolio.domain.project_file import DerivedFieldError, ProjectFile
from terrafolio.pipeline.arrays import (
    AssetArrays,
    BalanceSheetArrays,
    CapitalStructureArrays,
    CashFlowArrays,
    DebtScheduleArrays,
    DeclaredAssumptionArrays,
    ExecutionArrays,
    IncomeStatementArrays,
    LocationArrays,
    Matrix,
    PhysicalsArrays,
    ProjectArrays,
    RatiosArrays,
    RevenueArrays,
    StatementArrays,
    Vector,
)
from terrafolio.pipeline.dispersion import DispersionReport, dispersion_report
from terrafolio.pipeline.snapshot import file_content_hash, snapshot_hash
from terrafolio.pipeline.validator import (
    PlausibilityWarning,
    TieOutFailure,
    plausibility_warnings,
    tie_out_failures,
)

__all__ = [
    "LoadResult",
    "PipelineLoadError",
    "RejectedFile",
    "load_pipeline",
]

_UK_ALIAS = "UK"
_UK_ISO = "GB"

MILLISECONDS_PER_SECOND: Final = 1000  # structural: unit definition, not a calibration
"""``docs/api.md`` reports every elapsed time as ``durationMs``."""

SCHEMA_CHECK = "schema"
"""Reported for a file that never reached the tie-outs: unreadable, or malformed."""

DERIVED_FIELD_CHECK = "9 reject-derived fields"
"""Reported for a file carrying a *result* rather than a declaration.

Named apart from the schema because it is a different conversation with the analyst:
the file is well-formed, it is just claiming to know something only a run can know.
"""


class PipelineLoadError(ValueError):
    """The pipeline is unusable as a whole — not one bad file, a broken index."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RejectedFile:
    """One file excluded from the load, with the reason named.

    Keyed on ``file`` rather than ``id`` — deliberately, and as ``docs/api.md`` §3
    has it: a file that fails to parse may have no usable id to key on.
    """

    file: str
    check: str
    year: int | None
    residual_m: float
    message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class LoadResult:
    """Everything one pass over the pipeline directory produced."""

    arrays: ProjectArrays
    """The loaded projects, in canonical order, in euros and GWh."""

    files: tuple[ProjectFile, ...]
    """The same projects as validated models, **in the same order as** ``arrays.ids``.

    The core never sees a name or a provenance block, so anything that needs one joins
    by position. A test pins the two orderings together.
    """

    file_hashes: Mapping[str, str]
    pipeline_hash: str
    rejected: tuple[RejectedFile, ...]
    warnings: tuple[PlausibilityWarning, ...]
    dispersion: DispersionReport
    file_count: int
    """Files seen on disk, loaded or not."""

    duration_ms: int

    @property
    def loaded_count(self) -> int:
        return self.arrays.count


def _rejections_for(name: str, failures: Iterable[TieOutFailure]) -> list[RejectedFile]:
    return [
        RejectedFile(
            file=name,
            check=failure.check,
            year=failure.year,
            residual_m=failure.residual_m,
            message=failure.message,
        )
        for failure in failures
    ]


def _parse(path: Path) -> tuple[ProjectFile | None, bytes, list[RejectedFile]]:
    """Read and validate one file, or say why it could not be.

    Returns the raw bytes alongside the model so the content hash is taken from the
    same read. Hashing the file separately would read every file twice and, worse,
    could hash a version that is not the one that loaded.
    """
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        return None, b"", [_schema_rejection(path.name, f"unreadable: {error}")]

    try:
        return ProjectFile.model_validate(payload), raw, []
    except ValidationError as error:
        problems = "; ".join(render_validation_error(error))
        return None, raw, [_schema_rejection(path.name, problems, check=_check_for(error))]


def _check_for(error: ValidationError) -> str:
    """Name §9's rule when that is what failed, rather than lumping it under the schema.

    ``DerivedFieldError`` is raised inside a ``model_validator``, so pydantic wraps it
    and it never propagates on its own — catching it directly would be dead code. The
    original is still on the error's context, which is where a caller can see that a
    file was rejected for carrying a *result* rather than for being malformed.
    """
    for problem in error.errors():
        context = problem.get("ctx") or {}
        if isinstance(context.get("error"), DerivedFieldError):
            return DERIVED_FIELD_CHECK
    return SCHEMA_CHECK


def _schema_rejection(name: str, message: str, *, check: str = SCHEMA_CHECK) -> RejectedFile:
    return RejectedFile(file=name, check=check, year=None, residual_m=0.0, message=message)


# ---------------------------------------------------------------------------
# §7.9 and C-5 — the checks that fail the load rather than a file
# ---------------------------------------------------------------------------


def _reject_duplicate_ids(loaded: Sequence[tuple[Path, ProjectFile]]) -> None:
    counts = Counter(file.id for _, file in loaded)
    duplicates = sorted(project_id for project_id, seen in counts.items() if seen > 1)
    if not duplicates:
        return
    detail = "; ".join(
        f"{project_id}: " + ", ".join(sorted(p.name for p, f in loaded if f.id == project_id))
        for project_id in duplicates
    )
    raise PipelineLoadError(
        f"duplicate project ids abort the load, naming every file that claims one — "
        f"{detail}. Preferring one silently would make the snapshot hash a lie."
    )


def _require_one_base_year(loaded: Sequence[tuple[Path, ProjectFile]]) -> int:
    """A-13: the pipeline base year is the one every file shares."""
    by_year: dict[int, list[str]] = {}
    for path, file in loaded:
        by_year.setdefault(file.assumptions.base_year, []).append(path.name)
    if len(by_year) == 1:
        return next(iter(by_year))
    distribution = "; ".join(
        f"{year}: {len(names)} file(s) — {', '.join(sorted(names))}"
        for year, names in sorted(by_year.items())
    )
    raise PipelineLoadError(
        f"the pipeline base year must be unanimous; {len(by_year)} were declared. "
        f"{distribution}. Portfolio series are summed by position, so files based in "
        f"different years would have different calendar years added together. "
        f"Re-basing a file is the analyst's job; the loader never shifts a series."
    )


def _require_uniform_id_width(ids: Sequence[str]) -> None:
    """C-5: canonical order is lexicographic, so ``"P10" < "P9"``."""
    widths = sorted({len(project_id) for project_id in ids})
    if len(widths) <= 1:
        return
    examples = ", ".join(sorted(ids, key=len)[:: max(len(ids) - 1, 1)])
    raise PipelineLoadError(
        f"project ids must be zero-padded to a uniform width; found widths {widths} "
        f"(for example {examples}). Canonical order is a plain string sort, so mixed "
        f"widths put 'P10' before 'P9' — and the optimiser indexes its PRNG draws by "
        f"position, so that changes every result without anything noticing."
    )


# ---------------------------------------------------------------------------
# Files to arrays — and the one place €m becomes euros
# ---------------------------------------------------------------------------


def _money(rows: Iterable[Sequence[float]]) -> Matrix:
    """A statement series from €m to euros. **This is the boundary.**"""
    return _plain(rows) * EUR_PER_EUR_MILLION


def _plain(rows: Iterable[Sequence[float]]) -> Matrix:
    """A statement series that is not money: GWh, €/MWh, or a ratio.

    Reshaped rather than stacked so that an empty pipeline still yields a
    ``(0, 30)`` matrix. Every consumer reduces along the year axis, and a ``(0,)``
    would make those reductions fail somewhere far from here.
    """
    return np.array(list(rows), dtype=np.float64).reshape(-1, YEARS)


def _scalar(values: Iterable[float]) -> Vector:
    return np.array(list(values), dtype=np.float64)


def _integers(values: Iterable[int]) -> np.ndarray:
    return np.array(list(values), dtype=np.int64)


def _country_code(file: ProjectFile) -> str:
    """``UK`` is an accepted alias; ``GB`` is the code everything else uses."""
    code = file.location.country_code
    return _UK_ISO if code == _UK_ALIAS else code


def _statements(files: Sequence[ProjectFile]) -> StatementArrays:
    return StatementArrays(
        years=_integers(year for file in files for year in file.statements.years).reshape(
            -1, YEARS
        ),
        physicals=PhysicalsArrays(
            generation_gwh=_plain(f.statements.physicals.generation_gwh for f in files),
            achieved_price=_plain(f.statements.physicals.achieved_price for f in files),
        ),
        income=IncomeStatementArrays(
            revenue=_money(f.statements.income_statement.revenue for f in files),
            opex=_money(f.statements.income_statement.opex for f in files),
            ebitda=_money(f.statements.income_statement.ebitda for f in files),
            depreciation=_money(f.statements.income_statement.depreciation for f in files),
            ebit=_money(f.statements.income_statement.ebit for f in files),
            interest_expense=_money(f.statements.income_statement.interest_expense for f in files),
            pbt=_money(f.statements.income_statement.pbt for f in files),
            tax_expense=_money(f.statements.income_statement.tax_expense for f in files),
            net_income=_money(f.statements.income_statement.net_income for f in files),
        ),
        cash_flow=CashFlowArrays(
            interest_paid=_money(f.statements.cash_flow.interest_paid for f in files),
            debt_repayment=_money(f.statements.cash_flow.debt_repayment for f in files),
            tax_paid=_money(f.statements.cash_flow.tax_paid for f in files),
            capex=_money(f.statements.cash_flow.capex for f in files),
            debt_drawdown=_money(f.statements.cash_flow.debt_drawdown for f in files),
            equity_drawdown=_money(f.statements.cash_flow.equity_drawdown for f in files),
            fcfe=_money(f.statements.cash_flow.fcfe for f in files),
        ),
        debt=DebtScheduleArrays(
            opening=_money(f.statements.debt_schedule.opening for f in files),
            drawdown=_money(f.statements.debt_schedule.drawdown for f in files),
            repayment=_money(f.statements.debt_schedule.repayment for f in files),
            closing=_money(f.statements.debt_schedule.closing for f in files),
        ),
        balance=BalanceSheetArrays(ppe=_money(f.statements.balance_sheet.ppe for f in files)),
        ratios=RatiosArrays(
            dscr=_plain(
                [np.nan if value is None else value for value in f.statements.ratios.dscr]
                for f in files
            )
        ),
    )


def _to_arrays(files: Sequence[ProjectFile], base_year: int) -> ProjectArrays:
    """Assemble the struct of arrays. **The €m boundary is crossed here.**"""
    return ProjectArrays(
        ids=tuple(file.id for file in files),
        base_year=base_year,
        location=LocationArrays(
            latitude=_scalar(f.location.lat for f in files),
            longitude=_scalar(f.location.lon for f in files),
            country_codes=tuple(_country_code(f) for f in files),
        ),
        asset=AssetArrays(
            technologies=tuple(f.asset.technology for f in files),
            stages=tuple(f.asset.stage for f in files),
            capacity_mw=_scalar(f.asset.capacity_mw for f in files),
            cod_year=_integers(f.asset.cod_year for f in files),
            net_capacity_factor=_scalar(f.asset.net_capacity_factor for f in files),
            opex_per_kw_year=_scalar(f.asset.opex_per_kw_year for f in files),
        ),
        revenue=RevenueArrays(
            ppa_share=_scalar(f.revenue.ppa_share for f in files),
            ppa_price=_scalar(f.revenue.ppa_price for f in files),
            ppa_tenor_years=_integers(f.revenue.ppa_tenor_years for f in files),
            country_baseload_price=_scalar(f.revenue.country_baseload_price for f in files),
            capture_factor=_scalar(f.revenue.capture_factor for f in files),
        ),
        execution=ExecutionArrays(
            development_risk_score=_scalar(f.execution.development_risk_score for f in files),
            grid_secured=np.array([f.execution.grid_secured for f in files], dtype=np.bool_),
            om_contracted=np.array([f.execution.om_contracted for f in files], dtype=np.bool_),
            currencies=tuple(str(f.execution.currency) for f in files),
        ),
        capital=CapitalStructureArrays(
            total_capex=_scalar(f.capital_structure.total_capex for f in files)
            * EUR_PER_EUR_MILLION,
            senior_debt=_scalar(f.capital_structure.senior_debt for f in files)
            * EUR_PER_EUR_MILLION,
            max_gearing=_scalar(f.capital_structure.max_gearing for f in files),
        ),
        assumptions=DeclaredAssumptionArrays(
            tax_rate=_scalar(f.assumptions.tax_rate for f in files),
            depreciation_years=_integers(f.assumptions.depreciation_years for f in files),
            debt_rate=_scalar(f.assumptions.debt_rate for f in files),
            debt_tenor_years=_integers(f.assumptions.debt_tenor_years for f in files),
            degradation_rate=_scalar(f.assumptions.degradation_rate for f in files),
            price_escalation=_scalar(f.assumptions.price_escalation for f in files),
            merchant_escalation=_scalar(f.assumptions.merchant_escalation for f in files),
            opex_escalation=_scalar(f.assumptions.opex_escalation for f in files),
            target_dscr=_scalar(f.assumptions.target_dscr for f in files),
        ),
        statements=_statements(files),
    )


def load_pipeline(directory: Path, assumptions: AssumptionSet) -> LoadResult:
    """Load every ``*.json`` under ``directory``, validated and tied out.

    Raises :class:`PipelineLoadError` when the pipeline is broken as a whole. Files
    that fail on their own are excluded and reported; the load succeeds without them.
    """
    started = time.perf_counter()
    paths = sorted(directory.glob("*.json"))

    accepted: list[tuple[Path, ProjectFile]] = []
    hashes: dict[Path, str] = {}
    rejected: list[RejectedFile] = []

    for path in paths:
        file, raw, problems = _parse(path)
        if file is None:
            rejected.extend(problems)
            continue
        failures = tie_out_failures(file, assumptions)
        if failures:
            rejected.extend(_rejections_for(path.name, failures))
            continue
        accepted.append((path, file))
        hashes[path] = file_content_hash(raw)

    _reject_duplicate_ids(accepted)
    base_year = _require_one_base_year(accepted) if accepted else 0
    _require_uniform_id_width([file.id for _, file in accepted])

    order = {file.id: (path, file) for path, file in accepted}
    files = tuple(order[project_id][1] for project_id in canonical_order(order))
    file_hashes = {
        project_id: hashes[order[project_id][0]] for project_id in canonical_order(order)
    }

    arrays = _to_arrays(files, base_year)
    return LoadResult(
        arrays=arrays,
        files=files,
        file_hashes=file_hashes,
        pipeline_hash=snapshot_hash(file_hashes),
        rejected=tuple(rejected),
        warnings=plausibility_warnings(arrays, assumptions),
        dispersion=dispersion_report(arrays),
        file_count=len(paths),
        duration_ms=int((time.perf_counter() - started) * MILLISECONDS_PER_SECOND),
    )
