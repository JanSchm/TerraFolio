"""All 206 of 1C's objective cases, and the one place we deliberately disagree.

Each case carries its own screened pool and its own per-project IRRs, so this tests
the **objective** rather than the screens or the returns layer: a case is reproducible
even if eligibility were computed differently. That separation is what makes a failure
here mean what it says.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from reference_mandates import REFERENCE_MANDATES

from terrafolio.config.loader import load_default
from terrafolio.optimiser.aggregate import Aggregates, aggregate
from terrafolio.optimiser.features import Features, build_features
from terrafolio.optimiser.objective import TERM_ORDER, quantise, score, score_terms
from terrafolio.pipeline.loader import load_pipeline

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TOLERANCE = 1e-12

ASSUMPTIONS = load_default()
ARRAYS = load_pipeline(FIXTURES / "pipeline", ASSUMPTIONS).arrays
INDEX = ARRAYS.index_map()

CASES: dict[str, Any] = json.loads((FIXTURES / "objective_cases.json").read_text(encoding="utf-8"))

MERCHANT_SHARE = 1.0 - ARRAYS.revenue.ppa_share
"""§7.1 and §10.2 are both capex-weighted on the file's ``ppaShare``.

``economics.returns.contracted_revenue_share`` computes a different, revenue-weighted
figure for the detail sheet; feeding that one here would move every merchant penalty.
"""

ONE_ULP_UNDER = "OBJ-205"
"""The single case this implementation is *required* to disagree with. See below."""


def _case_features(case: dict[str, Any]) -> Features:
    """Features for the case's own pool, carrying the case's own per-project IRRs."""
    pool = case["pool"]["ids"]
    equity_irr = np.full(ARRAYS.count, np.nan)
    defined = np.zeros(ARRAYS.count, dtype=np.bool_)
    for project_id, rate in case["perProjectIrr"].items():
        if rate is not None:
            equity_irr[INDEX[project_id]] = rate
            defined[INDEX[project_id]] = True

    rows = np.array([INDEX[project_id] for project_id in pool], dtype=np.intp)
    return build_features(
        ARRAYS,
        equity_irr=np.nan_to_num(equity_irr),
        irr_defined=defined,
        merchant_share=MERCHANT_SHARE,
    ).take(rows)


def _selection(case: dict[str, Any], dtype: type = np.float64) -> np.ndarray:
    return np.array([[float(gene) for gene in case["genes"]]], dtype=dtype)


def _totals(case: dict[str, Any]) -> Aggregates:
    return aggregate(_case_features(case), _selection(case))


def _expected_fitness(case: dict[str, Any]) -> float:
    """The amended reject score where the reference's own rule is recorded (1C-7)."""
    if case["branch"] == "over-budget":
        return float(case["rejectDetail"]["fitnessAmendedPerEpic6_2"])
    return float(case["fitness"])


def _scored(case: dict[str, Any]) -> float:
    return float(score(_totals(case), REFERENCE_MANDATES[case["mandateId"]], ASSUMPTIONS)[0])


def test_the_corpus_is_all_of_it() -> None:
    assert len(CASES["cases"]) == CASES["coverage"]["total"] == 206
    assert {case["mandateId"] for case in CASES["cases"]} <= set(REFERENCE_MANDATES)


def test_the_term_order_is_the_one_the_fixture_records() -> None:
    """Floating-point addition is not associative, so this is arithmetic, not style."""
    assert list(TERM_ORDER) == CASES["termAccumulationOrder"]


def test_the_weights_and_clamps_come_from_the_assumption_set() -> None:
    """The fixture's own copy of §10.2's constants must agree with the shipped set."""
    weights, clamps = CASES["weights"], CASES["clamps"]
    objective = ASSUMPTIONS.objective
    assert objective.capacity_weight == weights["capacity"]
    assert objective.tech_split_weight == weights["techMix"]
    assert objective.return_weight == weights["returns"]
    assert objective.utilisation_weight == weights["utilisation"]
    assert objective.leverage_weight == weights["leveragePenalty"]
    assert objective.merchant_weight == weights["merchantPenalty"]
    assert objective.risk_weight == weights["riskPenalty"]
    assert objective.country_concentration_weight == weights["countryConcentrationPenalty"]
    assert objective.project_concentration_weight == weights["projectConcentrationPenalty"]
    assert objective.tech_split_tolerance == clamps["techMixScale"]
    assert objective.return_scale == clamps["returnsScale"]
    assert objective.return_clamp == clamps["returnsBand"]
    assert objective.empty_portfolio_score == CASES["emptyPortfolioScore"]
    # The rails are stated as deviations in the fixture and as score floors in the
    # assumption set: `1 - 1.6 = -0.6` and `1 - 1.5 = -0.5`.
    assert objective.capacity_score_floor == pytest.approx(1 - clamps["capacityDeviation"])
    assert objective.tech_split_score_floor == pytest.approx(1 - clamps["techMixDeviation"])


@pytest.mark.parametrize(
    "case",
    [case for case in CASES["cases"] if case["caseId"] != ONE_ULP_UNDER],
    ids=lambda case: f"{case['caseId']}-{case['mandateId']}",
)
def test_every_case_reproduces_its_recorded_fitness(case: dict[str, Any]) -> None:
    """Acceptance criterion 5, at 1e-12. Measured worst across the corpus: 2.3e-13."""
    assert _scored(case) == pytest.approx(_expected_fitness(case), abs=TOLERANCE)


def test_the_one_ulp_under_case_is_the_single_designed_divergence() -> None:
    """Epic §6.2's €1 cap tolerance, doing exactly what it exists to do.

    The reference rejects on a strict ``equity > capital``, so a capital one ulp below
    the selection's equity — about 1e-7 euros of overshoot — is rejected. The epic
    amends that with a €1 tolerance, precisely because the capital-utilisation reward
    pushes good portfolios onto the boundary and a portfolio landing on it must not be
    rejected by a rounding artefact. One ulp *is* the rounding artefact.

    So this case scores as feasible here, and that is the required behaviour rather
    than a near miss. Its sibling ``OBJ-204``, exactly on the cap, agrees with the
    reference and is covered by the parametrised test above.
    """
    case = next(c for c in CASES["cases"] if c["caseId"] == ONE_ULP_UNDER)
    mandate = REFERENCE_MANDATES[case["mandateId"]]
    totals = _totals(case)

    overshoot_eur = float(totals.equity[0]) - mandate.available_capital_eur
    assert 0.0 < overshoot_eur < ASSUMPTIONS.objective.equity_cap_tolerance_eur

    assert _scored(case) > 0.0
    assert _scored(case) != pytest.approx(_expected_fitness(case), abs=TOLERANCE)

    on_the_cap = next(c for c in CASES["cases"] if c["caseId"] == "OBJ-204")
    assert _scored(case) == pytest.approx(_scored(on_the_cap), abs=TOLERANCE)


# ---------------------------------------------------------------------------
# The overrides, checked as rules rather than as cases
# ---------------------------------------------------------------------------


def test_an_empty_portfolio_scores_exactly_the_configured_floor() -> None:
    """Exactly -50, not approximately: an empty portfolio is feasible on equity, so
    the check has to come first or it never fires at all."""
    empties = [case for case in CASES["cases"] if case["branch"] == "empty"]
    assert len(empties) == CASES["coverage"]["branchEmpty"]
    for case in empties:
        assert _scored(case) == ASSUMPTIONS.objective.empty_portfolio_score


def _over_budget_here(case: dict[str, Any]) -> bool:
    """Over budget by *this* implementation's rule, not by the fixture's label.

    They agree on 20 of the 21 cases the fixture calls over-budget. The exception is
    ``OBJ-205``, which the €1 cap tolerance admits — asserting the ordering rule over
    a set that includes it would be asserting the reference's rule, not ours.
    """
    return case["branch"] == "over-budget" and case["caseId"] != ONE_ULP_UNDER


def test_every_rejected_score_is_below_every_feasible_one() -> None:
    """The whole reason epic §6.2 amends the mockup's ``-20 - equity/capital``."""
    rejected = [_scored(case) for case in CASES["cases"] if _over_budget_here(case)]
    feasible = [_scored(case) for case in CASES["cases"] if case["branch"] in {"normal", "empty"}]
    assert rejected and feasible
    assert max(rejected) < min(feasible)


def test_the_reject_score_is_monotone_in_the_overshoot() -> None:
    """Graded, so selection has something to work with among infeasible chromosomes."""
    rejected = [
        (case["rejectDetail"]["overshootRatio"], _scored(case))
        for case in CASES["cases"]
        if _over_budget_here(case)
    ]
    by_overshoot = sorted(rejected)
    fitnesses = [fitness for _, fitness in by_overshoot]
    assert fitnesses == sorted(fitnesses, reverse=True)


def test_the_reject_score_is_always_below_the_empty_floor() -> None:
    for case in CASES["cases"]:
        if _over_budget_here(case):
            assert _scored(case) < ASSUMPTIONS.objective.empty_portfolio_score


# ---------------------------------------------------------------------------
# The terms, and the undefined-IRR invariant
# ---------------------------------------------------------------------------


def test_each_term_reproduces_its_recorded_contribution() -> None:
    """Not only the sum: a pair of compensating errors sums correctly."""
    checked = 0
    for case in CASES["cases"]:
        if case["terms"] is None:
            continue
        terms = score_terms(_totals(case), REFERENCE_MANDATES[case["mandateId"]], ASSUMPTIONS)
        for name in TERM_ORDER:
            expected = case["terms"][name]["contribution"]
            assert float(terms[name][0]) == pytest.approx(expected, abs=TOLERANCE), (
                f"{case['caseId']} term {name}"
            )
        checked += 1
    assert checked == CASES["coverage"]["branchNormal"]


def test_an_undefined_project_irr_is_excluded_rather_than_counted_as_zero() -> None:
    """The invariant, exercised where it actually bites.

    1C-6 predicted a divergence from the reference at short holds, and the corpus
    does carry five cases with a null per-project IRR — but in every one the returns
    term is already pinned to its negative rail, so excluding and coalescing give the
    same clipped contribution. The difference is real all the same, and this builds
    the case that shows it: one defined project and one undefined, with a hurdle low
    enough that the term is interior.
    """
    rows = np.array([INDEX["P01"], INDEX["P03"]], dtype=np.intp)
    equity_irr = np.full(ARRAYS.count, np.nan)
    defined = np.zeros(ARRAYS.count, dtype=np.bool_)
    equity_irr[INDEX["P01"]] = 0.12
    defined[INDEX["P01"]] = True

    excluding = build_features(
        ARRAYS,
        equity_irr=np.nan_to_num(equity_irr),
        irr_defined=defined,
        merchant_share=MERCHANT_SHARE,
    ).take(rows)
    coalescing = build_features(
        ARRAYS,
        equity_irr=np.nan_to_num(equity_irr),
        irr_defined=np.ones(ARRAYS.count, dtype=np.bool_),
        merchant_share=MERCHANT_SHARE,
    ).take(rows)

    both = np.array([[1.0, 1.0]])
    blended_excluding = float(aggregate(excluding, both).blended_irr[0])
    blended_coalescing = float(aggregate(coalescing, both).blended_irr[0])

    assert blended_excluding == pytest.approx(0.12)
    assert blended_coalescing < blended_excluding
    assert blended_coalescing == pytest.approx(
        0.12
        * ARRAYS.capital.equity[INDEX["P01"]]
        / (ARRAYS.capital.equity[INDEX["P01"]] + ARRAYS.capital.equity[INDEX["P03"]])
    )


def test_a_portfolio_with_no_defined_irr_contributes_nothing_rather_than_a_penalty() -> None:
    """§13: the return term falls back to the hurdle, so it scores exactly zero."""
    rows = np.array([INDEX["P01"]], dtype=np.intp)
    features = build_features(
        ARRAYS,
        equity_irr=np.zeros(ARRAYS.count),
        irr_defined=np.zeros(ARRAYS.count, dtype=np.bool_),
        merchant_share=MERCHANT_SHARE,
    ).take(rows)
    totals = aggregate(features, np.array([[1.0]]))
    assert np.isnan(totals.blended_irr[0])

    terms = score_terms(totals, REFERENCE_MANDATES["M0-default"], ASSUMPTIONS)
    assert float(terms["returns"][0]) == 0.0


# ---------------------------------------------------------------------------
# Quantisation is a property of the comparison, not of the objective
# ---------------------------------------------------------------------------


def test_the_objective_itself_is_not_rounded() -> None:
    """Rounding here would put the oracle six decimal places out of reach."""
    case = next(c for c in CASES["cases"] if c["branch"] == "normal")
    exact = _scored(case)
    assert exact == pytest.approx(_expected_fitness(case), abs=TOLERANCE)
    assert exact != quantise(np.array([exact]), ASSUMPTIONS)[0] or exact == round(exact, 6)


def test_quantisation_rounds_to_the_configured_precision() -> None:
    values = np.array([1.23456749, -1000.0000004])
    rounded = quantise(values, ASSUMPTIONS)
    assert rounded.tolist() == [
        round(1.23456749, ASSUMPTIONS.objective.quantisation_dp),
        round(-1000.0000004, ASSUMPTIONS.objective.quantisation_dp),
    ]
