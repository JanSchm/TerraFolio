"""IRR and the discounting convention, against hand-computed values.

The three figures issue #6 pins by hand are all here, because they are the only
check on the *convention* — every other test compares one implementation against
another, and two implementations of the wrong convention agree perfectly.
"""

from __future__ import annotations

import numpy as np
import pytest

from terrafolio.economics.annuity import annuity_pv_factor, discount_factors
from terrafolio.economics.irr import (
    IRR_BISECTION_ITERATIONS,
    IRR_BRACKET_HIGH,
    IRR_BRACKET_LOW,
    irr,
    npv,
)

ANALYTIC_DOUBLING_IRR = 0.07177346253629313
"""Exactly ``2 ** 0.1 - 1``: doubling your money over ten periods."""


def _rate(series: list[float]) -> float:
    rates, _ = irr(np.array([series], dtype=np.float64))
    return float(rates[0])


def _defined(series: list[float]) -> bool:
    _, defined = irr(np.array([series], dtype=np.float64))
    return bool(defined[0])


# ---------------------------------------------------------------------------
# The hand-computed values
# ---------------------------------------------------------------------------


def test_annuity_pv_factor_matches_the_pinned_value() -> None:
    """The factor behind the reference's own 18-year debt annuity at 5.5%."""
    assert annuity_pv_factor(0.055, 18) == pytest.approx(11.246074465287, abs=1e-12)


def test_money_doubling_over_ten_periods_gives_the_pinned_irr() -> None:
    """Issue #6's ``[-100, 0x8, +200]``.

    The pinned value ``0.07177346253629313`` is exactly ``2 ** 0.1 - 1``, which needs
    **ten** periods between the outflow and the inflow — nine intervening zeros, not
    the eight the criterion's shorthand writes. Raised on the issue rather than
    adjusted on either side; both series are asserted here so the discrepancy is
    visible rather than papered over.

    The residual against the analytic root is 8 ulp — the floating-point error of the
    NPV evaluation itself, far inside the 1e-9 the issue asks of derived scalars.
    """
    ten_periods = [-100.0] + [0.0] * 9 + [200.0]
    assert _rate(ten_periods) == pytest.approx(ANALYTIC_DOUBLING_IRR, abs=1e-15)

    nine_periods = [-100.0] + [0.0] * 8 + [200.0]
    assert _rate(nine_periods) == pytest.approx(2 ** (1 / 9) - 1, abs=1e-15)
    assert _rate(nine_periods) != pytest.approx(ANALYTIC_DOUBLING_IRR, abs=1e-6)


def test_lcoe_style_annuity_matches_the_pinned_value() -> None:
    """Flat 1 GWh a year for 30 years, €10m capex, no opex, 6% real.

    ``726.489114900472`` €/MWh comes out only if the first operating year is
    discounted once. Discounting from period zero gives ``685.4``.
    """
    present_value_gwh = float(discount_factors(0.06, 30).sum())
    assert 10e6 / (present_value_gwh * 1000) == pytest.approx(726.489114900472, abs=1e-9)


# ---------------------------------------------------------------------------
# Undefined means undefined
# ---------------------------------------------------------------------------


def test_an_all_negative_series_has_no_irr() -> None:
    assert not _defined([-1.0] * 5)
    assert np.isnan(_rate([-1.0] * 5))


def test_an_all_zero_series_has_no_irr() -> None:
    """A departure from the reference, which returns the bottom of its bracket.

    Every rate is a root of the zero function, so the sign test passes vacuously. A
    project with no cash flows has no rate of return.
    """
    assert not _defined([0.0] * 5)
    assert np.isnan(_rate([0.0] * 5))


def test_an_all_positive_series_has_no_irr() -> None:
    assert not _defined([1.0] * 5)


def test_undefined_is_never_zero() -> None:
    """§13, and the invariant that is easiest to lose at a hand-off."""
    series = [[-1.0] * 5, [0.0] * 5, [-100.0, 200.0, 0.0, 0.0, 0.0]]
    rates, defined = irr(np.array(series, dtype=np.float64))
    assert defined.tolist() == [False, False, True]
    assert np.isnan(rates[0]) and np.isnan(rates[1])
    assert not np.any(rates[~defined] == 0.0)


def test_the_mask_and_the_nan_agree() -> None:
    """Redundant on purpose: a caller that forgets the mask still gets a NaN."""
    series = np.array([[-1.0] * 4, [-100.0, 40.0, 40.0, 40.0], [0.0] * 4], dtype=np.float64)
    rates, defined = irr(series)
    assert np.array_equal(np.isnan(rates), ~defined)


# ---------------------------------------------------------------------------
# The solver itself
# ---------------------------------------------------------------------------


def test_npv_is_zero_at_the_computed_rate() -> None:
    series = np.array([[-100.0, 30.0, 40.0, 50.0], [-250.0, 60.0, 60.0, 200.0]])
    rates, defined = irr(series)
    assert defined.all()
    assert np.allclose(npv(rates, series), 0.0, atol=1e-9)


def test_npv_uses_horner_with_the_first_element_undiscounted() -> None:
    """A different NPV from the reference's by a constant factor — same root."""
    series = np.array([[-100.0, 110.0]])
    rate = np.array([0.1])
    assert float(npv(rate, series)[0]) == pytest.approx(-100.0 + 110.0 / 1.1)


def test_the_bracket_is_wider_than_the_references() -> None:
    """A 150% IRR is a number, not "undefined" — the reference's 1.2 ceiling is not."""
    assert IRR_BRACKET_LOW < -0.5
    assert IRR_BRACKET_HIGH > 1.2
    assert _rate([-100.0, 250.0]) == pytest.approx(1.5, abs=1e-12)


def test_a_rate_beyond_the_bracket_is_undefined_rather_than_clamped() -> None:
    """The bracket is semantic: outside it there is no answer, not a railed one."""
    beyond = [-1.0, float(IRR_BRACKET_HIGH + 2.0) * 10.0]
    rate = _rate(beyond)
    assert np.isnan(rate) or rate <= IRR_BRACKET_HIGH


def test_iteration_count_exhausts_the_double() -> None:
    """11 / 2**100 is 1e-29, so the answer is the root and not an approach to it."""
    assert IRR_BISECTION_ITERATIONS >= 60
    first = _rate([-100.0, 30.0, 40.0, 50.0])
    second = _rate([-100.0, 30.0, 40.0, 50.0])
    assert first == second


def test_rows_are_solved_independently() -> None:
    rows = [[-100.0, 130.0, 0.0], [-100.0, 0.0, 121.0], [-1.0, -1.0, 0.0]]
    together = irr(np.array(rows))[0]
    apart = [_rate(row) for row in rows]
    assert together[0] == pytest.approx(apart[0])
    assert together[1] == pytest.approx(apart[1])
    assert np.isnan(together[2]) and np.isnan(apart[2])


def test_a_known_root_is_recovered_exactly_enough() -> None:
    """``-100`` now against ``121`` in two periods is 10% a period."""
    assert _rate([-100.0, 0.0, 121.0]) == pytest.approx(0.1, abs=1e-12)


def test_annuity_pv_factor_handles_a_zero_rate_as_a_limit() -> None:
    """A zero discount rate is a legitimate assumption set, not a division by zero."""
    assert annuity_pv_factor(0.0, 12) == 12.0


def test_discount_factors_sum_to_the_annuity_factor() -> None:
    assert float(discount_factors(0.07, 15).sum()) == pytest.approx(annuity_pv_factor(0.07, 15))


def test_negative_periods_are_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        annuity_pv_factor(0.05, -1)
    with pytest.raises(ValueError, match="negative"):
        discount_factors(0.05, -1)
