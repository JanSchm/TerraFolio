""" "Our model says X, your file says Y" -- and then lets the file stand.

Each project file is authored by the analyst who follows that project, and the
file is the source of truth (epic §2). The house model's second job is to
**check** one: re-derive its statements from its own declared assumptions and
report where the two disagree, line by line and year by year.

**It reports. It never gates.** Q-4 and A-7 settle that: gating ingestion on the
house model would make the house model authoritative again, which is precisely
what the input design set out to change. An analyst who has modelled a
curtailment regime, a tax-loss carryforward or a merchant floor that this model
does not know about should see a variance and keep their numbers.

That is also why the report is per line item rather than a single score. A file
that diverges only on ``taxExpense`` is telling you something specific -- this
model has no loss carryforward (1C-3) -- and a number for the whole file would
bury it.

Numeric core: numpy, the standard library and ``config`` only. It takes arrays
and scalars, never a :class:`~terrafolio.domain.project_file.ProjectFile`; the
adapter that reads one lives in :mod:`terrafolio.generate.from_file`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

from terrafolio.model.project import ProjectInputs, ProjectStatements, project_statements

__all__ = ["LineVariance", "VarianceReport", "compare_to_house_model"]

COMPARED_LINES: Final[tuple[str, ...]] = (
    "generation_gwh",
    "achieved_price",
    "revenue",
    "opex",
    "ebitda",
    "depreciation",
    "ebit",
    "interest_expense",
    "pbt",
    "tax_expense",
    "net_income",
    "capex",
    "debt_drawdown",
    "equity_drawdown",
    "fcfe",
    "debt_opening",
    "debt_repayment",
    "debt_closing",
    "ppe",
)
"""Every statement line but ``dscr``, which carries nulls and is a ratio.

``dscr`` is excluded because it is a quotient of two lines already compared: a
variance in it is a variance in EBITDA or in debt service, reported there, and
reporting it twice would suggest two problems where there is one.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class LineVariance:
    """How far one statement line is from what the house model computes."""

    line: str
    worst_year: int
    """Index from the base year, so it can be read straight off the file's arrays."""
    declared: float
    modelled: float
    absolute: float
    relative: float
    diverges: bool
    """True when the worst year is outside the tie-out tolerance."""

    def __str__(self) -> str:
        verdict = "diverges" if self.diverges else "agrees"
        return (
            f"{self.line}: {verdict}; worst at year {self.worst_year}, "
            f"file {self.declared:.6f} vs model {self.modelled:.6f} "
            f"({self.absolute:.6f}, {self.relative:.2%})"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class VarianceReport:
    """Every compared line, worst first. Advisory only."""

    lines: tuple[LineVariance, ...]

    @property
    def diverging(self) -> tuple[LineVariance, ...]:
        """The lines outside tolerance, worst relative difference first."""
        return tuple(line for line in self.lines if line.diverges)

    @property
    def agrees(self) -> bool:
        """True when the file reproduces under the house model's own assumptions."""
        return not self.diverging

    def __str__(self) -> str:
        if self.agrees:
            return "the house model reproduces this file on every line"
        return "\n".join(str(line) for line in self.diverging)


def _worst(
    declared: npt.NDArray[np.float64],
    modelled: npt.NDArray[np.float64],
    *,
    tolerance_abs: float,
    tolerance_rel: float,
) -> tuple[int, float, float, bool]:
    """The year that disagrees most, judged the way a tie-out is judged.

    "Most" is by *breach* over the tolerance rather than by raw residual, so a
    line is not ranked by the size of its numbers. A EUR 0.02m miss on a EUR
    2,000m capex line is inside the relative limb and matters less than a EUR
    0.02m miss on a EUR 1m one, and ranking by residual alone would put them the
    other way round.
    """
    residual = np.abs(declared - modelled)
    allowed = np.maximum(tolerance_abs, tolerance_rel * np.abs(declared))
    breach = residual - allowed
    year = int(np.argmax(breach))
    scale = abs(float(declared[year]))
    relative = float(residual[year] / scale) if scale else 0.0
    return year, float(residual[year]), relative, bool(breach[year] > 0)


def compare_to_house_model(
    inputs: ProjectInputs,
    declared: ProjectStatements,
    *,
    tolerance_abs: float,
    tolerance_rel: float,
) -> VarianceReport:
    """Run the house model on ``inputs`` and compare it to ``declared``.

    ``inputs`` comes from the file's own declared fields, so this is not "our
    assumptions against yours" -- it is the same assumptions run through a
    different model. A divergence is therefore a modelling difference, which is
    the thing worth reporting.

    Lines are returned worst-relative-difference first, so a caller that prints
    the first few prints the ones worth looking at.
    """
    modelled = project_statements(inputs)
    found: list[LineVariance] = []
    for line in COMPARED_LINES:
        year, absolute, relative, diverges = _worst(
            getattr(declared, line),
            getattr(modelled, line),
            tolerance_abs=tolerance_abs,
            tolerance_rel=tolerance_rel,
        )
        found.append(
            LineVariance(
                line=line,
                worst_year=year,
                declared=float(getattr(declared, line)[year]),
                modelled=float(getattr(modelled, line)[year]),
                absolute=absolute,
                relative=relative,
                diverges=diverges,
            )
        )
    found.sort(key=lambda item: (not item.diverges, -item.relative))
    return VarianceReport(lines=tuple(found))
