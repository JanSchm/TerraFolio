"""Annuity sizing and its two amortisation schedules.

Issue #8 pins four figures here, and each one guards a different mistake:
the PV factor itself (using the reciprocal), the principal sum (a schedule that
does not retire the debt), the closed form against the recurrence (an algebra
slip), and the terminal balance (an off-by-one in the tenor).
"""

from __future__ import annotations

from typing import Final

import numpy as np
import pytest

from terrafolio.model.annuity import (
    annuity_payment_factor,
    annuity_pv_factor,
    closed_form_schedule,
    schedule,
)

RATE: Final = 0.055
TENOR: Final = 18
DEBT: Final = 74.3922455688865
"""The template project's senior debt, so the figures here are a real case."""

PINNED_PV_FACTOR: Final = 11.246074465287
"""Issue #8's pinned value for ``annuity_pv_factor(0.055, 18)``."""


def test_the_pv_factor_is_the_pinned_figure() -> None:
    assert annuity_pv_factor(RATE, TENOR) == pytest.approx(PINNED_PV_FACTOR, abs=1e-12)


def test_the_two_factors_are_reciprocal() -> None:
    """Both computed from the definition, so this is a real check, not a tautology."""
    assert annuity_pv_factor(RATE, TENOR) * annuity_payment_factor(RATE, TENOR) == pytest.approx(
        1.0, abs=1e-15
    )


def test_sizing_multiplies_by_the_pv_factor_not_divides() -> None:
    """The slip in issue #8's prose, guarded by its own arithmetic.

    Sizing a project whose stabilised EBITDA is EUR 9.27m at a 1.40x cover gives
    roughly EUR 74m of debt. Dividing by the PV factor rather than multiplying
    gives EUR 0.59m -- visibly not a project financing, which is what makes the
    slip easy to catch once it is written down.
    """
    stabilised, target = 9.2666, 1.40
    sized = stabilised / target * annuity_pv_factor(RATE, TENOR)
    assert sized == pytest.approx(74.4, abs=0.1)
    assert stabilised / target / annuity_pv_factor(RATE, TENOR) < 1.0


def test_the_principals_retire_the_debt() -> None:
    """Issue #8 asks for 1e-10; the recurrence lands three orders inside it."""
    principal = schedule(DEBT, RATE, TENOR).principal
    assert principal.sum() == pytest.approx(DEBT, abs=1e-10)


def test_the_facility_closes_at_zero() -> None:
    assert schedule(DEBT, RATE, TENOR).closing[-1] == pytest.approx(0.0, abs=1e-10)


def test_the_closed_form_matches_the_recurrence_across_every_period() -> None:
    """Issue #8's 1e-9, over all 18 periods and all four series."""
    loop, closed = schedule(DEBT, RATE, TENOR), closed_form_schedule(DEBT, RATE, TENOR)
    for name in loop._fields:
        assert np.allclose(getattr(loop, name), getattr(closed, name), rtol=0, atol=1e-9), (
            f"{name} diverges"
        )


def test_the_closed_form_is_not_bit_identical_and_that_is_why_it_does_not_emit() -> None:
    """Recorded as a test because it is the reason for having two schedules.

    Both are correct; only the recurrence reproduces the committed corpus, so
    the seed pipeline is emitted from it. If this ever starts passing bit for
    bit, the second implementation has stopped earning its place.
    """
    loop, closed = schedule(DEBT, RATE, TENOR), closed_form_schedule(DEBT, RATE, TENOR)
    assert not np.array_equal(loop.opening, closed.opening)


def test_interest_accrues_on_the_opening_balance() -> None:
    """The first period carries a full year on the whole facility."""
    built = schedule(DEBT, RATE, TENOR)
    assert built.opening[0] == DEBT
    assert built.interest[0] == pytest.approx(DEBT * RATE, abs=1e-15)


def test_service_is_level() -> None:
    """Sculpted means *sized*, not shaped: the same payment every year."""
    built = schedule(DEBT, RATE, TENOR)
    service = built.interest + built.principal
    assert np.allclose(service, service[0], rtol=0, atol=1e-12)


def test_a_zero_rate_amortises_in_equal_instalments() -> None:
    built = schedule(DEBT, 0.0, TENOR)
    assert built.principal == pytest.approx(np.full(TENOR, DEBT / TENOR))
    assert built.interest.sum() == 0.0
    assert annuity_pv_factor(0.0, TENOR) == TENOR


def test_degenerate_terms_are_refused() -> None:
    with pytest.raises(ValueError, match="periods"):
        annuity_payment_factor(RATE, 0)
    with pytest.raises(ValueError, match="periods"):
        annuity_pv_factor(RATE, -1)
