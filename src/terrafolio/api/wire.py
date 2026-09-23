"""Request and response shapes that ``domain/results.py`` does not already own.

1A's result models — ``ProjectScalars``, ``Holding``, ``PortfolioAggregates``,
``ConvergencePoint``, ``RunProvenance``, ``RunRecord``, ``FeasibilityWarning`` —
*are* the wire for everything a run produces, and are reused here rather than
respelled. What is left is the envelopes: the pipeline listing, the load report,
the preview, the run request and its acknowledgement, and the assumption set.

Everything is camelCase on the wire and snake_case in Python (§1.1), and every
amount in €m carries an ``_m`` suffix while a per-unit price does not (§1.2).
Both are properties of this module's config rather than of each field.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.domain.enums import Effort, RunStatus
from terrafolio.domain.mandate import Mandate
from terrafolio.domain.results import FeasibilityWarning, ProjectScalars
from terrafolio.pipeline.dispersion import Distribution
from terrafolio.pipeline.loader import LoadResult
from terrafolio.store.snapshots import assumption_payload

__all__ = [
    "AssumptionsResponse",
    "DispersionEntry",
    "OptimisationAccepted",
    "OptimisationRequest",
    "PipelineResponse",
    "PipelineStatusResponse",
    "PreviewRequest",
    "PreviewResponse",
    "RejectedFileEntry",
    "RunInFlight",
    "WarningEntry",
    "assumptions_response",
    "pipeline_status",
    "statements_payload",
]

WIRE_CONFIG: Final = ConfigDict(
    alias_generator=to_camel,
    validate_by_alias=True,
    validate_by_name=True,
    serialize_by_alias=True,
    extra="forbid",
    allow_inf_nan=False,
)


class _Wire(BaseModel):
    model_config = WIRE_CONFIG


# --------------------------------------------------------------------------
# GET /pipeline
# --------------------------------------------------------------------------


class PipelineResponse(_Wire):
    """§2. **Scalars only, never the 30-year arrays.**

    Measured on the template, the arrays are 1.8 MB at 300 projects against
    400 KB for everything else — four and a half times the whole rest of the
    response, on an endpoint the mandate screen hits on every load. §7.5's detail
    sheet needs only these, so the arrays live at ``GET /projects/{id}/statements``.
    """

    pipeline_hash: str
    assumption_set_id: str
    engine_version: str
    base_year: int
    hold_years: int
    project_count: int
    projects: tuple[ProjectScalars, ...]


# --------------------------------------------------------------------------
# GET /pipeline/status and POST /pipeline/reload
# --------------------------------------------------------------------------


class RejectedFileEntry(_Wire):
    """One file that did not load, keyed on its **filename** rather than its id.

    A file that fails to parse may have no usable id to key on, which is why §3
    reports the name. ``residual_m`` is the signed miss against the €0.01m / 0.1%
    tolerance, so a reader can see whether a rejection is a typo or a broken
    model.
    """

    file: str
    check: str
    year: int | None
    residual_m: float = Field(alias="residual_m")
    message: str


class WarningEntry(_Wire):
    """One plausibility warning. Loaded normally: these never block (A-7)."""

    id: str | None
    check: str
    message: str


class DispersionEntry(_Wire):
    """One declared assumption's spread across the files that declare it."""

    count: int
    median: float
    min: float
    max: float
    outliers: tuple[str, ...]


class PipelineStatusResponse(_Wire):
    """§3, and the body ``POST /pipeline/reload`` answers with.

    One shape for "what is the state of the pipeline", rather than a second one
    that reports the same facts after a reload.
    """

    pipeline_hash: str
    loaded_at: dt.datetime
    file_count: int
    loaded_count: int
    duration_ms: int
    rejected: tuple[RejectedFileEntry, ...]
    warnings: tuple[WarningEntry, ...]
    dispersion: Mapping[str, DispersionEntry]


def _warning_id(message: str, known: frozenset[str]) -> str | None:
    """Recover which project a plausibility warning is about.

    ``pipeline/validator.py``'s ``PlausibilityWarning`` carries only ``check``
    and ``message``, while §3 puts an ``id`` on the wire. Every warning that
    concerns one project writes ``"{id}: …"``, so the id is recoverable — but
    only by that convention, so it is **checked against the loaded ids** and
    reported as ``null`` rather than guessed at. A warning about the pipeline as
    a whole legitimately has no id.
    """
    head, separator, _ = message.partition(": ")
    return head if separator and head in known else None


def pipeline_status(result: LoadResult, *, loaded_at: dt.datetime) -> PipelineStatusResponse:
    """§3's body for one load."""
    known = frozenset(result.arrays.ids)
    return PipelineStatusResponse(
        pipeline_hash=result.pipeline_hash,
        loaded_at=loaded_at,
        file_count=result.file_count,
        loaded_count=result.loaded_count,
        duration_ms=result.duration_ms,
        rejected=tuple(
            RejectedFileEntry(
                file=row.file,
                check=row.check,
                year=row.year,
                residual_m=row.residual_m,
                message=row.message,
            )
            for row in result.rejected
        ),
        warnings=tuple(
            WarningEntry(id=_warning_id(row.message, known), check=row.check, message=row.message)
            for row in result.warnings
        ),
        dispersion={
            name: _distribution(spread) for name, spread in result.dispersion.pipeline_wide.items()
        },
    )


def _distribution(spread: Distribution) -> DispersionEntry:
    return DispersionEntry(
        count=spread.count,
        median=spread.median,
        min=spread.minimum,
        max=spread.maximum,
        outliers=spread.outliers,
    )


# --------------------------------------------------------------------------
# POST /mandate/preview
# --------------------------------------------------------------------------


class PreviewRequest(_Wire):
    mandate: Mandate
    locked_ids: tuple[str, ...] = ()
    excluded_ids: tuple[str, ...] = ()


class PreviewResponse(_Wire):
    """§5's figures, and whether the run button is live.

    **``runnable`` is false if and only if some warning blocks**, and those are
    exactly the two conditions ``POST /optimisations`` answers with 422. A
    preview that reported ``runnable: true`` for a mandate the run rejects would
    light up a button that cannot work.
    """

    eligible_count: int
    total_count: int
    eligible_capacity_mw: float
    eligible_equity_m: float = Field(alias="eligibleEquity_m")
    eligible_solar_share: float
    eligible_gearing: float
    locked_equity_m: float = Field(alias="lockedEquity_m")
    warnings: tuple[FeasibilityWarning, ...]
    screens_to_widen: tuple[str, ...]
    """§13's "naming the screens to widen", worst offender first."""
    runnable: bool


# --------------------------------------------------------------------------
# POST /optimisations
# --------------------------------------------------------------------------


class OptimisationRequest(_Wire):
    """§6.2. ``seed`` omitted means the server draws one and records it."""

    mandate: Mandate
    locked_ids: tuple[str, ...] = ()
    excluded_ids: tuple[str, ...] = ()
    effort: Effort = Effort.STANDARD
    seed: int | None = None
    pipeline_hash: str | None = None
    """The snapshot the client was looking at. A mismatch is 409, never a
    silent run against different data."""
    assumption_set_id: str | None = None


class OptimisationAccepted(_Wire):
    """§6.3's 202 body. ``seed`` is the **resolved** one, always."""

    run_id: str
    run_ref: str
    status: RunStatus
    stream_url: str
    result_url: str
    total_generations: int
    seed: int


class RunInFlight(_Wire):
    """§8's body for a run that has not produced a portfolio yet.

    200 rather than 202: the run *is* the resource, and 202 would say the
    request to read it had been accepted rather than that the run has not
    finished. ``generation`` is what a non-streaming client polls.
    """

    run_id: str
    run_ref: str
    status: RunStatus
    created_at: dt.datetime
    effort: Effort
    mandate: Mandate
    locked_ids: tuple[str, ...]
    excluded_ids: tuple[str, ...]
    generation: int
    total_generations: int


# --------------------------------------------------------------------------
# GET /projects/{id}/statements and GET /assumptions
# --------------------------------------------------------------------------

STATEMENT_BLOCKS: Final[tuple[str, ...]] = (
    "years",
    "physicals",
    "incomeStatement",
    "cashFlow",
    "debtSchedule",
    "balanceSheet",
    "ratios",
)
"""§4's blocks, in ``pipeline-schema.md`` §5's order."""


def statements_payload(file: Any) -> dict[str, Any]:
    """§4's body: one project's thirty years, in €m and GWh, plus its context.

    ``assumptions`` is the file's **own** declared block, so a caller can
    interpret the arrays without a second request, and ``provenance`` is the
    full block *with* its notes — the half ``GET /pipeline`` leaves out.
    """
    dumped = file.model_dump(by_alias=True, mode="json")
    statements = dumped["statements"]
    return {
        "id": dumped["id"],
        "assumptions": dumped["assumptions"],
        **{block: statements[block] for block in STATEMENT_BLOCKS},
        "provenance": dumped["provenance"],
    }


class AssumptionsResponse(_Wire):
    """§10. Every rate, weight, floor, clamp, tolerance and band the engine uses.

    The groups are taken from the **stored** payload — the same bytes
    ``assumption_set.payload_json`` holds and the same ones
    ``assumptionSetHash`` covers — so what a client reads here and what a run
    was produced under cannot be two different things.
    """

    id: str
    hash: str
    name: str
    label: str
    version: int
    created_at: dt.datetime
    exit_multiples: Mapping[str, float]
    lcoe_discount_rate: float
    co2_factor_t_per_mwh: float
    """``to_camel`` already spells this ``co2FactorTPerMwh``, as §10 writes it."""
    objective_weights: Mapping[str, Any]
    ga: Mapping[str, Any]
    risk_caps: Mapping[str, Any]
    feasibility: Mapping[str, Any]
    validation: Mapping[str, Any]
    interpretation: Mapping[str, Any]
    generator: Mapping[str, Any]


def assumptions_response(
    assumptions: AssumptionSet, *, recorded_at: dt.datetime
) -> AssumptionsResponse:
    """§10's body, read back out of the canonical stored payload.

    The seven names §10 lists do not map one for one onto the dataclass fields —
    ``lcoeDiscountRate`` is ``lcoe_real_discount_rate``, ``objectiveWeights`` is
    ``objective`` — so the handful that differ are spelled out rather than
    camelised blindly, and the three groups §10's illustrative list omits
    (``ga``, ``feasibility``, ``interpretation``) are included, because §10's
    claim is that everything the engine reads appears here and nowhere else.
    """
    payload: dict[str, Any] = json.loads(assumption_payload(assumptions))
    return AssumptionsResponse(
        id=assumptions.assumption_set_id,
        hash=assumptions.content_hash,
        name=assumptions.name,
        label=assumptions.meta.label,
        version=assumptions.meta.version,
        created_at=recorded_at,
        exit_multiples=payload["exit_multiples"],
        lcoe_discount_rate=payload["lcoe_real_discount_rate"],
        co2_factor_t_per_mwh=payload["co2_t_per_mwh"],
        objective_weights=payload["objective"],
        ga=payload["ga"],
        risk_caps=payload["risk_caps"],
        feasibility=payload["feasibility"],
        validation=payload["validation"],
        interpretation=payload["interpretation"],
        generator=payload["generator"],
    )
