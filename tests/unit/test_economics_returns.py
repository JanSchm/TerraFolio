"""The two cash-flow series, and what is built on each.

The distinction between them is the most likely silent bug in the whole feature, so
it is asserted directly here rather than inferred from a downstream number that
happens to come out right.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest
from test_pipeline_arrays import sample_arrays

from terrafolio.config.loader import load_default
from terrafolio.domain.conventions import YEARS, exit_index
from terrafolio.economics.returns import (
    contracted_revenue_share,
    hold_truncated_fcfe,
    moic,
    payback_period,
    project_returns,
    thirty_year_fcfe,
)
from terrafolio.economics.terminal import exit_multiples, terminal_value

ASSUMPTIONS = load_default()
HOLD = 10


# ---------------------------------------------------------------------------
# The two series
# ---------------------------------------------------------------------------


def test_the_hold_series_includes_the_terminal_value_and_the_thirty_year_one_does_not() -> None:
    """The acceptance criterion, asserted as arithmetic rather than as a vibe.

    Truncate the 30-year series at the hold year and the only thing separating it
    from the IRR series is the exit proceeds. If that difference ever comes out zero,
    the terminal value has gone missing; if it comes out twice, it has been added
    somewhere else as well.
    """
    arrays = sample_arrays()
    index = exit_index(HOLD)

    truncated_without = arrays.statements.cash_flow.fcfe[:, : index + 1]
    with_terminal = hold_truncated_fcfe(arrays, ASSUMPTIONS, HOLD)
    difference = with_terminal.sum(axis=-1) - truncated_without.sum(axis=-1)

    assert np.allclose(difference, terminal_value(arrays, ASSUMPTIONS, HOLD), rtol=0, atol=1e-6)
    assert (difference > 0.0).all()


def test_the_thirty_year_sum_is_the_files_own_series_untouched() -> None:
    arrays = sample_arrays()
    assert np.array_equal(thirty_year_fcfe(arrays), arrays.statements.cash_flow.fcfe.sum(axis=-1))


def test_the_two_series_have_different_lengths() -> None:
    arrays = sample_arrays()
    assert arrays.statements.cash_flow.fcfe.shape[-1] == YEARS
    assert hold_truncated_fcfe(arrays, ASSUMPTIONS, HOLD).shape[-1] == HOLD


def test_building_the_hold_series_does_not_mutate_the_arrays() -> None:
    """It writes into its last column; a view rather than a copy would corrupt the
    file's own FCFE and silently move the 30-year tile."""
    arrays = sample_arrays()
    before = arrays.statements.cash_flow.fcfe.copy()
    hold_truncated_fcfe(arrays, ASSUMPTIONS, HOLD)
    hold_truncated_fcfe(arrays, ASSUMPTIONS, HOLD)
    assert np.array_equal(arrays.statements.cash_flow.fcfe, before)


def test_the_hold_series_moves_with_the_hold_period() -> None:
    """§5.1's slider is live because nothing mandate-dependent is stored."""
    arrays = sample_arrays()
    short = hold_truncated_fcfe(arrays, ASSUMPTIONS, 5)
    long = hold_truncated_fcfe(arrays, ASSUMPTIONS, 20)
    assert short.shape[-1] == 5
    assert long.shape[-1] == 20
    assert short[:, -1].tolist() != long[:, 4].tolist()


def test_the_exit_year_flag_is_honoured() -> None:
    arrays = sample_arrays()
    excluded = dataclasses.replace(
        ASSUMPTIONS,
        interpretation=dataclasses.replace(
            ASSUMPTIONS.interpretation, exit_year_fcfe_included=False
        ),
    )
    index = exit_index(HOLD)
    proceeds = terminal_value(arrays, ASSUMPTIONS, HOLD)
    assert np.allclose(hold_truncated_fcfe(arrays, excluded, HOLD)[:, index], proceeds)

    own_flow = arrays.statements.cash_flow.fcfe[:, index]
    assert np.allclose(
        hold_truncated_fcfe(arrays, ASSUMPTIONS, HOLD)[:, index], own_flow + proceeds
    )


def test_hold_years_outside_the_window_is_refused() -> None:
    arrays = sample_arrays()
    with pytest.raises(ValueError, match="hold_years"):
        project_returns(arrays, ASSUMPTIONS, 0)
    with pytest.raises(ValueError, match="hold_years"):
        project_returns(arrays, ASSUMPTIONS, YEARS + 1)


# ---------------------------------------------------------------------------
# Terminal value
# ---------------------------------------------------------------------------


def test_the_exit_multiple_is_per_technology() -> None:
    arrays = sample_arrays()
    multiples = exit_multiples(arrays, ASSUMPTIONS)
    assert multiples[0] == ASSUMPTIONS.exit_multiples[arrays.asset.technologies[0]]
    assert multiples[0] != multiples[1]


def test_terminal_value_is_multiple_times_ebitda_less_outstanding_debt() -> None:
    arrays = sample_arrays()
    index = exit_index(HOLD)
    expected = (
        exit_multiples(arrays, ASSUMPTIONS) * arrays.statements.income.ebitda[:, index]
        - arrays.statements.debt.closing[:, index]
    )
    assert np.allclose(terminal_value(arrays, ASSUMPTIONS, HOLD), np.maximum(expected, 0.0))


def test_terminal_value_is_floored_at_zero() -> None:
    """Limited liability: equity is wiped out, it does not owe the difference."""
    arrays = sample_arrays()
    drowning = dataclasses.replace(
        arrays,
        statements=dataclasses.replace(
            arrays.statements,
            debt=dataclasses.replace(
                arrays.statements.debt,
                closing=arrays.statements.debt.closing * 1e6,
            ),
        ),
    )
    assert (terminal_value(drowning, ASSUMPTIONS, HOLD) == 0.0).all()


# ---------------------------------------------------------------------------
# MOIC and payback
# ---------------------------------------------------------------------------


def test_moic_is_distributions_over_contributions() -> None:
    series = np.array([[-100.0, 40.0, 40.0, 120.0]])
    assert float(moic(series)[0]) == pytest.approx(200.0 / 100.0)


def test_moic_is_undefined_when_nothing_was_contributed() -> None:
    assert np.isnan(moic(np.array([[10.0, 10.0]]))[0])
    assert np.isnan(moic(np.array([[0.0, 0.0]]))[0])


def test_moic_counts_every_outflow_not_only_the_first() -> None:
    series = np.array([[-100.0, -50.0, 300.0]])
    assert float(moic(series)[0]) == pytest.approx(300.0 / 150.0)


def test_payback_is_the_first_period_the_cumulative_turns_non_negative() -> None:
    series = np.array([[-100.0, 40.0, 40.0, 40.0]])
    assert float(payback_period(series)[0]) == 4.0


def test_payback_is_a_period_not_a_calendar_year() -> None:
    """The distinction that cost a bug.

    ``docs/api.md`` §2 gives ``paybackYear`` as a calendar year and
    ``ProjectScalars`` bounds it to 2000-2100. This function knows nothing about a
    base year, so it returns a small 1-based period and the result layer converts —
    storing this number under the wire's name would fail validation on every holding.
    """
    series = np.array([[-100.0, 40.0, 40.0, 40.0]])
    assert float(payback_period(series)[0]) < 2000.0


def test_payback_is_undefined_when_it_never_happens() -> None:
    """Not "the hold length" — "not paid back by year ten" is a different answer."""
    assert np.isnan(payback_period(np.array([[-100.0, 10.0, 10.0]]))[0])


# ---------------------------------------------------------------------------
# Contracted revenue share
# ---------------------------------------------------------------------------


def test_contracted_share_is_not_simply_the_ppa_share() -> None:
    """N-1's point: a volume share over a 10-year tenor is not a revenue share over 30."""
    arrays = sample_arrays()
    share = contracted_revenue_share(arrays)
    assert (share < arrays.revenue.ppa_share).all()
    assert (share >= 0.0).all()


def test_a_project_with_no_ppa_is_fully_merchant() -> None:
    arrays = sample_arrays()
    merchant = dataclasses.replace(
        arrays,
        revenue=dataclasses.replace(
            arrays.revenue,
            ppa_share=np.zeros_like(arrays.revenue.ppa_share),
            ppa_tenor_years=np.zeros_like(arrays.revenue.ppa_tenor_years),
        ),
    )
    assert (contracted_revenue_share(merchant) == 0.0).all()


# ---------------------------------------------------------------------------
# The bundle
# ---------------------------------------------------------------------------


def test_project_returns_agrees_with_its_parts() -> None:
    arrays = sample_arrays()
    returns = project_returns(arrays, ASSUMPTIONS, HOLD)
    assert returns.hold_years == HOLD
    assert np.array_equal(returns.series, hold_truncated_fcfe(arrays, ASSUMPTIONS, HOLD))
    assert np.array_equal(returns.terminal, terminal_value(arrays, ASSUMPTIONS, HOLD))
    assert np.array_equal(np.isnan(returns.moic), np.isnan(moic(returns.series)))


def test_nothing_is_cached_across_hold_periods() -> None:
    """The slider stops working the moment anything here is memoised without the hold."""
    arrays = sample_arrays()
    five = project_returns(arrays, ASSUMPTIONS, 5)
    twenty = project_returns(arrays, ASSUMPTIONS, 20)
    assert five.series.shape != twenty.series.shape
    assert not np.allclose(five.terminal, twenty.terminal)
