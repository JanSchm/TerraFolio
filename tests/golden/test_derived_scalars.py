"""Every derived scalar, for all 48 projects, against 1C's oracle.

``derived_expectations.json`` was produced by running the JavaScript reference itself,
so this is the check that the Python port computes the same economics rather than
merely self-consistent economics. Issue #6 asks for 1e-9; most of these land at 1e-13
or better.

The oracle gives every figure under three exit-multiple bases — 8.5, 9 and 9.5, with
the technology-specific multiple the reference derives from each — so the exit
multiple is exercised as a configuration value rather than as the one number that
happens to ship.

Two deliberate divergences are asserted **as** divergences, with their measured size,
rather than hidden behind a loose tolerance:

* LCOE, where neither documented ``lcoe_opex_basis`` reproduces the reference (1C-10);
* the reference's 2 dp rounding and 3.2 cap on min DSCR, which the production screen
  does not apply (1C-4).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.config.loader import load_default
from terrafolio.domain.conventions import EUR_PER_EUR_MILLION
from terrafolio.domain.enums import Technology
from terrafolio.economics.irr import irr
from terrafolio.economics.lcoe import NOMINAL_FROM_BASE, REAL_FROM_BASE, REAL_FROM_COD, lcoe
from terrafolio.economics.returns import project_returns
from terrafolio.pipeline.arrays import ProjectArrays
from terrafolio.pipeline.derive import annual_generation_gwh, min_dscr
from terrafolio.pipeline.loader import load_pipeline

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TOLERANCE = 1e-9

ASSUMPTIONS = load_default()
LOADED = load_pipeline(FIXTURES / "pipeline", ASSUMPTIONS)
ARRAYS: ProjectArrays = LOADED.arrays

EXPECTED: dict[str, Any] = json.loads(
    (FIXTURES / "derived_expectations.json").read_text(encoding="utf-8")
)
BY_ID: dict[str, Any] = {project["id"]: project for project in EXPECTED["projects"]}
IN_ORDER: list[Any] = [BY_ID[project_id] for project_id in ARRAYS.ids]
HOLD: int = EXPECTED["holdYears"]

TECHNOLOGY_OFFSET = {
    Technology.SOLAR: 0.0,
    Technology.ONSHORE_WIND: -0.5,
    Technology.OFFSHORE_WIND: 0.5,
}
"""``exitMultipleByTechnology`` in the oracle: solar takes the base, onshore is half a
turn below it and offshore half a turn above."""


def _assumptions_at(base: float) -> AssumptionSet:
    """The shipped set with its exit multiples re-derived from ``base``."""
    multiples = {technology: base + offset for technology, offset in TECHNOLOGY_OFFSET.items()}
    return dataclasses.replace(ASSUMPTIONS, exit_multiples=multiples)


def _expected(key: str) -> np.ndarray:
    return np.array([project[key] for project in IN_ORDER], dtype=np.float64)


def _expected_at(base: str, key: str) -> np.ndarray:
    return np.array(
        [project["byExitMultipleBase"][base][key] for project in IN_ORDER], dtype=np.float64
    )


def _assert_matches(got: np.ndarray, want: np.ndarray, label: str) -> None:
    worst = float(np.nanmax(np.abs(np.asarray(got, dtype=np.float64) - want)))
    assert worst <= TOLERANCE, f"{label}: worst absolute difference {worst:.3e}"


def test_the_oracle_covers_the_whole_pipeline() -> None:
    """Guard the guard: a mismatched corpus would make every test here vacuous."""
    assert LOADED.loaded_count == 48
    assert set(BY_ID) == set(ARRAYS.ids)


# ---------------------------------------------------------------------------
# Mandate-independent
# ---------------------------------------------------------------------------


def test_total_capex_matches() -> None:
    _assert_matches(
        ARRAYS.capital.total_capex / EUR_PER_EUR_MILLION, _expected("capexEURm"), "capex"
    )


def test_equity_matches() -> None:
    _assert_matches(ARRAYS.capital.equity / EUR_PER_EUR_MILLION, _expected("equityEURm"), "equity")


def test_gearing_matches() -> None:
    _assert_matches(ARRAYS.capital.gearing, _expected("gearing"), "gearing")


def test_capex_per_kw_matches() -> None:
    _assert_matches(ARRAYS.capex_per_kw, _expected("capexPerKW"), "capexPerKw")


def test_annual_generation_matches() -> None:
    """The P50 full-year figure, recovered by undoing degradation."""
    _assert_matches(annual_generation_gwh(ARRAYS), _expected("p50GenerationGWh"), "p50Generation")


def test_min_dscr_matches_the_raw_figure() -> None:
    """``minDSCRRaw``, not ``minDSCRReference``: production does not round or cap."""
    _assert_matches(min_dscr(ARRAYS), _expected("minDSCRRaw"), "minDscr")


def test_min_dscr_excludes_the_ramp_year() -> None:
    """The oracle records the exclusion as a flag; this asserts it was honoured.

    Including the ramp year would pull the minimum below the recorded figure for every
    levered project, because the first operating year runs at 55% of full output.
    """
    assert all(project["minDSCRExcludesRampYear"] for project in IN_ORDER)
    lowest = min_dscr(ARRAYS)
    levered = ~np.isnan(lowest)
    assert (lowest[levered] >= 1.0).all()


def test_min_dscr_diverges_from_the_reference_only_by_its_rounding() -> None:
    """1C-4: the reference screens on ``round(min(raw, 3.2), 2)``. We screen on raw.

    Asserted as a divergence with a bound, not tolerated silently: if the two ever
    differed by more than a 2 dp rounding, something other than the rounding changed.
    """
    raw = min_dscr(ARRAYS)
    reference = _expected("minDSCRReference")
    cap = _expected("minDSCRCap")
    levered = ~np.isnan(raw)
    rounded = np.round(np.minimum(raw[levered], cap[levered]), 2)
    assert np.allclose(rounded, reference[levered], rtol=0, atol=TOLERANCE)


# ---------------------------------------------------------------------------
# Mandate-dependent, at each exit-multiple base
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base", ["8.5", "9", "9.5"])
def test_equity_irr_matches_at_every_exit_base(base: str) -> None:
    returns = project_returns(ARRAYS, _assumptions_at(float(base)), HOLD)
    assert returns.defined.all(), "the oracle records no undefined IRR at hold 10"
    _assert_matches(returns.equity_irr, _expected_at(base, "irrAtHold"), f"irr@{base}")


@pytest.mark.parametrize("base", ["8.5", "9", "9.5"])
def test_moic_matches_at_every_exit_base(base: str) -> None:
    returns = project_returns(ARRAYS, _assumptions_at(float(base)), HOLD)
    _assert_matches(returns.moic, _expected_at(base, "moicAtHold"), f"moic@{base}")


@pytest.mark.parametrize("base", ["8.5", "9", "9.5"])
def test_the_hold_truncated_series_matches_element_by_element(base: str) -> None:
    """Not just its sum: the terminal value has to land in the right year."""
    returns = project_returns(ARRAYS, _assumptions_at(float(base)), HOLD)
    want = np.array(
        [
            project["byExitMultipleBase"][base]["holdTruncatedFcfeWithTerminalValue"]
            for project in IN_ORDER
        ],
        dtype=np.float64,
    )
    got = returns.series / EUR_PER_EUR_MILLION
    assert got.shape == want.shape
    worst = float(np.abs(got - want).max())
    assert worst <= TOLERANCE, f"hold series @{base}: worst {worst:.3e}"


def test_the_shipped_exit_multiples_are_the_oracles_base_of_nine() -> None:
    """The default set is not an arbitrary point in the sweep — it is the reference's."""
    for technology, offset in TECHNOLOGY_OFFSET.items():
        assert ASSUMPTIONS.exit_multiples[technology] == pytest.approx(9.0 + offset)


def test_the_thirty_year_irr_without_a_terminal_value_matches() -> None:
    """The oracle's ``irr30yNoTerminalValue`` — the other series, and a direct check
    that the terminal value really is absent from the 30-year one."""
    rates, defined = irr(ARRAYS.statements.cash_flow.fcfe)
    assert defined.all()
    _assert_matches(rates, _expected("irr30yNoTerminalValue"), "irr30y")


# ---------------------------------------------------------------------------
# LCOE — a measured divergence, not a silent one
# ---------------------------------------------------------------------------


def _lcoe_under(basis: str) -> np.ndarray:
    assumptions = dataclasses.replace(
        ASSUMPTIONS,
        interpretation=dataclasses.replace(ASSUMPTIONS.interpretation, lcoe_opex_basis=basis),
    )
    return np.asarray(lcoe(ARRAYS, assumptions))


def test_the_reference_lcoe_basis_is_real_opex_discounted_from_the_base_year() -> None:
    """1C-10 warned that the reference's basis is neither documented option.

    It is: unescalated opex discounted from the **base year**, with capex undiscounted.
    That reproduces all 48 of the oracle's integers exactly, which is what makes the
    divergence of the shipped default a configuration difference rather than a bug.
    """
    rounded = np.round(_lcoe_under(REAL_FROM_BASE))
    assert np.array_equal(rounded, _expected("lcoeEURPerMWh"))


def test_the_shipped_basis_diverges_from_the_reference_by_a_bounded_amount() -> None:
    """``real_from_cod`` is what #6 specifies and what the assumption set selects.

    Discounting from COD rather than from the base year raises LCOE, because a
    project's generation is no longer discounted through its construction years. The
    bound is asserted so the gap is a recorded quantity rather than folklore.
    """
    shipped = _lcoe_under(REAL_FROM_COD)
    reference = _expected("lcoeEURPerMWh")
    difference = np.abs(shipped - reference)
    assert difference.mean() < 5.0
    assert difference.max() < 20.0
    assert (shipped > 0.0).all()


def test_an_unknown_lcoe_basis_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ValueError, match="lcoe_opex_basis"):
        _lcoe_under("whatever_the_analyst_typed")


def test_every_documented_basis_produces_a_positive_cost() -> None:
    for basis in (REAL_FROM_COD, REAL_FROM_BASE, NOMINAL_FROM_BASE):
        values = _lcoe_under(basis)
        assert np.isfinite(values).all()
        assert (values > 0.0).all()
