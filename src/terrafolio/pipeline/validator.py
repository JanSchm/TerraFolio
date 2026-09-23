"""Tie-outs and plausibility: proving 300 independently-authored models cohere.

Two passes with opposite consequences, and the difference is the whole point.

**Tie-outs (§7) block.** They are arithmetic identities inside one file — if
``ebitda ≠ revenue - opex`` the file is not a model of anything, and no subset of it
is usable. A failing file is excluded by name, with the check, the year and the
signed residual, so a reader can tell a typo from a broken model.

**Plausibility checks (§10) warn.** They say a file looks unusual, not that it is
incoherent. A blocking rule here would let one stale file stop all work (A-7, epic
§12 Q3).

Tolerance is §7's — **€0.01m absolute or 0.1% relative, whichever is looser** — read
from ``validation.tolerance_abs_m`` and ``validation.tolerance_rel`` and never written
here. Tie-outs therefore work in **€m**, on the ``ProjectFile`` as parsed, because
that is the unit the tolerance is quoted in. Plausibility runs afterwards on
``ProjectArrays``, in euros, so that capex per kW is derived once rather than once per
unit system.

**What this module does not check**, because 1A's model already rejects it at parse
time and a check that can never fire is dead code: series length, the ``years``
anchor, nulls outside ``ratios.dscr``, unknown or missing keys, and §9's
reject-derived field names. §7.8's DSCR **coverage** rule is not among them, so it is
here.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np

from terrafolio.config.assumptions import AssumptionSet, ValidationParams
from terrafolio.domain.conventions import EUR_PER_EUR_MILLION, MWH_PER_GWH, YEARS
from terrafolio.domain.project_file import ProjectFile
from terrafolio.pipeline.arrays import KW_PER_MW, ProjectArrays, Vector
from terrafolio.pipeline.derive import min_dscr

__all__ = [
    "TIE_OUT_NAMES",
    "PlausibilityWarning",
    "TieOutFailure",
    "plausibility_warnings",
    "tie_out_failures",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class TieOutFailure:
    """One blocking identity a file does not satisfy.

    Mirrors ``docs/api.md`` §3's ``rejected`` row. ``residual_m`` is **signed** —
    actual less expected, in €m — so the sign says which way the file is wrong.
    """

    check: str
    year: int | None
    residual_m: float
    message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PlausibilityWarning:
    """One §10 band a file sits outside. Reported, never blocking."""

    check: str
    message: str


Check = Callable[[ProjectFile, ValidationParams], list[TieOutFailure]]


def _column(values: Sequence[float | None]) -> Vector:
    """A statement series as float64, with ``ratios.dscr``'s nulls as ``NaN``."""
    return np.array([np.nan if value is None else value for value in values], dtype=np.float64)


def _breach(actual: Vector, expected: Vector, params: ValidationParams) -> Vector:
    """How far each year misses, over tolerance. Zero means inside.

    ``docs/pipeline-schema.md`` §13 writes the same expression for the spreadsheet's
    per-year cells, so the workbook and the loader agree year by year rather than
    only on the verdict.
    """
    residual = np.abs(actual - expected)
    reference = np.maximum(np.abs(actual), np.abs(expected))
    tolerance = np.maximum(params.tolerance_abs_m, params.tolerance_rel * reference)
    over_tolerance: Vector = np.maximum(residual - tolerance, 0.0)
    return over_tolerance


def _by_year(
    check: str,
    actual: Vector,
    expected: Vector,
    base_year: int,
    params: ValidationParams,
) -> list[TieOutFailure]:
    """Report the first year that breaches, which is the one worth looking at."""
    breaches = np.flatnonzero(_breach(actual, expected, params))
    if breaches.size == 0:
        return []
    index = int(breaches[0])
    residual = float(actual[index] - expected[index])
    return [
        TieOutFailure(
            check=check,
            year=base_year + index,
            residual_m=residual,
            message=(
                f"{check}: {base_year + index} is out by €{residual:+.6f}m "
                f"({int(breaches.size)} of {actual.size} years breach tolerance)"
            ),
        )
    ]


def _whole_file(
    check: str, actual: float, expected: float, params: ValidationParams
) -> list[TieOutFailure]:
    """A single figure for the file, with no year to point at."""
    if not _breach(np.array([actual]), np.array([expected]), params)[0]:
        return []
    residual = actual - expected
    return [
        TieOutFailure(
            check=check,
            year=None,
            residual_m=residual,
            message=f"{check}: out by €{residual:+.6f}m (€{actual:.6f}m against €{expected:.6f}m)",
        )
    ]


# ---------------------------------------------------------------------------
# §7.1 — §7.7, the arithmetic identities
# ---------------------------------------------------------------------------


def _income_statement(file: ProjectFile, params: ValidationParams) -> list[TieOutFailure]:
    income = file.statements.income_statement
    base = file.assumptions.base_year
    revenue, opex = _column(income.revenue), _column(income.opex)
    ebitda, depreciation = _column(income.ebitda), _column(income.depreciation)
    ebit, interest = _column(income.ebit), _column(income.interest_expense)
    pbt, tax = _column(income.pbt), _column(income.tax_expense)
    return [
        *_by_year("7.1 ebitda = revenue - opex", ebitda, revenue - opex, base, params),
        *_by_year("7.1 ebit = ebitda - depreciation", ebit, ebitda - depreciation, base, params),
        *_by_year("7.1 pbt = ebit - interestExpense", pbt, ebit - interest, base, params),
        *_by_year(
            "7.1 netIncome = pbt - taxExpense",
            _column(income.net_income),
            pbt - tax,
            base,
            params,
        ),
    ]


def _revenue_ties_to_physicals(file: ProjectFile, params: ValidationParams) -> list[TieOutFailure]:
    """§7.2. The ``÷ 1e6`` is the €m boundary, not a fudge (A-1).

    The opex identity is §7.2's own commentary rather than a row in its table, but
    §7's header makes everything in the section blocking, and 1C's extractor runs it
    as a check across all 48 files. It is reproduced with the file's **own** declared
    escalator — which is what C-2 added those fields for — rather than a house rate.
    """
    base = file.assumptions.base_year
    physicals = file.statements.physicals
    generation = _column(physicals.generation_gwh)
    price = _column(physicals.achieved_price)

    asset = file.asset
    full_year_opex = asset.capacity_mw * KW_PER_MW * asset.opex_per_kw_year / EUR_PER_EUR_MILLION
    age = np.arange(YEARS) + base - asset.cod_year
    escalated = full_year_opex * (1.0 + file.assumptions.opex_escalation) ** np.maximum(age, 0)
    expected_opex = np.where(age < 0, 0.0, escalated)

    return [
        *_by_year(
            "7.2 revenue = generationGwh x 1000 x achievedPrice / 1e6",
            _column(file.statements.income_statement.revenue),
            generation * MWH_PER_GWH * price / EUR_PER_EUR_MILLION,
            base,
            params,
        ),
        *_by_year(
            "7.2 opex = capacityMw x 1000 x opexPerKwYear / 1e6, escalated",
            _column(file.statements.income_statement.opex),
            expected_opex,
            base,
            params,
        ),
    ]


def _cash_flow(file: ProjectFile, params: ValidationParams) -> list[TieOutFailure]:
    """§7.3. Note ``cashFlow.interestPaid``, not ``incomeStatement.interestExpense``."""
    cash = file.statements.cash_flow
    expected = (
        _column(file.statements.income_statement.ebitda)
        - _column(cash.interest_paid)
        - _column(cash.debt_repayment)
        - _column(cash.tax_paid)
        - _column(cash.capex)
        + _column(cash.debt_drawdown)
    )
    return _by_year(
        "7.3 fcfe = ebitda - interestPaid - debtRepayment - taxPaid - capex + debtDrawdown",
        _column(cash.fcfe),
        expected,
        file.assumptions.base_year,
        params,
    )


def _debt_schedule(file: ProjectFile, params: ValidationParams) -> list[TieOutFailure]:
    """§7.4 — roll-forward, continuity, and full amortisation."""
    base = file.assumptions.base_year
    debt = file.statements.debt_schedule
    opening, closing = _column(debt.opening), _column(debt.closing)
    drawdown, repayment = _column(debt.drawdown), _column(debt.repayment)

    previous_closing = np.concatenate((np.zeros(1), closing[:-1]))
    return [
        *_by_year(
            "7.4 closing = opening - repayment + drawdown",
            closing,
            opening - repayment + drawdown,
            base,
            params,
        ),
        *_by_year(
            "7.4 opening[t] = closing[t-1], opening[0] = 0",
            opening,
            previous_closing,
            base,
            params,
        ),
        *_by_year(
            "7.4 closing[last] = 0 (fully amortised)",
            closing[-1:],
            np.zeros(1),
            base + YEARS - 1,
            params,
        ),
    ]


def _funding(file: ProjectFile, params: ValidationParams) -> list[TieOutFailure]:
    """§7.5. ``totalCapex``, never the annual ``capex`` line (A-2)."""
    base = file.assumptions.base_year
    cash = file.statements.cash_flow
    capital = file.capital_structure
    capex = _column(cash.capex)
    debt_drawdown, equity_drawdown = _column(cash.debt_drawdown), _column(cash.equity_drawdown)
    return [
        *_whole_file(
            "7.5 sum debtDrawdown = seniorDebt",
            float(debt_drawdown.sum()),
            capital.senior_debt,
            params,
        ),
        *_whole_file(
            "7.5 sum equityDrawdown = totalCapex - seniorDebt",
            float(equity_drawdown.sum()),
            capital.total_capex - capital.senior_debt,
            params,
        ),
        *_whole_file("7.5 sum capex = totalCapex", float(capex.sum()), capital.total_capex, params),
        *_by_year(
            "7.5 capex[t] = debtDrawdown[t] + equityDrawdown[t]",
            capex,
            debt_drawdown + equity_drawdown,
            base,
            params,
        ),
    ]


def _depreciation_and_ppe(file: ProjectFile, params: ValidationParams) -> list[TieOutFailure]:
    """§7.6. The residual form, which holds even when the depreciation life runs
    past the window — a 25-year life from a 2033 COD ends in 2057 (A-3)."""
    base = file.assumptions.base_year
    ppe = _column(file.statements.balance_sheet.ppe)
    depreciation = _column(file.statements.income_statement.depreciation)
    capex = _column(file.statements.cash_flow.capex)

    previous_ppe = np.concatenate((np.zeros(1), ppe[:-1]))
    return [
        *_by_year(
            "7.6 ppe[t] = ppe[t-1] - depreciation[t] + capex[t]",
            ppe,
            previous_ppe - depreciation + capex,
            base,
            params,
        ),
        *_whole_file(
            "7.6 sum depreciation = totalCapex - ppe[last]",
            float(depreciation.sum()),
            file.capital_structure.total_capex - float(ppe[-1]),
            params,
        ),
    ]


def _dscr(file: ProjectFile, params: ValidationParams) -> list[TieOutFailure]:
    """§7.7, in the multiplied form.

    ``dscr = ebitda ÷ (interestPaid + debtRepayment)`` is checked as
    ``dscr x service = ebitda``. Algebraically the same identity, but it compares €m
    to €m, which is the unit §7's €0.01m tolerance is quoted in — an absolute
    tolerance against a bare ratio would mean nothing. It also never divides, so a
    year with no debt service cannot raise a warning that
    ``filterwarnings = ["error"]`` turns into a failure; such a year fails the
    identity on its own terms, as it should.
    """
    base = file.assumptions.base_year
    cash = file.statements.cash_flow
    dscr = _column(file.statements.ratios.dscr)
    covered = ~np.isnan(dscr)
    service = _column(cash.interest_paid) + _column(cash.debt_repayment)
    ebitda = _column(file.statements.income_statement.ebitda)
    return _by_year(
        "7.7 dscr x (interestPaid + debtRepayment) = ebitda",
        np.where(covered, dscr * service, 0.0),
        np.where(covered, ebitda, 0.0),
        base,
        params,
    )


def _dscr_coverage(file: ProjectFile, params: ValidationParams) -> list[TieOutFailure]:
    """§7.8's coverage rule — the one shape check 1A's model does not enforce.

    ``dscr`` must be non-null **exactly** where ``0 ≤ years[t] - codYear <
    debtTenorYears``. A file that carries a ratio outside the debt life, or omits one
    inside it, disagrees with its own debt schedule about when the debt exists.
    """
    base = file.assumptions.base_year
    age = np.arange(YEARS) + base - file.asset.cod_year
    expected = (age >= 0) & (age < file.assumptions.debt_tenor_years)
    actual = ~np.isnan(_column(file.statements.ratios.dscr))
    disagreements = np.flatnonzero(expected != actual)
    if disagreements.size == 0:
        return []
    index = int(disagreements[0])
    year = base + index
    missing = "missing" if expected[index] else "present"
    return [
        TieOutFailure(
            check="7.8 dscr non-null exactly where 0 <= year - codYear < debtTenorYears",
            year=year,
            residual_m=float(disagreements.size),
            message=(
                f"ratios.dscr is {missing} in {year}, against a COD of "
                f"{file.asset.cod_year} and a {file.assumptions.debt_tenor_years}-year "
                f"tenor ({int(disagreements.size)} years disagree)"
            ),
        )
    ]


TIE_OUT_CHECKS: Final[tuple[Check, ...]] = (
    _income_statement,
    _revenue_ties_to_physicals,
    _cash_flow,
    _debt_schedule,
    _funding,
    _depreciation_and_ppe,
    _dscr,
    _dscr_coverage,
)

TIE_OUT_NAMES: Final[tuple[str, ...]] = (
    "7.1 ebitda = revenue - opex",
    "7.1 ebit = ebitda - depreciation",
    "7.1 pbt = ebit - interestExpense",
    "7.1 netIncome = pbt - taxExpense",
    "7.2 revenue = generationGwh x 1000 x achievedPrice / 1e6",
    "7.2 opex = capacityMw x 1000 x opexPerKwYear / 1e6, escalated",
    "7.3 fcfe = ebitda - interestPaid - debtRepayment - taxPaid - capex + debtDrawdown",
    "7.4 closing = opening - repayment + drawdown",
    "7.4 opening[t] = closing[t-1], opening[0] = 0",
    "7.4 closing[last] = 0 (fully amortised)",
    "7.5 sum debtDrawdown = seniorDebt",
    "7.5 sum equityDrawdown = totalCapex - seniorDebt",
    "7.5 sum capex = totalCapex",
    "7.5 capex[t] = debtDrawdown[t] + equityDrawdown[t]",
    "7.6 ppe[t] = ppe[t-1] - depreciation[t] + capex[t]",
    "7.6 sum depreciation = totalCapex - ppe[last]",
    "7.7 dscr x (interestPaid + debtRepayment) = ebitda",
    "7.8 dscr non-null exactly where 0 <= year - codYear < debtTenorYears",
)
"""Every identity this module can report, for the CLI's per-file status table.

Named rather than counted so a check that is dropped or renamed shows up as a diff
here, and so ``terrafolio pipeline validate`` can list what it ran.
"""


def tie_out_failures(file: ProjectFile, assumptions: AssumptionSet) -> tuple[TieOutFailure, ...]:
    """Every §7 identity ``file`` breaks. Empty means the file loads.

    All of them run: a file with two broken identities should say so once, rather
    than rejecting on the first and revealing the second after the analyst fixes it.
    """
    params = assumptions.validation
    return tuple(failure for check in TIE_OUT_CHECKS for failure in check(file, params))


# ---------------------------------------------------------------------------
# §10 — the plausibility bands, on the euro arrays
# ---------------------------------------------------------------------------


def _within_ceiling(value: Vector, ceiling: Vector, params: ValidationParams) -> np.ndarray:
    """``≤`` as §10 means it: ``≤ ceiling x (1 + tolerance_rel)``.

    A file whose gearing sits exactly on its ceiling straddles it once the statements
    are rounded to the file's own precision — Almonte's 0.72 reads as 0.7200000062
    from the committed figures. Comparing a rounded quotient to an exact ceiling
    warns on a file that is right.
    """
    return value <= ceiling * (1.0 + params.tolerance_rel)


def _capacity_factor_warnings(
    arrays: ProjectArrays, params: ValidationParams
) -> list[PlausibilityWarning]:
    band = params.capacity_factor
    factor = arrays.asset.net_capacity_factor
    outside = np.flatnonzero((factor < band.low) | (factor > band.high))
    return [
        PlausibilityWarning(
            check="plausibility.capacityFactor",
            message=(
                f"{arrays.ids[index]}: net capacity factor {factor[index]:.3f} is outside "
                f"{band.low:.2f} to {band.high:.2f}"
            ),
        )
        for index in outside.tolist()
    ]


def _capex_per_kw_warnings(
    arrays: ProjectArrays, params: ValidationParams
) -> list[PlausibilityWarning]:
    """The one money-valued band, derived from the euro arrays so there is one
    capex-per-kW in the system rather than one per unit system."""
    per_kw = arrays.capex_per_kw
    warnings: list[PlausibilityWarning] = []
    for index, technology in enumerate(arrays.asset.technologies):
        band = params.capex_per_kw[technology]
        if band.low <= per_kw[index] <= band.high:
            continue
        warnings.append(
            PlausibilityWarning(
                check="plausibility.capexPerKw",
                message=(
                    f"{arrays.ids[index]}: €{per_kw[index]:,.0f}/kW is outside the "
                    f"{technology} band €{band.low:,.0f} to €{band.high:,.0f}/kW"
                ),
            )
        )
    return warnings


def _gearing_warnings(arrays: ProjectArrays, params: ValidationParams) -> list[PlausibilityWarning]:
    """Two ceilings: the stage's, and the project's own declared ``maxGearing``."""
    gearing = arrays.capital.gearing
    stage_ceiling = np.array(
        [params.stage_gearing_ceiling[stage] for stage in arrays.asset.stages], dtype=np.float64
    )
    warnings: list[PlausibilityWarning] = []
    for index in np.flatnonzero(~_within_ceiling(gearing, stage_ceiling, params)).tolist():
        warnings.append(
            PlausibilityWarning(
                check="plausibility.gearing",
                message=(
                    f"{arrays.ids[index]}: gearing {gearing[index]:.3f} is above the "
                    f"{arrays.asset.stages[index]} ceiling {stage_ceiling[index]:.2f}"
                ),
            )
        )
    declared = arrays.capital.max_gearing
    for index in np.flatnonzero(~_within_ceiling(gearing, declared, params)).tolist():
        warnings.append(
            PlausibilityWarning(
                check="plausibility.declaredGearing",
                message=(
                    f"{arrays.ids[index]}: gearing {gearing[index]:.3f} is above the file's "
                    f"own maxGearing {declared[index]:.3f}"
                ),
            )
        )
    return warnings


def _tax_warnings(arrays: ProjectArrays, params: ValidationParams) -> list[PlausibilityWarning]:
    """``taxExpense ≈ taxRate x max(0, pbt)``, year by year, at the **file's** rate.

    The tolerance comes from the assumption set; the rate does not. Each analyst
    declares their own, and the dispersion report is what makes disagreement visible.
    """
    income = arrays.statements.income
    expected = arrays.assumptions.tax_rate[:, None] * np.maximum(income.pbt, 0.0)
    residual = np.abs(income.tax_expense - expected)
    reference = np.maximum(np.abs(income.tax_expense), np.abs(expected))
    tolerance = np.maximum(
        params.tolerance_abs_m * EUR_PER_EUR_MILLION, params.tolerance_rel * reference
    )
    offending = np.flatnonzero((residual > tolerance).any(axis=-1))
    return [
        PlausibilityWarning(
            check="plausibility.tax",
            message=(
                f"{arrays.ids[index]}: taxExpense does not follow "
                f"{arrays.assumptions.tax_rate[index]:.1%} of positive PBT in "
                f"{int((residual[index] > tolerance[index]).sum())} year(s); no loss "
                f"carryforward is modelled"
            ),
        )
        for index in offending.tolist()
    ]


def _dscr_band_warnings(
    arrays: ProjectArrays, params: ValidationParams
) -> list[PlausibilityWarning]:
    """The band stabilised-EBITDA sizing produces (epic §6.3, decision D-3).

    Unlevered projects are silent here, not warned about: ``NaN`` fails both
    comparisons, which is the right answer for an asset with no debt service.
    """
    band = params.min_dscr_band
    lowest = min_dscr(arrays)
    outside = np.flatnonzero((lowest < band.low) | (lowest > band.high))
    return [
        PlausibilityWarning(
            check="plausibility.minDscr",
            message=(
                f"{arrays.ids[index]}: minimum DSCR {lowest[index]:.2f} is outside "
                f"{band.low:.2f}x to {band.high:.2f}x"
            ),
        )
        for index in outside.tolist()
    ]


def plausibility_warnings(
    arrays: ProjectArrays, assumptions: AssumptionSet
) -> tuple[PlausibilityWarning, ...]:
    """Every §10 band the loaded pipeline sits outside. Never blocking (A-7)."""
    params = assumptions.validation
    return (
        *_capacity_factor_warnings(arrays, params),
        *_capex_per_kw_warnings(arrays, params),
        *_gearing_warnings(arrays, params),
        *_tax_warnings(arrays, params),
        *_dscr_band_warnings(arrays, params),
    )
