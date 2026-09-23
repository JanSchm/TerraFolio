"""The two interpretations the specification leaves open, pinned against 1C's oracle.

§9.4 does not settle two things, and both change reported returns:

1. Does the exit year's **own** FCFE count alongside the terminal value?
2. What is "PV of opex" discounted *from*, and does the opex escalate?

Issue #8 asks for a sweep that reports the single matching combination. The sweep
was run; what ships is the **pin**, which is the durable half of it: it asserts
that the combination in ``assumptions/default-2026.toml`` reproduces
``derived_expectations.json`` and that every alternative does not.

**It exercises 2A's production code, not a second implementation.** The flags are
read by :mod:`terrafolio.economics.lcoe` and
:mod:`terrafolio.economics.returns`, which is where the optimiser reads them, so
a pin against anything else would leave the shipped path untested. 2A reached
the same LCOE answer independently (2A-4 and A-24) — this is what keeps it true.

Measured, over 48 projects and three exit-multiple bases:

=========================================  ==========================================
combination                                result
=========================================  ==========================================
``real_from_base``                         **48/48 LCOEs**
``real_from_cod``                          10/48, missing by up to 16 EUR/MWh
``nominal_from_base``                      0/48
``exit_year_fcfe_included = true``         **144/144 IRRs and MOICs**
  set to false                             0/144
=========================================  ==========================================

Note that neither of the two readings issue #8 names is the one that matches. It
offers "real opex from COD" and "nominal opex from the base year"; what
reproduces the corpus is unescalated opex discounted from the **base year**, a
third reading. Recorded as A-24 rather than quietly renamed.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Final

import numpy as np
import pytest

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.config.loader import load_default
from terrafolio.domain.conventions import EUR_PER_EUR_MILLION
from terrafolio.domain.enums import Technology
from terrafolio.economics.irr import irr
from terrafolio.economics.lcoe import NOMINAL_FROM_BASE, REAL_FROM_BASE, REAL_FROM_COD, lcoe
from terrafolio.economics.returns import hold_truncated_fcfe, moic
from terrafolio.model.rounding import js_round
from terrafolio.pipeline.arrays import ProjectArrays
from terrafolio.pipeline.loader import load_pipeline

FIXTURES: Final = Path(__file__).resolve().parent / "fixtures"

PINNED_LCOE_BASIS: Final = REAL_FROM_BASE
PINNED_EXIT_YEAR_FCFE_INCLUDED: Final = True
ALL_BASES: Final = (REAL_FROM_BASE, REAL_FROM_COD, NOMINAL_FROM_BASE)

ULP: Final = 1e-12
"""Tolerance for the hold series, IRR and MOIC, and why it is not zero.

The reference re-amortises the debt in a *third* independent loop to compute the
exit bridge, and its answer sits an ulp from the ``debtSchedule.closing`` in the
same file. Both implementations here read the schedule, because §7.4 ties it out
and §9 forbids two sources for one number — so a handful of oracle cells differ
in their last bit (A-31). LCOE is compared **exactly**, after the rounding §7.4
displays it with; it has no such second derivation.
"""


@pytest.fixture(scope="module")
def assumptions() -> AssumptionSet:
    return load_default()


@pytest.fixture(scope="module")
def oracle() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / "derived_expectations.json").read_text(encoding="utf-8")
    )
    assert data["holdYears"] == 10
    assert len(data["projects"]) == 48
    return data


@pytest.fixture(scope="module")
def arrays(assumptions: AssumptionSet) -> ProjectArrays:
    """The 48 golden files through **2A's loader**, in canonical order."""
    return load_pipeline(FIXTURES / "pipeline", assumptions).arrays


def test_the_loader_returns_the_oracle_s_own_order(
    arrays: ProjectArrays, oracle: dict[str, Any]
) -> None:
    """Everything below compares by position, so this is not a formality."""
    assert list(arrays.ids) == [entry["id"] for entry in oracle["projects"]]


def tuned(
    assumptions: AssumptionSet, *, basis: str | None = None, included: bool | None = None
) -> AssumptionSet:
    interpretation = assumptions.interpretation
    if basis is not None:
        interpretation = dataclasses.replace(interpretation, lcoe_opex_basis=basis)
    if included is not None:
        interpretation = dataclasses.replace(interpretation, exit_year_fcfe_included=included)
    return dataclasses.replace(assumptions, interpretation=interpretation)


def rebased(assumptions: AssumptionSet, base: float) -> AssumptionSet:
    """Shift the technology multiples so their solar figure is ``base``.

    The oracle gives every quantity under bases 8.5, 9 and 9.5. The assumption
    set states the three technology multiples directly, calibrated at 9, so the
    base is moved by shifting all three together rather than by rewriting them.
    """
    offset = base - assumptions.exit_multiples[Technology.SOLAR]
    return dataclasses.replace(
        assumptions,
        exit_multiples={
            technology: multiple + offset
            for technology, multiple in assumptions.exit_multiples.items()
        },
    )


def lcoe_matches(arrays: ProjectArrays, assumptions: AssumptionSet, oracle: dict[str, Any]) -> int:
    """How many of the 48 reference LCOEs a basis reproduces.

    Compared after rounding to a whole EUR/MWh. The core returns the unrounded
    figure — §14's formats belong to the display boundary — and the reference
    stores what it displayed.
    """
    computed = np.array([js_round(value) for value in lcoe(arrays, assumptions)])
    expected = np.array([entry["lcoeEURPerMWh"] for entry in oracle["projects"]], dtype=float)
    return int(np.sum(computed == expected))


def hold_cases(
    arrays: ProjectArrays, assumptions: AssumptionSet, oracle: dict[str, Any]
) -> tuple[int, int]:
    """IRRs and MOICs within an ulp, across all 48 projects x 3 exit bases."""
    hold = oracle["holdYears"]
    irrs = moics = 0
    for key in ("9", "8.5", "9.5"):
        cases = [entry["byExitMultipleBase"][key] for entry in oracle["projects"]]
        at_base = rebased(assumptions, cases[0]["base"])
        series = hold_truncated_fcfe(arrays, at_base, hold)
        rates, _defined = irr(series)
        irrs += int(np.sum(np.abs(rates - np.array([c["irrAtHold"] for c in cases])) <= ULP))
        moics += int(
            np.sum(np.abs(moic(series) - np.array([c["moicAtHold"] for c in cases])) <= ULP)
        )
    return irrs, moics


# --------------------------------------------------------------------------
# The pin
# --------------------------------------------------------------------------


def test_the_assumption_set_carries_the_pinned_combination(assumptions: AssumptionSet) -> None:
    assert assumptions.interpretation.lcoe_opex_basis == PINNED_LCOE_BASIS
    assert assumptions.interpretation.exit_year_fcfe_included is PINNED_EXIT_YEAR_FCFE_INCLUDED


def test_the_pinned_lcoe_basis_reproduces_every_reference_lcoe(
    arrays: ProjectArrays, assumptions: AssumptionSet, oracle: dict[str, Any]
) -> None:
    assert lcoe_matches(arrays, assumptions, oracle) == 48


def test_the_pinned_exit_reading_reproduces_every_hold_case(
    arrays: ProjectArrays, assumptions: AssumptionSet, oracle: dict[str, Any]
) -> None:
    assert hold_cases(arrays, assumptions, oracle) == (144, 144)


def test_the_hold_series_itself_reproduces_the_oracle(
    arrays: ProjectArrays, assumptions: AssumptionSet, oracle: dict[str, Any]
) -> None:
    """The cash flow, not only the ratios derived from it.

    In **euros** in the core and €m in the oracle, which is the conversion epic
    §5 confines to two boundaries.
    """
    series = hold_truncated_fcfe(arrays, assumptions, oracle["holdYears"])
    expected = np.array(
        [
            entry["byExitMultipleBase"]["9"]["holdTruncatedFcfeWithTerminalValue"]
            for entry in oracle["projects"]
        ]
    )
    assert np.allclose(series / EUR_PER_EUR_MILLION, expected, rtol=0, atol=ULP)


# --------------------------------------------------------------------------
# ...and the alternatives really are wrong
# --------------------------------------------------------------------------


@pytest.mark.parametrize("basis", [b for b in ALL_BASES if b != PINNED_LCOE_BASIS])
def test_every_other_lcoe_basis_fails(
    basis: str, arrays: ProjectArrays, assumptions: AssumptionSet, oracle: dict[str, Any]
) -> None:
    """A pin means nothing unless the alternatives are shown to miss.

    Both of these are the readings issue #8 actually names, which is the point:
    the one that matches is a third one.
    """
    assert lcoe_matches(arrays, tuned(assumptions, basis=basis), oracle) < 48


def test_excluding_the_exit_year_fcfe_fails_everywhere(
    arrays: ProjectArrays, assumptions: AssumptionSet, oracle: dict[str, Any]
) -> None:
    assert hold_cases(arrays, tuned(assumptions, included=False), oracle) == (0, 0)


def test_the_two_flags_together_admit_exactly_one_combination(
    arrays: ProjectArrays, assumptions: AssumptionSet, oracle: dict[str, Any]
) -> None:
    """The sweep's own claim: one of the six combinations reproduces the oracle."""
    winners = [
        (basis, included)
        for basis in ALL_BASES
        for included in (True, False)
        if lcoe_matches(arrays, tuned(assumptions, basis=basis), oracle) == 48
        and hold_cases(arrays, tuned(assumptions, included=included), oracle) == (144, 144)
    ]
    assert winners == [(PINNED_LCOE_BASIS, PINNED_EXIT_YEAR_FCFE_INCLUDED)]


# --------------------------------------------------------------------------
# The two cash-flow series stay apart (A-6)
# --------------------------------------------------------------------------


def test_the_thirty_year_series_never_carries_a_terminal_value(
    arrays: ProjectArrays, assumptions: AssumptionSet, oracle: dict[str, Any]
) -> None:
    """Epic §5 calls conflating them the most likely silent bug in the feature.

    The 30-year series in a file has no terminal value; the hold-truncated one
    does. Different lengths, different numbers, and neither is derived from the
    other by slicing.
    """
    hold = oracle["holdYears"]
    held = hold_truncated_fcfe(arrays, assumptions, hold)
    thirty = arrays.statements.cash_flow.fcfe
    assert held.shape[1] == hold
    assert thirty.shape[1] == 30
    assert np.all(held[:, -1] > thirty[:, hold - 1])
    assert np.array_equal(held[:, :-1], thirty[:, : hold - 1])
