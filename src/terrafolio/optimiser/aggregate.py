"""One portfolio aggregation, used by both the objective and the result tiles.

§10.2 needs capacity, solar share, merchant exposure and country concentration for a
whole population; §7.1 needs the same figures for the winner. Computing them twice is
how two implementations of the same arithmetic come to disagree in the last few bits
and then, later, in substance — so there is one, vectorised over ``(m, n)`` selections,
and a population of one is how the winner is aggregated.

**Precision.** The reduction runs at whatever dtype the selection carries — float32 in
the genetic algorithm's hot path — and everything downstream of it is float64. That is
the epic's rule stated as code: the speed-up is in the matrix product, and precision
belongs in the outputs.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from terrafolio.optimiser.features import COLUMN, Features
from terrafolio.pipeline.arrays import Matrix, Vector

__all__ = ["Aggregates", "aggregate"]


def _ratio(numerator: Vector, denominator: Vector) -> Vector:
    """Divide where the denominator is positive, and give ``0`` elsewhere.

    An empty portfolio has a zero denominator for every weighted average here, and
    ``filterwarnings = ["error"]`` turns a divide-by-zero warning into a test failure.
    The empty case is scored by an override before any of these values is read, so the
    zero is a placeholder rather than an answer.
    """
    usable = denominator > 0.0
    return np.where(usable, numerator / np.where(usable, denominator, 1.0), 0.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class Aggregates:
    """Portfolio totals for ``m`` selections, all in **euros**, all float64."""

    project_count: Vector
    capacity_mw: Vector
    total_capex: Vector
    senior_debt: Vector
    equity: Vector
    solar_capacity_mw: Vector

    solar_share: Vector
    """Capacity-weighted, per §5.1: offshore wind counts as wind."""

    gearing: Vector
    merchant_share: Vector
    weighted_risk: Vector

    blended_irr: Vector
    """Equity-weighted over projects with a **defined** IRR, ``NaN`` when none has one.

    ``NaN`` rather than zero, because zero is a rate and "we cannot say" is not
    (§13). The objective substitutes the hurdle so the return term contributes
    exactly nothing; it does not substitute a return of nought.
    """

    country_shares: Matrix
    """``(m, k)`` capex share by country, in the order of ``Features.country_codes``."""

    project_shares: Matrix
    """``(m, n)`` capex share by project — zero for anything not selected.

    The one genuinely ``O(m x n)`` term in the objective. The epic measured it at
    0.078 ms and concluded no cleverness was needed; this is that conclusion.
    """

    @property
    def selection_count(self) -> int:
        return int(self.project_count.shape[0])

    @property
    def is_empty(self) -> Vector:
        """Selections holding nothing. Checked before anything else is scored."""
        empty: Vector = self.project_count == 0
        return empty


def aggregate(features: Features, selection: Matrix) -> Aggregates:
    """Reduce ``(m, n)`` selections to their portfolio totals.

    ``selection`` is float — 1.0 for held, 0.0 for not — rather than boolean, because
    the whole point is that one matrix product replaces a loop.
    """
    totals = np.asarray(selection @ features.matrix_for(selection.dtype), dtype=np.float64)
    fit_width = features.fit.shape[-1]
    fit, country = totals[:, :fit_width], totals[:, fit_width:]

    capex = fit[:, COLUMN["capex"]]
    capacity = fit[:, COLUMN["capacity_mw"]]
    solar = fit[:, COLUMN["solar_mw"]]
    equity_with_irr = fit[:, COLUMN["equity_with_defined_irr"]]

    weights = np.asarray(selection, dtype=np.float64)
    return Aggregates(
        project_count=weights.sum(axis=-1),
        capacity_mw=capacity,
        total_capex=capex,
        senior_debt=fit[:, COLUMN["senior_debt"]],
        equity=fit[:, COLUMN["equity"]],
        solar_capacity_mw=solar,
        solar_share=_ratio(solar, capacity),
        gearing=_ratio(fit[:, COLUMN["senior_debt"]], capex),
        merchant_share=_ratio(fit[:, COLUMN["capex_weighted_merchant"]], capex),
        weighted_risk=_ratio(fit[:, COLUMN["capex_weighted_risk"]], capex),
        blended_irr=np.where(
            equity_with_irr > 0.0,
            _ratio(fit[:, COLUMN["equity_weighted_irr"]], equity_with_irr),
            np.nan,
        ),
        country_shares=country / np.where(capex > 0.0, capex, 1.0)[:, None],
        project_shares=(weights * features.capex) / np.where(capex > 0.0, capex, 1.0)[:, None],
    )
