"""The mandate-dependent returns: the hold-truncated series, IRR, MOIC and payback.

**The two cash-flow series are different series and never share a name.** This is the
single most likely silent bug in the feature, so it is worth stating twice:

===========================  ======  ==============  ===========================
Series                       Length  Terminal value  Drives
===========================  ======  ==============  ===========================
``fcfe`` (in ``ProjectArrays``)  30   **no**         the chart, the CSV, the
                                                     30-year FCFE tile
``hold_truncated_fcfe``      ``hold`` **yes**        equity IRR and MOIC, nothing
                                                     else
===========================  ======  ==============  ===========================

Neither is ever derived from the other by slicing: the truncated one is built here,
with the terminal value folded into its final element, and the 30-year one is the
file's own and is never touched (decision A-6, ``docs/api.md`` §1.5).

Nothing in this module may be cached without ``hold_years`` in the key. That is what
keeps §5.1's hold-period slider a live control rather than decoration.
"""

from __future__ import annotations

import numpy as np

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.domain.conventions import MWH_PER_GWH, YEARS, exit_index
from terrafolio.economics.irr import irr
from terrafolio.economics.terminal import terminal_value
from terrafolio.pipeline.arrays import BoolVector, IntVector, Matrix, ProjectArrays, Vector

__all__ = [
    "ProjectReturns",
    "contracted_revenue_share",
    "hold_truncated_fcfe",
    "moic",
    "payback_period",
    "project_returns",
    "thirty_year_fcfe",
]


def thirty_year_fcfe(arrays: ProjectArrays) -> Vector:
    """``(n,)`` undiscounted sum of the **30-year** series — no terminal value.

    This is §7.1's *30-year FCFE* tile and the ``cashflow.csv`` column. If it ever
    equals the sum of the truncated series, something has gone wrong.
    """
    total: Vector = arrays.statements.cash_flow.fcfe.sum(axis=-1)
    return total


def hold_truncated_fcfe(
    arrays: ProjectArrays, assumptions: AssumptionSet, hold_years: int
) -> Matrix:
    """``(n, hold)`` equity cash flow to the exit year, **with** the terminal value.

    Whether the exit year's own FCFE counts alongside the exit proceeds is the one
    thing §9.4 leaves open, so it is a flag —
    ``assumptions.interpretation.exit_year_fcfe_included`` — rather than a choice
    buried here. The shipped set says it does, which is what the reference does.
    """
    index = exit_index(hold_years)
    series = arrays.statements.cash_flow.fcfe[:, : index + 1].copy()
    exit_proceeds = terminal_value(arrays, assumptions, hold_years)
    if assumptions.interpretation.exit_year_fcfe_included:
        series[:, index] += exit_proceeds
    else:
        series[:, index] = exit_proceeds
    return series


def moic(series: Matrix) -> Vector:
    """``(n,)`` distributions over contributions, across the truncated series.

    ``NaN`` where nothing was ever contributed — dividing by zero would report an
    infinite multiple on a project that took no money, and the undefined-means-undefined
    rule applies here exactly as it does to IRR.
    """
    contributions = np.maximum(-series, 0.0).sum(axis=-1)
    distributions = np.maximum(series, 0.0).sum(axis=-1)
    return np.where(
        contributions > 0.0,
        distributions / np.where(contributions > 0.0, contributions, 1.0),
        np.nan,
    )


def payback_period(series: Matrix) -> Vector:
    """``(n,)`` 1-based **period** in which cumulative equity cash flow first turns
    non-negative, or ``NaN`` if it never does inside the hold.

    A period, not a calendar year. Period 1 is the pipeline's base year, and the caller
    that knows the base year converts — ``docs/api.md`` §2 gives ``paybackYear`` as a
    calendar year and ``ProjectScalars`` bounds it to 2000-2100, so a small integer
    under that name was an invitation to store one. The name now says which it is.

    ``NaN`` rather than the hold length: "not paid back by year ten" and "paid back in
    year ten" are different answers, and only one of them is good news.
    """
    cumulative = np.cumsum(series, axis=-1)
    repaid: BoolVector = cumulative >= 0.0
    ever = np.asarray(repaid.any(axis=-1))
    first: IntVector = repaid.argmax(axis=-1)
    return np.where(ever, first.astype(np.float64) + 1.0, np.nan)


def contracted_revenue_share(arrays: ProjectArrays) -> Vector:
    """``(n,)`` share of lifetime revenue under contract, **revenue-weighted**.

    Not ``ppaShare``. That is a share of *volume* over the PPA tenor; this is a share
    of *revenue* over thirty years, and the two differ a lot — a ten-year PPA on a
    thirty-year asset leaves two thirds of the life fully merchant whatever the volume
    share was.

    Reconstructed from the file's declared assumptions, which is what C-2 put the
    escalators there for: contracted revenue in an operating year is
    ``generation x ppaShare x ppaPrice x (1 + priceEscalation)^age`` while the age is
    inside the tenor, and nothing after it.

    **This is not what feeds the objective.** §7.1's merchant-exposure tile and §10.2's
    merchant penalty are both *capex*-weighted on ``ppaShare``, which is what the spec,
    ``docs/api.md`` and 1C's objective cases all pin. This figure is the per-project
    detail, and N-1 on the epic is the open question about whether the two should be
    reconciled by storing the revenue split in the file.
    """
    revenue = arrays.statements.income.revenue
    generation = arrays.statements.physicals.generation_gwh
    age = arrays.statements.years - arrays.asset.cod_year[:, None]

    escalation = (1.0 + arrays.assumptions.price_escalation[:, None]) ** np.maximum(age, 0)
    price = arrays.revenue.ppa_price[:, None] * escalation
    under_contract = (age >= 0) & (age < arrays.revenue.ppa_tenor_years[:, None])

    contracted = np.where(
        under_contract,
        generation * MWH_PER_GWH * arrays.revenue.ppa_share[:, None] * price,
        0.0,
    )
    lifetime = revenue.sum(axis=-1)
    return np.where(
        lifetime > 0.0, contracted.sum(axis=-1) / np.where(lifetime > 0.0, lifetime, 1.0), 0.0
    )


class ProjectReturns:
    """Every mandate-dependent per-project figure, computed once for one hold period.

    Bundled rather than returned piecemeal because they share the truncated series, and
    because carrying ``hold_years`` alongside them is what stops any of it being cached
    under a key that does not mention it.
    """

    __slots__ = ("defined", "equity_irr", "hold_years", "moic", "payback", "series", "terminal")

    def __init__(self, arrays: ProjectArrays, assumptions: AssumptionSet, hold_years: int) -> None:
        self.hold_years = hold_years
        self.series = hold_truncated_fcfe(arrays, assumptions, hold_years)
        self.terminal = terminal_value(arrays, assumptions, hold_years)
        self.equity_irr, self.defined = irr(self.series)
        self.moic = moic(self.series)
        self.payback = payback_period(self.series)


def project_returns(
    arrays: ProjectArrays, assumptions: AssumptionSet, hold_years: int
) -> ProjectReturns:
    """Compute the hold-dependent returns for every project in ``arrays``."""
    if not 1 <= hold_years <= YEARS:
        raise ValueError(f"hold_years must be between 1 and {YEARS}, got {hold_years}")
    return ProjectReturns(arrays, assumptions, hold_years)
