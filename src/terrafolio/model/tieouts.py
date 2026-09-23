"""The blocking tie-outs of ``docs/pipeline-schema.md`` §7.

§7 is unambiguous about these: *"**Blocking.** A file that fails any of these does
not load, and the loader reports the check, the year and the residual."* They are
not advisory, and they are not the same thing as the house model's variance
report -- that says the model would have computed something else, which an
analyst is entitled to overrule; these say the file **disagrees with itself**,
which nobody is entitled to overrule because there is no coherent reading of it.

Every identity is linear, which is why §2's sign convention matters and why the
schema rejects a negative magnitude line: a negative ``opex`` satisfies
``ebitda = revenue - opex`` while inflating EBITDA.

**Ownership.** Issue 2A owns ``pipeline/`` and the loader, and this is not that
loader -- it is the arithmetic the loader has to apply, put where the generator
and the spreadsheet path can both reach it so that neither writes a file it has
not checked. ``pipeline`` ranks *below* ``model`` in
``tests/unit/test_import_boundaries.py``'s ``LAYERS``, so 2A cannot import this
as things stand; re-ranking is the one-line change that guard's own docstring
sanctions, and is preferable to a second copy of these identities. Flagged on
issue #1.

Numeric core: numpy, the standard library and ``config`` only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

from terrafolio.domain.conventions import EUR_PER_EUR_MILLION, MWH_PER_GWH, YEARS
from terrafolio.model.project import ProjectInputs, ProjectStatements

__all__ = ["TieOutFailure", "check_tie_outs"]

_F64: Final = np.float64


@dataclass(frozen=True, slots=True, kw_only=True)
class TieOutFailure:
    """One identity a file does not satisfy, with the year and the residual.

    §7 asks for exactly these three things to be reported, because "this file is
    invalid" is not actionable and "``ebitda`` is out by EUR 5.00m in 2032" is.
    """

    check: str
    year: int | None
    """Index from the base year, or ``None`` for a check over the whole series."""
    residual: float
    tolerance: float

    def __str__(self) -> str:
        where = "overall" if self.year is None else f"year index {self.year}"
        return (
            f"{self.check}: {where}, residual {self.residual:.6f} "
            f"against a tolerance of {self.tolerance:.6f}"
        )


def _tolerance(reference: float, absolute: float, relative: float) -> float:
    """§7's tolerance: EUR 0.01m absolute or 0.1% relative, whichever is looser."""
    return max(absolute, relative * abs(reference))


def check_tie_outs(
    inputs: ProjectInputs,
    statements: ProjectStatements,
    *,
    tolerance_abs: float,
    tolerance_rel: float,
) -> tuple[TieOutFailure, ...]:
    """Every §7 identity, in file order. Empty means the file is internally coherent.

    Returns all failures rather than the first: a file with three broken blocks
    should take one edit to fix, not three rounds of ingest.
    """
    found: list[TieOutFailure] = []

    def series(
        name: str, actual: npt.NDArray[np.float64], expected: npt.NDArray[np.float64]
    ) -> None:
        residual = np.abs(actual - expected)
        allowed = np.maximum(tolerance_abs, tolerance_rel * np.abs(actual))
        breach = residual - allowed
        worst = int(np.argmax(breach))
        if breach[worst] > 0:
            found.append(
                TieOutFailure(
                    check=name,
                    year=worst,
                    residual=float(residual[worst]),
                    tolerance=float(allowed[worst]),
                )
            )

    def scalar(name: str, actual: float, expected: float) -> None:
        allowed = _tolerance(expected, tolerance_abs, tolerance_rel)
        if abs(actual - expected) > allowed:
            found.append(
                TieOutFailure(
                    check=name, year=None, residual=abs(actual - expected), tolerance=allowed
                )
            )

    s = statements
    equity = inputs.total_capex - inputs.senior_debt

    # §7.1 income statement
    series("ebitda = revenue - opex", s.ebitda, s.revenue - s.opex)
    series("ebit = ebitda - depreciation", s.ebit, s.ebitda - s.depreciation)
    series("pbt = ebit - interestExpense", s.pbt, s.ebit - s.interest_expense)
    series("netIncome = pbt - taxExpense", s.net_income, s.pbt - s.tax_expense)

    # §7.2 revenue ties to the physicals, with A-1's explicit division
    series(
        "revenue = generationGwh x 1000 x achievedPrice / 1e6",
        s.revenue,
        s.generation_gwh * MWH_PER_GWH * s.achieved_price / EUR_PER_EUR_MILLION,
    )

    # §7.3 cash flow, in the form §7 publishes
    series(
        "fcfe = ebitda - interestPaid - debtRepayment - taxPaid - capex + debtDrawdown",
        s.fcfe,
        s.ebitda
        - s.interest_expense
        - s.debt_repayment
        - s.tax_expense
        - s.capex
        + s.debt_drawdown,
    )

    # §7.4 debt schedule
    series(
        "closing = opening - repayment + drawdown",
        s.debt_closing,
        s.debt_opening - s.debt_repayment + s.debt_drawdown,
    )
    series("opening[t] = closing[t-1]", s.debt_opening[1:], s.debt_closing[:-1])
    scalar("opening[0] = 0", float(s.debt_opening[0]), 0.0)
    scalar("closing[last] = 0 (fully amortised)", float(s.debt_closing[-1]), 0.0)

    # §7.5 funding
    scalar("sum debtDrawdown = seniorDebt", float(s.debt_drawdown.sum()), inputs.senior_debt)
    scalar("sum equityDrawdown = totalCapex - seniorDebt", float(s.equity_drawdown.sum()), equity)
    scalar("sum capex = totalCapex", float(s.capex.sum()), inputs.total_capex)
    series("capex = debtDrawdown + equityDrawdown", s.capex, s.debt_drawdown + s.equity_drawdown)

    # §7.6 depreciation and PP&E, in A-3's residual form
    previous = np.concatenate((np.zeros(1, dtype=_F64), s.ppe[:-1]))
    series(
        "ppe[t] = ppe[t-1] - depreciation[t] + capex[t]", s.ppe, previous - s.depreciation + s.capex
    )
    scalar(
        "sum depreciation = totalCapex - ppe[last]",
        float(s.depreciation.sum()),
        inputs.total_capex - float(s.ppe[-1]),
    )

    # §7.7 DSCR, where it is reported
    live = np.isfinite(s.dscr)
    if np.any(live):
        service = (s.interest_expense + s.debt_repayment)[live]
        series(
            "dscr = ebitda / (interestPaid + debtRepayment)", s.dscr[live] * service, s.ebitda[live]
        )

    # §7.8 shape: the placement of dscr, which is blocking in its own right
    age = np.arange(YEARS) + inputs.base_year - inputs.cod_year
    inside = (age >= 0) & (age < inputs.debt_tenor_years)
    misplaced = np.nonzero(live != inside)[0]
    if misplaced.size:
        found.append(
            TieOutFailure(
                check="dscr is present exactly inside the debt life",
                year=int(misplaced[0]),
                residual=float(misplaced.size),
                tolerance=0.0,
            )
        )
    return tuple(found)
