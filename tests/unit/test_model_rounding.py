"""Half-up rounding, and why Python's built-in will not do.

The reference rounds in four places that reach an emitted file or the oracle:
the capture price to a whole EUR/MWh, the risk score to one decimal, the
contracted share to two, and min DSCR to two. A capture price that rounds the
other way changes the PPA price, every year's revenue, the entry capex and the
debt quantum -- so the tie rule is load-bearing, not cosmetic.
"""

from __future__ import annotations

import pytest

from terrafolio.model.rounding import js_round, js_round_to


@pytest.mark.parametrize(
    ("value", "expected"), [(0.5, 1), (1.5, 2), (2.5, 3), (3.5, 4), (-0.5, 0), (39.44, 39)]
)
def test_halves_go_up_not_to_even(value: float, expected: int) -> None:
    assert js_round(value) == expected


def test_python_s_own_round_disagrees_on_exactly_the_cases_that_matter() -> None:
    """Recorded so the reason for this module is visible, not assumed."""
    assert round(0.5) == 0
    assert round(2.5) == 2
    assert js_round(0.5) == 1
    assert js_round(2.5) == 3


@pytest.mark.parametrize(
    ("value", "places", "expected"),
    [
        # Verified against `node`: these are Math.round(v * 10**p) / 10**p.
        (2.675, 2, 2.68),
        (1.005, 2, 1.0),
        (0.145, 2, 0.14),
        (0.8449999, 2, 0.84),
        (2.65, 1, 2.7),
        (39.44, 0, 39.0),
    ],
)
def test_shifting_by_a_power_of_ten_matches_the_reference(
    value: float, places: int, expected: float
) -> None:
    """Including where the shift, not the tie rule, decides the answer.

    ``2.675 * 100`` is exactly ``267.5`` in binary64 and rounds up; ``1.005 *
    100`` is ``100.49999999999999`` and rounds down. Reaching for
    :mod:`decimal` here would give a different -- and wrong, for this purpose --
    answer on both.
    """
    assert js_round_to(value, places) == expected


def test_the_capture_price_case_the_corpus_actually_exercises() -> None:
    """Iberian and Italian solar, the two that set the effective capture factor."""
    assert js_round(58 * 0.68) == 39
    assert js_round(92 * 0.68) == 63


def test_negative_places_are_refused() -> None:
    with pytest.raises(ValueError, match="places"):
        js_round_to(1.0, -1)
