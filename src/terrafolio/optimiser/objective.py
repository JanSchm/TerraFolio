"""§10.2's nine-term objective, with the two overrides that have to come first.

**Check order is load-bearing** (epic §6.2):

1. **Empty portfolio → exactly the configured ``empty_portfolio_score``.** An empty
   portfolio has equity zero and is therefore technically *feasible*, so a
   budget-first check would score it on its merits and a -50 floor would never fire.
2. **Equity over the cap → the graded reject.** ``reject_base + reject_slope x
   (equity / capital - 1)``, which for the shipped set is
   ``-1000 - 100 x overshoot``. §10.2 says "rejected before scoring", and implemented
   literally as one constant every over-budget chromosome becomes indistinguishable
   and selection has nothing to work with. The mockup's graded ``-20 - equity/capital``
   grades, but sits **above** the worst feasible score of about -44, so an infeasible
   portfolio could outrank a feasible one. This grade is monotone in the overshoot and
   always below the empty-portfolio floor.
3. Otherwise the nine terms, accumulated in the order ``objective_cases.json`` records.

The cap carries a **€1 tolerance**, which is not defensiveness: the capital-utilisation
reward actively pushes good portfolios onto the boundary, so a portfolio landing
exactly on it must not be rejected by a rounding artefact.

**Floors and clamps apply to the normalised term, before the weight multiplies.** The
capacity term is ``max(floor, 1 - |mw - target| / target)``, so it bottoms out at
``3.2 x -0.6 = -1.92`` rather than at ``-0.6``. Every weight, floor, clamp, scale and
cap comes from the assumption set; there is not a calibration number in this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.domain.scalars import MandateScalars
from terrafolio.optimiser.aggregate import Aggregates
from terrafolio.pipeline.arrays import Vector

__all__ = ["TERM_ORDER", "Terms", "quantise", "score", "score_terms"]

TERM_ORDER: Final[tuple[str, ...]] = (
    "capacity",
    "techMix",
    "returns",
    "utilisation",
    "leveragePenalty",
    "merchantPenalty",
    "riskPenalty",
    "countryConcentrationPenalty",
    "projectConcentrationPenalty",
)
"""``objective_cases.json``'s ``termAccumulationOrder``.

Floating-point addition is not associative, so the order the nine contributions are
summed in is part of the answer, not a presentation choice. Reproduced literally.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class Terms:
    """Each term's contribution, for one or many selections.

    Kept as a record rather than summed on the spot because §7.1 shows a portfolio's
    compliance term by term, and because a fitness that is wrong is far easier to
    diagnose from nine numbers than from one.
    """

    contributions: dict[str, Vector]
    total: Vector

    def __getitem__(self, name: str) -> Vector:
        return self.contributions[name]


def quantise(fitness: Vector, assumptions: AssumptionSet) -> Vector:
    """Round to the configured precision **before any comparison**.

    The genetic algorithm's whole trajectory turns on ``f[a] >= f[b]``. One last-bit
    difference — a BLAS with a different reduction order, a different thread count —
    flips a tournament and diverges the run from there on. Rounding first makes the
    comparison a property of the numbers rather than of the machine.
    """
    rounded: Vector = np.round(fitness, assumptions.objective.quantisation_dp)
    return rounded


def _excess(value: Vector, cap: float) -> Vector:
    return np.maximum(value - cap, 0.0)


def _shortfall(floor: float, value: Vector) -> Vector:
    return np.maximum(floor - value, 0.0)


def score_terms(totals: Aggregates, mandate: MandateScalars, assumptions: AssumptionSet) -> Terms:
    """The nine §10.2 terms, before the empty and over-budget overrides.

    Returned for every selection including the ones an override will replace: the
    override is applied by :func:`score`, and computing the terms unconditionally
    keeps this function branch-free and the same shape for every row.
    """
    weights = assumptions.objective

    capacity_deviation = np.abs(totals.capacity_mw - mandate.capacity_target_mw) / (
        mandate.capacity_target_mw
    )
    capacity = np.maximum(1.0 - capacity_deviation, weights.capacity_score_floor)

    split_deviation = np.abs(totals.solar_share - mandate.solar_share) / (
        weights.tech_split_tolerance
    )
    tech_split = np.maximum(1.0 - split_deviation, weights.tech_split_score_floor)

    # An undefined blend falls back to the hurdle, so the term contributes exactly
    # nothing (§13). Substituting a return of zero would instead charge the portfolio
    # the full negative rail for a number nobody has.
    blended = np.where(np.isnan(totals.blended_irr), mandate.target_irr, totals.blended_irr)
    returns = np.clip(
        (blended - mandate.target_irr) / weights.return_scale,
        -weights.return_clamp,
        weights.return_clamp,
    )

    utilisation = totals.equity / mandate.available_capital_eur
    risk_cap = assumptions.risk_caps.portfolio[mandate.risk_appetite]

    contributions = {
        "capacity": weights.capacity_weight * capacity,
        "techMix": weights.tech_split_weight * tech_split,
        "returns": weights.return_weight * returns,
        "utilisation": weights.utilisation_weight * utilisation,
        "leveragePenalty": weights.leverage_weight
        * _shortfall(mandate.min_leverage, totals.gearing),
        "merchantPenalty": weights.merchant_weight
        * _excess(totals.merchant_share, mandate.max_merchant_share),
        "riskPenalty": weights.risk_weight * _excess(totals.weighted_risk, risk_cap),
        "countryConcentrationPenalty": weights.country_concentration_weight
        * np.maximum(totals.country_shares - mandate.max_country_share, 0.0).sum(axis=-1),
        "projectConcentrationPenalty": weights.project_concentration_weight
        * np.maximum(totals.project_shares - mandate.max_project_share, 0.0).sum(axis=-1),
    }

    total = np.zeros_like(totals.equity)
    for name in TERM_ORDER:
        total = total + contributions[name]
    return Terms(contributions=contributions, total=total)


def score(totals: Aggregates, mandate: MandateScalars, assumptions: AssumptionSet) -> Vector:
    """``(m,)`` §10.2 fitness with the two overrides applied, **unrounded**.

    This is the value the specification defines and the one 1C's objective cases
    record, so it is what the golden test compares at 1e-12. Quantisation is a
    property of the *comparison*, not of the objective — :func:`quantise` is applied
    by the genetic algorithm before any selection and by the result layer before any
    number is reported, and applying it here as well would make the oracle
    unreachable by six decimal places.
    """
    weights = assumptions.objective
    capital = mandate.available_capital_eur

    overshoot = totals.equity / capital - 1.0
    rejected = totals.equity > capital + weights.equity_cap_tolerance_eur
    graded = weights.reject_base + weights.reject_slope * overshoot

    fitness = np.where(rejected, graded, score_terms(totals, mandate, assumptions).total)
    with_overrides: Vector = np.where(totals.is_empty, weights.empty_portfolio_score, fitness)
    return with_overrides
