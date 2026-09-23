"""The twelve tiles, the two series, and the hand-off to 3A.

The field-parity tests are the point of this module. ``optimiser/result.py`` emits
plain dataclasses in euros because the numeric core may not import pydantic, and
``domain/results.py``'s models are pydantic in €m. That correspondence is a convention
until something checks it, and a convention across an issue boundary is a convention
that breaks — so it is checked, both ways.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from test_optimiser_screens import ASSUMPTIONS, mandate

from terrafolio.domain.enums import Effort
from terrafolio.domain.results import Holding as WireHolding
from terrafolio.domain.results import PortfolioAggregates
from terrafolio.economics.irr import irr
from terrafolio.economics.returns import contracted_revenue_share, project_returns
from terrafolio.optimiser.features import build_features
from terrafolio.optimiser.ga import SearchControls, run_search
from terrafolio.optimiser.objective import TERM_ORDER
from terrafolio.optimiser.result import (
    Compliance,
    Holding,
    PortfolioTotals,
    SelectionOutcome,
    _weighted_lcoe,
    build_result,
)
from terrafolio.optimiser.screens import apply_screens
from terrafolio.pipeline.loader import load_pipeline

FIXTURES = Path(__file__).resolve().parents[1] / "golden" / "fixtures" / "pipeline"
LOADED = load_pipeline(FIXTURES, ASSUMPTIONS)
ARRAYS = LOADED.arrays
MANDATE = mandate(
    countries=tuple(sorted(set(ARRAYS.location.country_codes))),
    capacity_target_mw=1500.0,
    available_capital_eur=1_200e6,
)


def _build_result() -> object:
    screens = apply_screens(ARRAYS, MANDATE, ASSUMPTIONS)
    rows = np.flatnonzero(screens.eligible)
    returns = project_returns(ARRAYS, ASSUMPTIONS, MANDATE.hold_years)
    features = build_features(
        ARRAYS,
        equity_irr=np.nan_to_num(returns.equity_irr),
        irr_defined=returns.defined,
        merchant_share=1.0 - ARRAYS.revenue.ppa_share,
    ).take(rows)
    outcome = run_search(
        features,
        MANDATE,
        ASSUMPTIONS,
        SearchControls(effort=Effort.FAST, seed=42),
    )
    return build_result(
        ARRAYS,
        features,
        MANDATE,
        ASSUMPTIONS,
        SelectionOutcome(
            eligible=screens.eligible,
            winner=outcome.selection,
            locked=None,
            returns=returns,
            contracted_share=contracted_revenue_share(ARRAYS),
        ),
    )


@pytest.fixture(scope="module")
def result() -> object:
    """One search, shared by every test here.

    A module-level ``result = _build_result()`` ran a pipeline load and a full search
    during collection, so anything that raised — a NaN reaching a result model, say —
    surfaced as a collection error and took every other test in the file with it. As a
    fixture the cost is the same and the failure lands on the test that provoked it.
    """
    return _build_result()


# ---------------------------------------------------------------------------
# The two cash-flow series
# ---------------------------------------------------------------------------


def test_the_thirty_year_series_is_thirty_years_and_carries_no_terminal_value(result: Any) -> None:
    assert result.cashflow_30y.size == 30  # type: ignore[attr-defined]
    selected = np.array([pid in result.selected_ids for pid in ARRAYS.ids])  # type: ignore[attr-defined]
    expected = ARRAYS.statements.cash_flow.fcfe[selected].sum(axis=0)
    assert np.array_equal(result.cashflow_30y, expected)  # type: ignore[attr-defined]


def test_the_hold_series_is_the_hold_length_and_does_carry_one(result: Any) -> None:
    assert result.cashflow_hold.size == MANDATE.hold_years  # type: ignore[attr-defined]
    truncated = result.cashflow_30y[: MANDATE.hold_years]  # type: ignore[attr-defined]
    difference = float(result.cashflow_hold.sum() - truncated.sum())  # type: ignore[attr-defined]
    assert difference > 0.0


def test_neither_series_is_sliced_from_the_other(result: Any) -> None:
    """If they ever agree element for element, the terminal value has gone missing."""
    hold = result.cashflow_hold  # type: ignore[attr-defined]
    assert not np.array_equal(hold, result.cashflow_30y[: hold.size])  # type: ignore[attr-defined]


def test_the_thirty_year_tile_is_the_sum_of_its_own_series(result: Any) -> None:
    assert result.totals.thirty_year_fcfe == pytest.approx(  # type: ignore[attr-defined]
        float(result.cashflow_30y.sum())  # type: ignore[attr-defined]
    )


# ---------------------------------------------------------------------------
# The tiles
# ---------------------------------------------------------------------------


def test_the_project_counts_partition_the_selection(result: Any) -> None:
    totals = result.totals  # type: ignore[attr-defined]
    assert totals.solar_count + totals.wind_count == totals.project_count
    assert totals.project_count == len(result.selected_ids)  # type: ignore[attr-defined]


def test_capital_deployed_is_a_share_of_the_mandate_not_of_the_pipeline(result: Any) -> None:
    totals = result.totals  # type: ignore[attr-defined]
    assert totals.capital_deployed == pytest.approx(totals.equity / MANDATE.available_capital_eur)


def test_the_co2_factor_comes_from_the_assumption_set(result: Any) -> None:
    """GWh to MWh is x1000 and tonnes to kilotonnes is /1000, so they cancel."""
    totals = result.totals  # type: ignore[attr-defined]
    assert totals.co2_avoided_kt == pytest.approx(
        totals.annual_generation_gwh * ASSUMPTIONS.co2_t_per_mwh
    )


def test_the_pooled_irr_is_not_the_blended_approximation(result: Any) -> None:
    """§10.3 keeps them distinct: the search optimises one, the tile shows the other."""
    totals = result.totals  # type: ignore[attr-defined]
    assert totals.equity_irr is not None
    assert totals.blended_irr is not None
    assert totals.equity_irr != pytest.approx(totals.blended_irr, abs=1e-6)


def test_the_pooled_irr_solves_the_portfolios_own_cash_flow(result: Any) -> None:
    rate, defined = irr(result.cashflow_hold[None, :])  # type: ignore[attr-defined]
    assert defined[0]
    assert result.totals.equity_irr == pytest.approx(float(rate[0]))  # type: ignore[attr-defined]


def test_an_undefined_lcoe_does_not_poison_the_weighted_average(result: Any) -> None:
    """``NaN x 0.0`` is ``NaN``, so multiplying through is not enough.

    A project that generates nothing over its life has an undefined LCOE. Weighting
    it by its zero generation looks harmless and is not: the product is ``NaN`` and it
    propagates through the sum, so a single such project would make the whole tile
    ``NaN`` — which ``PortfolioAggregates.weighted_lcoe`` refuses, failing the
    aggregate at 3A's boundary rather than here.
    """
    levelised = np.array([np.nan, 40.0, 50.0])
    generation = np.array([0.0, 100.0, 200.0])
    selection = np.array([True, True, True])

    assert np.isnan((levelised * generation).sum()), "the naive form really is NaN"
    weighted = _weighted_lcoe(levelised, generation, selection)
    assert weighted == pytest.approx((40.0 * 100.0 + 50.0 * 200.0) / 300.0)

    assert not np.isnan(result.totals.weighted_lcoe)


def test_the_terms_sum_to_the_fitness_whenever_they_are_reported(result: Any) -> None:
    assert result.terms is not None
    assert sum(result.terms[name] for name in TERM_ORDER) == pytest.approx(
        result.totals.fitness, abs=1e-6
    )


def test_an_override_reports_no_terms_rather_than_ones_that_do_not_add_up() -> None:
    """An empty winner scores the floor, which the nine terms did not produce.

    Returning them anyway handed a caller nine figures summing to about -5 beside a
    fitness of -50, and the CLI prints the two a few lines apart.
    """
    screens = apply_screens(ARRAYS, MANDATE, ASSUMPTIONS)
    rows = np.flatnonzero(screens.eligible)
    returns = project_returns(ARRAYS, ASSUMPTIONS, MANDATE.hold_years)
    features = build_features(
        ARRAYS,
        equity_irr=np.nan_to_num(returns.equity_irr),
        irr_defined=returns.defined,
        merchant_share=1.0 - ARRAYS.revenue.ppa_share,
    ).take(rows)

    empty = build_result(
        ARRAYS,
        features,
        MANDATE,
        ASSUMPTIONS,
        SelectionOutcome(
            eligible=screens.eligible,
            winner=np.zeros(rows.size, dtype=np.bool_),
            locked=None,
            returns=returns,
            contracted_share=contracted_revenue_share(ARRAYS),
        ),
    )
    assert empty.totals.fitness == ASSUMPTIONS.objective.empty_portfolio_score
    assert empty.terms is None


def test_country_shares_come_from_the_aggregate_not_a_second_computation(
    result: Any,
) -> None:
    """One implementation of country concentration, as ``aggregate``'s docstring says.

    The tile and §10.2's penalty read the same numbers, so a portfolio cannot be
    shaped by one concentration figure and reported with another.
    """
    shares = result.totals.country_shares
    assert all(value > 0.0 for value in shares.values()), "a country held nothing"
    for code, value in shares.items():
        held = np.array(
            [
                pid in set(result.selected_ids) and ARRAYS.location.country_codes[i] == code
                for i, pid in enumerate(ARRAYS.ids)
            ]
        )
        expected = ARRAYS.capital.total_capex[held].sum() / result.totals.total_capex
        assert value == pytest.approx(expected, abs=1e-12)


def test_country_shares_sum_to_one_and_name_the_largest(result: Any) -> None:
    totals = result.totals  # type: ignore[attr-defined]
    assert sum(totals.country_shares.values()) == pytest.approx(1.0)
    assert totals.largest_country_share == max(totals.country_shares.values())
    assert totals.country_shares[totals.largest_country_code] == totals.largest_country_share


def test_compliance_is_a_verdict_where_a_mandate_threshold_exists(result: Any) -> None:
    totals = result.totals  # type: ignore[attr-defined]
    assert totals.leverage_compliance == (
        Compliance.COMPLIANT if totals.gearing >= MANDATE.min_leverage else Compliance.ALERT
    )
    assert totals.merchant_compliance == (
        Compliance.COMPLIANT
        if totals.merchant_share <= MANDATE.max_merchant_share
        else Compliance.ALERT
    )


def test_tiles_one_and_three_carry_deviations_rather_than_a_banded_verdict(result: Any) -> None:
    """Their 8% and 8-point bands live in ``ui-contract.md`` §5.1, not in config."""
    totals = result.totals  # type: ignore[attr-defined]
    assert totals.capacity_compliance == Compliance.NEUTRAL
    assert totals.capacity_deviation == pytest.approx(
        (totals.capacity_mw - MANDATE.capacity_target_mw) / MANDATE.capacity_target_mw
    )
    assert totals.tech_split_deviation == pytest.approx(totals.solar_share - MANDATE.solar_share)


def test_the_reported_fitness_re_scores_the_winner(result: Any) -> None:
    """The float32 hot path decides the ordering; the reported number is float64."""
    terms = result.terms  # type: ignore[attr-defined]
    assert set(terms) == set(TERM_ORDER)
    assert sum(terms[name] for name in TERM_ORDER) == pytest.approx(
        result.totals.fitness,  # type: ignore[attr-defined]
        abs=1e-6,
    )


# ---------------------------------------------------------------------------
# Holdings
# ---------------------------------------------------------------------------


def test_holdings_carry_the_whole_eligible_set_not_only_the_winners(result: Any) -> None:
    """A-14: §7.4's *Show all candidates* needs to say what the run rejected."""
    screens = apply_screens(ARRAYS, MANDATE, ASSUMPTIONS)
    assert len(result.holdings) == screens.eligible_count  # type: ignore[attr-defined]
    assert len(result.holdings) > len(result.selected_ids)  # type: ignore[attr-defined]


def test_the_selected_flags_agree_with_the_selected_ids(result: Any) -> None:
    """C-16's consistency rule, which the store and the table both depend on."""
    flagged = {holding.id for holding in result.holdings if holding.selected}  # type: ignore[attr-defined]
    assert flagged == set(result.selected_ids)  # type: ignore[attr-defined]


def test_holdings_are_in_canonical_order(result: Any) -> None:
    ids = [holding.id for holding in result.holdings]  # type: ignore[attr-defined]
    assert ids == sorted(ids)


def test_payback_is_a_calendar_year_the_wire_model_would_accept(result: Any) -> None:
    """``economics`` returns a 1-based period; the conversion happens here.

    ``ProjectScalars.payback_year`` is bounded to 2000-2100, so storing the period
    straight through — a ``10`` — made every holding with a defined payback
    unserialisable. The bound is asserted against the model's own field rather than
    against two literals.
    """
    bounds = WireHolding.model_fields["payback_year"].metadata
    lower = next(m.ge for m in bounds if hasattr(m, "ge"))
    upper = next(m.le for m in bounds if hasattr(m, "le"))

    paid_back = [h.payback_year for h in result.holdings if h.payback_year is not None]  # type: ignore[attr-defined]
    assert paid_back, "the fixture should have at least one project that pays back"
    for year in paid_back:
        assert lower <= year <= upper
        assert year >= ARRAYS.base_year


def test_an_undefined_per_project_figure_is_none_not_zero(result: Any) -> None:
    """The NaN-to-None conversion happens here, where arrays stop and records begin."""
    for holding in result.holdings:  # type: ignore[attr-defined]
        for value in (holding.min_dscr, holding.equity_irr, holding.moic):
            assert value is None or not np.isnan(value)


def test_the_revenue_weighted_share_is_not_the_capex_weighted_one(result: Any) -> None:
    """The objective uses ``1 - ppa_share``; this is a different, smaller number.

    ``merchant_share`` is deliberately absent from the row: it is one step from
    ``ppa_share`` and §9's one-source rule applies to a reported record too.
    """
    assert "merchant_share" not in Holding.__dataclass_fields__
    for holding in result.holdings[:5]:  # type: ignore[attr-defined]
        assert holding.contracted_revenue_share < holding.ppa_share


# ---------------------------------------------------------------------------
# The hand-off to 3A and 2B
# ---------------------------------------------------------------------------


def _wire_names(model: type) -> set[str]:
    return set(model.model_fields)


JOIN_SUPPLIED = {
    "name",
    "country",
    "iso3",
    "lat",
    "lon",
    "currency",
    "net_capacity_factor",
    "grid_secured",
    "om_contracted",
    "provenance",
}
"""Fields the numeric core never computes, joined from the loader's ``ProjectFile``
tuple by position. Carrying them through the optimiser would make ``ProjectArrays`` a
second spelling of the file schema."""


AWAITING_A_WIRE_FIELD = {"contracted_revenue_share"}
"""Computed here, with nowhere to go on the wire yet.

The revenue-weighted contracted share is not derivable from the other holding fields —
it needs the declared escalators and the generation series — and ``docs/api.md`` §2
carries no field for it. N-1 on the epic is the open question about whether the split
should be stored in the file instead. Named here rather than dropped, so the gap is a
listed exception rather than an absence nobody notices."""


def test_every_holding_field_maps_onto_the_wire_model(result: Any) -> None:
    """``x`` here becomes ``x`` or ``x_m`` there. Nothing else is permitted."""
    wire = _wire_names(WireHolding)
    for field in set(Holding.__dataclass_fields__) - AWAITING_A_WIRE_FIELD:
        assert field in wire or f"{field}_m" in wire, f"{field} has no home on the wire"


def test_every_wire_holding_field_is_either_computed_here_or_joined(result: Any) -> None:
    ours = set(Holding.__dataclass_fields__)
    unaccounted = {
        field
        for field in _wire_names(WireHolding)
        if field not in ours and field.removesuffix("_m") not in ours
    }
    assert unaccounted <= JOIN_SUPPLIED, f"nothing produces {sorted(unaccounted - JOIN_SUPPLIED)}"


def test_every_aggregate_field_maps_onto_the_wire_model(result: Any) -> None:
    wire = _wire_names(PortfolioAggregates)
    ours = set(PortfolioTotals.__dataclass_fields__)
    display_only = {
        "capacity_deviation",
        "tech_split_deviation",
        "blended_irr",
        "capacity_compliance",
        "return_compliance",
        "leverage_compliance",
        "merchant_compliance",
        "country_compliance",
    }
    for field in ours - display_only:
        assert field in wire or f"{field}_m" in wire, f"{field} has no home on the wire"


def test_every_wire_aggregate_field_is_produced_here(result: Any) -> None:
    ours = set(PortfolioTotals.__dataclass_fields__)
    missing = {
        field
        for field in _wire_names(PortfolioAggregates)
        if field not in ours and field.removesuffix("_m") not in ours
    }
    assert missing == set(), f"nothing produces {sorted(missing)}"


def test_the_core_emits_euros_so_the_wire_can_convert_once(result: Any) -> None:
    """Every ``_m`` field on the wire is an ``_m``-less one here, in euros."""
    totals = result.totals  # type: ignore[attr-defined]
    assert totals.equity > 1e6
    assert totals.total_capex > 1e6
    assert not any(name.endswith("_m") for name in PortfolioTotals.__dataclass_fields__)
    assert not any(name.endswith("_m") for name in Holding.__dataclass_fields__)
