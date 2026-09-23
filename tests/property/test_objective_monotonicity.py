"""Properties of §10.2 that must hold for every portfolio, not just the 206 recorded.

The golden cases prove the objective reproduces the reference on the chromosomes 1C
happened to generate. These prove the two things a *tuned* objective has to satisfy
whatever the inputs: that breaching a constraint never pays, and that breaching it
further never pays more. §10.2 states the first as a design goal — "weights are tuned
so that a constraint breach always costs more than the reward available from breaching
it" — and nothing else in the suite checks it.
"""

from __future__ import annotations

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from terrafolio.config.loader import load_default
from terrafolio.domain.enums import RiskAppetite, Stage
from terrafolio.domain.scalars import MandateScalars
from terrafolio.optimiser.aggregate import Aggregates
from terrafolio.optimiser.objective import score, score_terms

ASSUMPTIONS = load_default()

# No deadline. These assert arithmetic properties of the objective, not how fast it
# runs, and hypothesis's default 200 ms wall clock measures the machine instead: on a
# loaded box the same example took 336 ms once and 42 ms on retry, failing the suite
# for a reason that has nothing to do with the code. Timing belongs in tests/perf.
PENALTIES = (
    "leveragePenalty",
    "merchantPenalty",
    "riskPenalty",
    "countryConcentrationPenalty",
    "projectConcentrationPenalty",
)

MANDATE = MandateScalars(
    available_capital_eur=1_200e6,
    capacity_target_mw=1_500.0,
    solar_share=0.45,
    target_irr=0.11,
    hold_years=10,
    countries=("ES", "DE"),
    stages=(Stage.GREENFIELD, Stage.READY_TO_BUILD, Stage.CONSTRUCTION),
    min_leverage=0.60,
    min_dscr=1.25,
    max_merchant_share=0.35,
    max_country_share=0.35,
    max_project_share=0.15,
    cod_from=2027,
    cod_to=2032,
    risk_appetite=RiskAppetite.BALANCED,
    grid_secured_only=False,
    eur_revenue_only=False,
    om_contracted_only=False,
)

PORTFOLIO_RISK_CAP = ASSUMPTIONS.risk_caps.portfolio[MANDATE.risk_appetite]


def _totals(  # noqa: PLR0913 - a fixture builder: one keyword per constraint, by design
    *,
    gearing: float = 0.70,
    merchant: float = 0.20,
    risk: float = 2.0,
    country_excess: float = 0.0,
    project_excess: float = 0.0,
    capacity_mw: float = 1_500.0,
    solar_share: float = 0.45,
    equity: float = 600e6,
    blended_irr: float = 0.12,
) -> Aggregates:
    """One feasible portfolio, with each constraint dialled independently.

    The concentration terms take their *excess over the cap* directly, because that is
    the quantity the penalty is monotone in and building a country map that produces a
    given excess would test the map rather than the penalty.
    """
    return Aggregates(
        project_count=np.array([12.0]),
        capacity_mw=np.array([capacity_mw]),
        total_capex=np.array([1_500e6]),
        senior_debt=np.array([1_500e6 * gearing]),
        equity=np.array([equity]),
        solar_capacity_mw=np.array([capacity_mw * solar_share]),
        solar_share=np.array([solar_share]),
        gearing=np.array([gearing]),
        merchant_share=np.array([merchant]),
        weighted_risk=np.array([risk]),
        blended_irr=np.array([blended_irr]),
        country_shares=np.array([[MANDATE.max_country_share + country_excess]]),
        project_shares=np.array([[MANDATE.max_project_share + project_excess]]),
    )


def _term(name: str, totals: Aggregates) -> float:
    return float(score_terms(totals, MANDATE, ASSUMPTIONS)[name][0])


def _fitness(totals: Aggregates) -> float:
    return float(score(totals, MANDATE, ASSUMPTIONS)[0])


breach = st.floats(min_value=0.0, max_value=0.6, allow_nan=False, allow_infinity=False)


# ---------------------------------------------------------------------------
# Each penalty is monotone non-increasing in its own breach
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(smaller=breach, extra=breach)
def test_the_leverage_penalty_never_rises_as_the_shortfall_grows(
    smaller: float, extra: float
) -> None:
    less = _term("leveragePenalty", _totals(gearing=MANDATE.min_leverage - smaller))
    more = _term("leveragePenalty", _totals(gearing=MANDATE.min_leverage - smaller - extra))
    assert more <= less


@settings(max_examples=200, deadline=None)
@given(smaller=breach, extra=breach)
def test_the_merchant_penalty_never_rises_as_the_overshoot_grows(
    smaller: float, extra: float
) -> None:
    less = _term("merchantPenalty", _totals(merchant=MANDATE.max_merchant_share + smaller))
    more = _term("merchantPenalty", _totals(merchant=MANDATE.max_merchant_share + smaller + extra))
    assert more <= less


@settings(max_examples=200, deadline=None)
@given(smaller=breach, extra=breach)
def test_the_risk_penalty_never_rises_as_the_overshoot_grows(smaller: float, extra: float) -> None:
    less = _term("riskPenalty", _totals(risk=PORTFOLIO_RISK_CAP + smaller))
    more = _term("riskPenalty", _totals(risk=PORTFOLIO_RISK_CAP + smaller + extra))
    assert more <= less


@settings(max_examples=200, deadline=None)
@given(smaller=breach, extra=breach)
def test_the_country_penalty_never_rises_as_the_concentration_grows(
    smaller: float, extra: float
) -> None:
    less = _term("countryConcentrationPenalty", _totals(country_excess=smaller))
    more = _term("countryConcentrationPenalty", _totals(country_excess=smaller + extra))
    assert more <= less


@settings(max_examples=200, deadline=None)
@given(smaller=breach, extra=breach)
def test_the_project_penalty_never_rises_as_the_concentration_grows(
    smaller: float, extra: float
) -> None:
    less = _term("projectConcentrationPenalty", _totals(project_excess=smaller))
    more = _term("projectConcentrationPenalty", _totals(project_excess=smaller + extra))
    assert more <= less


@settings(max_examples=100, deadline=None)
@given(inside=st.floats(min_value=0.0, max_value=0.3))
def test_a_penalty_is_exactly_zero_until_the_cap_is_crossed(inside: float) -> None:
    """Not merely small: a constraint that is met costs nothing at all."""
    assert _term("merchantPenalty", _totals(merchant=MANDATE.max_merchant_share - inside)) == 0.0
    assert _term("leveragePenalty", _totals(gearing=MANDATE.min_leverage + inside)) == 0.0
    assert _term("riskPenalty", _totals(risk=PORTFOLIO_RISK_CAP - inside)) == 0.0


# ---------------------------------------------------------------------------
# A breach never improves the score
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(amount=st.floats(min_value=1e-9, max_value=0.5))
def test_breaching_the_merchant_cap_never_improves_fitness(amount: float) -> None:
    """The tuning goal of §10.2, stated as a test.

    Everything else is held constant, so the only thing that moves is the breach —
    which is what "a breach always costs more than the reward available from breaching
    it" has to mean for the weights to be doing their job.
    """
    at_the_cap = _fitness(_totals(merchant=MANDATE.max_merchant_share))
    over = _fitness(_totals(merchant=MANDATE.max_merchant_share + amount))
    assert over <= at_the_cap


@settings(max_examples=200, deadline=None)
@given(amount=st.floats(min_value=1e-9, max_value=0.5))
def test_missing_the_leverage_floor_never_improves_fitness(amount: float) -> None:
    at_the_floor = _fitness(_totals(gearing=MANDATE.min_leverage))
    under = _fitness(_totals(gearing=MANDATE.min_leverage - amount))
    assert under <= at_the_floor


@settings(max_examples=200, deadline=None)
@given(amount=st.floats(min_value=1e-9, max_value=1.5))
def test_breaching_the_risk_cap_never_improves_fitness(amount: float) -> None:
    at_the_cap = _fitness(_totals(risk=PORTFOLIO_RISK_CAP))
    over = _fitness(_totals(risk=PORTFOLIO_RISK_CAP + amount))
    assert over <= at_the_cap


@settings(max_examples=200, deadline=None)
@given(amount=st.floats(min_value=1e-9, max_value=0.5))
def test_concentrating_further_never_improves_fitness(amount: float) -> None:
    at_the_cap = _fitness(_totals(country_excess=0.0, project_excess=0.0))
    over = _fitness(_totals(country_excess=amount, project_excess=amount))
    assert over <= at_the_cap


# ---------------------------------------------------------------------------
# The two overrides, over the whole space
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(
    equity=st.floats(min_value=0.0, max_value=4_000e6),
    gearing=st.floats(min_value=0.0, max_value=1.0),
    merchant=st.floats(min_value=0.0, max_value=1.0),
    risk=st.floats(min_value=1.0, max_value=5.0),
)
def test_a_rejected_portfolio_always_scores_below_the_empty_one(
    equity: float, gearing: float, merchant: float, risk: float
) -> None:
    """Epic §6.2's requirement, over the whole space rather than 21 recorded cases."""
    totals = _totals(equity=equity, gearing=gearing, merchant=merchant, risk=risk)
    fitness = _fitness(totals)
    rejected = equity > MANDATE.available_capital_eur + (
        ASSUMPTIONS.objective.equity_cap_tolerance_eur
    )
    if rejected:
        assert fitness < ASSUMPTIONS.objective.empty_portfolio_score


@settings(max_examples=200, deadline=None)
@given(overshoot=st.floats(min_value=0.0, max_value=20.0))
def test_the_reject_score_grades_monotonically_in_the_overshoot(overshoot: float) -> None:
    """Graded, so selection has something to work with among infeasible chromosomes."""
    capital = MANDATE.available_capital_eur
    nearer = _fitness(_totals(equity=capital * (1.0 + overshoot)))
    further = _fitness(_totals(equity=capital * (1.0 + overshoot + 0.5)))
    assert further <= nearer


@settings(max_examples=100, deadline=None)
@given(gearing=st.floats(min_value=0.0, max_value=1.0))
def test_an_empty_portfolio_scores_the_floor_whatever_else_is_true(gearing: float) -> None:
    """Checked first, because an empty portfolio is feasible on equity."""
    empty = Aggregates(
        project_count=np.array([0.0]),
        capacity_mw=np.array([0.0]),
        total_capex=np.array([0.0]),
        senior_debt=np.array([0.0]),
        equity=np.array([0.0]),
        solar_capacity_mw=np.array([0.0]),
        solar_share=np.array([0.0]),
        gearing=np.array([gearing]),
        merchant_share=np.array([0.0]),
        weighted_risk=np.array([0.0]),
        blended_irr=np.array([np.nan]),
        country_shares=np.zeros((1, 1)),
        project_shares=np.zeros((1, 1)),
    )
    assert _fitness(empty) == ASSUMPTIONS.objective.empty_portfolio_score
