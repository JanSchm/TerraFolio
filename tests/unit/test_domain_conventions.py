"""Indexing and ordering conventions — small functions that three issues rely on."""

from __future__ import annotations

import random

import pytest

from terrafolio.domain.conventions import (
    EUR_PER_EUR_MILLION,
    MWH_PER_GWH,
    YEARS,
    canonical_order,
    exit_index,
    ramp_index,
)


def test_the_file_format_is_thirty_years() -> None:
    assert YEARS == 30


def test_unit_constants_are_definitional() -> None:
    assert EUR_PER_EUR_MILLION == 1_000_000
    assert MWH_PER_GWH == 1_000


# --------------------------------------------------------------------------
# Canonical order
# --------------------------------------------------------------------------


def test_shuffling_does_not_change_canonical_order() -> None:
    """The GA's draws are position-indexed, so this is determinism, not tidiness."""
    ids = [f"P{index:03d}" for index in range(1, 60)]
    shuffled = ids.copy()
    random.Random(42).shuffle(shuffled)
    assert canonical_order(shuffled) == canonical_order(ids) == tuple(ids)


def test_uniform_width_ids_sort_as_intended() -> None:
    assert canonical_order(["P010", "P009", "P100"]) == ("P009", "P010", "P100")


def test_mixed_width_ids_are_the_hazard_the_docstring_warns_about() -> None:
    """Documented, not repaired.

    ``canonical_order`` cannot fix this without assuming ids are numeric, which
    the schema's pattern does not require. Generators zero-pad (issues 1C, 2C)
    and the loader rejects a non-uniform pipeline (issue 2A).
    """
    assert canonical_order(["P9", "P10"]) == ("P10", "P9")


# --------------------------------------------------------------------------
# Ramp year
# --------------------------------------------------------------------------


def test_the_ramp_year_is_the_first_operating_year() -> None:
    assert ramp_index(cod_year=2028, base_year=2027) == 1
    assert ramp_index(cod_year=2027, base_year=2027) == 0


def test_an_already_operating_asset_has_no_ramp_year() -> None:
    """§13: the whole equity outflow is booked in year one, with no partial year."""
    assert ramp_index(cod_year=2025, base_year=2027) is None


def test_a_cod_beyond_the_window_has_no_ramp_year() -> None:
    assert ramp_index(cod_year=2027 + YEARS, base_year=2027) is None
    assert ramp_index(cod_year=2027 + YEARS - 1, base_year=2027) == YEARS - 1


# --------------------------------------------------------------------------
# Exit index
# --------------------------------------------------------------------------


def test_holding_h_years_exits_on_index_h_minus_one() -> None:
    """Years 0 to h-1 are held, so the terminal value lands on the last of them."""
    assert exit_index(10) == 9
    assert exit_index(5) == 4


def test_the_exit_index_is_clamped_to_the_window() -> None:
    assert exit_index(YEARS) == YEARS - 1
    assert exit_index(YEARS + 10) == YEARS - 1


def test_a_hold_of_less_than_a_year_is_an_error() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        exit_index(0)
