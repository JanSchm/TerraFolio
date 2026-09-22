"""LCOE, terminal value, IRR and MOIC -- the house model's returns arithmetic.

Two of these turn on questions the specification leaves open, which is why they
are flags in the assumption set rather than choices buried in code:

``interpretation.exit_year_fcfe_included``
    Does the exit year's **own** free cash flow count alongside the terminal
    value, or does the terminal value replace it? §9.4 says the IRR runs "over
    the equity cash flow truncated at the hold year, with a terminal value of
    the exit multiple x exit-year EBITDA less outstanding debt", which does not
    settle it.

``interpretation.lcoe_opex_basis``
    §9.4 gives LCOE as "(capex + PV of opex) / PV of generation, 6% real" and
    does not say what opex is discounted *from*, or whether it escalates. Three
    readings are implemented and one is pinned; see
    :data:`LCOE_BASES` and ``docs/decisions.md``.

Issue 2A owns ``economics/`` and the mandate-dependent derivations the optimiser
runs on. This module is the **house model's own**, used by the generator's checks
and by the interpretation sweep that pins the two flags. If 2A wants to share
these rather than reimplement them, ``model`` needs to rank below ``economics``
in ``tests/unit/test_import_boundaries.py``'s ``LAYERS`` -- a one-line change
that guard's own docstring sanctions for exactly these four packages.

Numeric core: numpy, the standard library and ``config`` only.
"""

from __future__ import annotations

from typing import Final

import numpy as np
import numpy.typing as npt

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.domain.conventions import EUR_PER_EUR_MILLION, MWH_PER_GWH, YEARS, exit_index
from terrafolio.domain.enums import Technology
from terrafolio.model.project import ProjectInputs, ProjectStatements
from terrafolio.model.rounding import js_round

__all__ = [
    "LCOE_BASES",
    "exit_multiple",
    "hold_truncated_fcfe",
    "irr",
    "lcoe",
    "moic",
    "terminal_value",
]

_F64: Final = np.float64

LCOE_BASES: Final[tuple[str, ...]] = (
    "real_from_base_year",
    "real_from_cod",
    "nominal_from_base_year",
)
"""The three readings of §9.4's "PV of opex", discounted at 6% real.

``real_from_base_year``
    Unescalated opex, discounted from the model's base year. Capex enters
    undiscounted at t0.
``real_from_cod``
    Unescalated opex, discounted from the project's own COD -- so a late-COD
    asset is not penalised for the years before it exists.
``nominal_from_base_year``
    Opex escalated at the file's own rate, discounted from the base year.

Issue #8 names the middle and the last. The first is what the JavaScript
reference computes, and the sweep in ``tests/golden/test_interpretations.py``
decides between them against 1C's oracle rather than by argument.
"""


def exit_multiple(technology: Technology, base: float, assumptions: AssumptionSet) -> float:
    """The EV/EBITDA multiple for ``technology`` at an exit-multiple ``base``.

    The assumption set states the three multiples directly, calibrated at the
    reference's base of 9: solar at the base, onshore wind half a turn below,
    offshore half a turn above. The offset is recovered by taking solar as the
    base rather than by writing 9 down again, so re-calibrating the table moves
    all three together and nothing here needs editing.
    """
    solar = assumptions.exit_multiples[Technology.SOLAR]
    return base + (assumptions.exit_multiples[technology] - solar)


def lcoe(
    inputs: ProjectInputs,
    statements: ProjectStatements,
    assumptions: AssumptionSet,
    *,
    basis: str | None = None,
) -> float:
    """Levelised cost of energy in EUR/MWh, at the configured real rate (§9.4).

    ``(capex + PV of opex) / PV of generation``. Capex enters **undiscounted at
    t0** under every basis: it is a purchase price, not a stream. Generation is
    the modelled series, so it carries both degradation and the ramp year.

    Rounded to a whole EUR/MWh, as the reference rounds it and as §7.4 displays
    it. Returns NaN for a project that never generates, which is undefined
    rather than free.
    """
    chosen = basis if basis is not None else assumptions.interpretation.lcoe_opex_basis
    if chosen not in LCOE_BASES:
        raise ValueError(f"unknown lcoe_opex_basis {chosen!r}; expected one of {LCOE_BASES}")

    rate = 1 + assumptions.lcoe_real_discount_rate
    index = np.arange(YEARS)
    age = index + inputs.base_year - inputs.cod_year
    operating = age >= 0

    from_cod = chosen == "real_from_cod"
    periods = np.where(from_cod, age, index) + 1
    discount = rate ** periods.astype(_F64)

    annual_opex = inputs.capacity_mw * MWH_PER_GWH * inputs.opex_per_kw_year / EUR_PER_EUR_MILLION
    opex = np.full(YEARS, annual_opex, dtype=_F64)
    if chosen == "nominal_from_base_year":
        opex = opex * (1 + inputs.opex_escalation) ** np.maximum(age, 0).astype(_F64)

    present_cost = inputs.total_capex + float(np.sum(opex[operating] / discount[operating]))
    generation = statements.generation_gwh * MWH_PER_GWH
    present_generation = float(np.sum(generation[operating] / discount[operating]))
    if present_generation <= 0:
        return float("nan")
    return js_round(present_cost * EUR_PER_EUR_MILLION / present_generation)


def terminal_value(ebitda_at_exit: float, multiple: float, debt_outstanding: float) -> float:
    """Exit multiple x exit-year EBITDA, less debt still outstanding (§9.4).

    Floored at zero: an exit that does not clear the debt returns nothing to
    equity rather than a negative sum, because the equity holder walks away.
    """
    return max(0.0, ebitda_at_exit * multiple - debt_outstanding)


def hold_truncated_fcfe(
    statements: ProjectStatements,
    *,
    hold_years: int,
    multiple: float,
    exit_year_fcfe_included: bool,
) -> npt.NDArray[np.float64]:
    """The equity cash flow to the exit year, **with** the terminal value.

    This is the series that drives IRR and MOIC, and it is deliberately named
    apart from the 30-year series a file carries, which has no terminal value
    (A-6). Conflating the two is the single most likely silent bug in the
    feature, so they never share a name and neither is derived from the other by
    slicing.

    The debt netted off is the balance **after** the exit year's repayment, so
    the exit-year cash flow and the exit bridge do not both take credit for it.
    It is read from the statements' own ``debtSchedule.closing``, which §7.4
    ties out. The JavaScript reference instead re-amortises the facility in a
    *third* independent loop for this one purpose, and its answer sits an ulp
    away from the schedule in the same file -- which is why 35 of 1,440 oracle
    cells differ in their last bit. Reproducing that would mean carrying a
    fourth derivation of a number the file already states, which is precisely
    the "two sources for one number" §9 exists to prevent. Recorded in
    ``docs/decisions.md``.
    """
    at = exit_index(hold_years)
    series = statements.fcfe[: at + 1].astype(_F64).copy()
    value = terminal_value(
        float(statements.ebitda[at]), multiple, float(statements.debt_closing[at])
    )
    series[at] = (series[at] if exit_year_fcfe_included else 0.0) + value
    return series


def irr(cashflow: npt.NDArray[np.float64], assumptions: AssumptionSet) -> float:
    """IRR by bisection, or **NaN** where the series has no sign change (§9.4).

    NaN, never zero and never a sentinel: §13 requires an undefined IRR to
    render as an em dash, and epic §5 requires it to be excluded from every
    weighted average. Coercing it to zero -- which the JavaScript reference does
    via ``|| 0`` (1C-6) -- silently drags a portfolio average down.

    Every element is discounted, including the first: ``sum(cf[i] / (1+r)^(i+1))``.
    That scales the NPV by a constant and leaves the root where it is, but it is
    what the reference computes, so it is what the bracket test is evaluated on.
    """
    bracket = assumptions.irr

    def npv(rate: float) -> float:
        # Summed in order, one term at a time, rather than with `np.sum`.
        # Float addition is not associative and numpy sums pairwise above a
        # block size, so the two disagree in the last bits -- which is enough to
        # send a bisection step the other way and move the root by an ulp.
        # The reference reduces left to right, so this does too.
        total = 0.0
        for period, amount in enumerate(cashflow, start=1):
            total += float(amount) / (1 + rate) ** period
        return total

    low, high = bracket.low, bracket.high
    if npv(low) * npv(high) > 0:
        return float("nan")
    for _ in range(bracket.iterations):
        middle = (low + high) / (1 + 1)
        if npv(low) * npv(middle) <= 0:
            high = middle
        else:
            low = middle
    return (low + high) / (1 + 1)


def moic(cashflow: npt.NDArray[np.float64]) -> float:
    """Distributions over contributions, undiscounted, on the truncated series.

    Returns NaN where nothing was contributed, which is undefined rather than
    infinite.
    """
    inflow = float(np.sum(np.maximum(0.0, cashflow)))
    outflow = -float(np.sum(np.minimum(0.0, cashflow)))
    if outflow <= 0:
        return float("nan")
    return inflow / outflow
