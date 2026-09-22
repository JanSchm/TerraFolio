"""The two interpretations the specification leaves open, pinned against 1C's oracle.

§9.4 does not settle two things, and both change reported returns:

1. Does the exit year's **own** FCFE count alongside the terminal value?
2. What is "PV of opex" discounted *from*, and does the opex escalate?

Issue #8 asks for a sweep that reports the single matching combination. The
sweep was run; what ships is the **pin**, which is the durable half of it: it
asserts that the combination in ``assumptions/default-2026.toml`` reproduces
``derived_expectations.json`` and that every alternative does not. A search
re-run forever to rediscover an answer already written down is not worth its
runtime; a guard that fails when someone flips a flag is.

Measured, over 48 projects and three exit-multiple bases:

===========================  ===========================================
combination                  result
===========================  ===========================================
``real_from_base_year``      **48/48 LCOEs exact**
``real_from_cod``            10/48, missing by up to 16 EUR/MWh
``nominal_from_base_year``   0/48
``exit_year_fcfe_included``  **144/144 IRRs and MOICs, within an ulp**
  set to false               0/144
===========================  ===========================================

Note that neither of the two readings issue #8 names is the one that matches.
The issue offers "real opex from COD" and "nominal opex from the base year"; the
reference computes unescalated opex discounted from the **base year**, which is
a third reading. It is pinned because it is what reproduces the corpus, and
recorded in ``docs/decisions.md`` as A-24 rather than quietly renamed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import numpy as np
import pytest

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.config.loader import load_default
from terrafolio.domain.enums import Technology
from terrafolio.generate.from_file import inputs_from_file
from terrafolio.model.project import ProjectStatements, project_statements
from terrafolio.model.returns import (
    LCOE_BASES,
    exit_multiple,
    hold_truncated_fcfe,
    irr,
    lcoe,
    moic,
)

FIXTURES: Final = Path(__file__).resolve().parent / "fixtures"

TECHNOLOGY: Final = {
    "Solar": Technology.SOLAR,
    "Wind": Technology.ONSHORE_WIND,
    "Offshore wind": Technology.OFFSHORE_WIND,
}

PINNED_LCOE_BASIS: Final = "real_from_base_year"
PINNED_EXIT_YEAR_FCFE_INCLUDED: Final = True

ULP: Final = 1e-12
"""The tolerance for the hold-truncated series, IRR and MOIC, and why it is not zero.

The reference re-amortises the debt in a *third* independent loop to compute the
exit bridge, and its answer sits an ulp from the ``debtSchedule.closing`` in the
same file. This implementation reads the schedule, because §7.4 ties it out and
§9 forbids two sources for one number -- so 35 of 1,440 oracle cells differ in
their last bit. LCOE and the 30-year IRR are compared **exactly**; they have no
such second derivation.
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
def files() -> dict[str, dict[str, Any]]:
    return {
        path.stem.split("-")[0]: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((FIXTURES / "pipeline").glob("*.json"))
    }


@pytest.fixture(scope="module")
def modelled(
    oracle: dict[str, Any], files: dict[str, dict[str, Any]], assumptions: AssumptionSet
) -> dict[str, ProjectStatements]:
    return {
        entry["id"]: project_statements(inputs_from_file(files[entry["id"]], assumptions))
        for entry in oracle["projects"]
    }


def lcoe_matches(
    basis: str,
    oracle: dict[str, Any],
    files: dict[str, dict[str, Any]],
    modelled: dict[str, ProjectStatements],
    assumptions: AssumptionSet,
) -> int:
    """How many of the 48 reference LCOEs a basis reproduces exactly."""
    return sum(
        lcoe(
            inputs_from_file(files[entry["id"]], assumptions),
            modelled[entry["id"]],
            assumptions,
            basis=basis,
        )
        == entry["lcoeEURPerMWh"]
        for entry in oracle["projects"]
    )


def hold_cases(
    included: bool,
    oracle: dict[str, Any],
    modelled: dict[str, ProjectStatements],
    assumptions: AssumptionSet,
) -> tuple[int, int]:
    """How many of the 144 (project x exit base) IRRs and MOICs land within an ulp."""
    irrs = moics = 0
    for entry in oracle["projects"]:
        statements = modelled[entry["id"]]
        for case in entry["byExitMultipleBase"].values():
            multiple = exit_multiple(TECHNOLOGY[entry["technology"]], case["base"], assumptions)
            series = hold_truncated_fcfe(
                statements,
                hold_years=oracle["holdYears"],
                multiple=multiple,
                exit_year_fcfe_included=included,
            )
            irrs += abs(irr(series, assumptions) - case["irrAtHold"]) <= ULP
            moics += abs(moic(series) - case["moicAtHold"]) <= ULP
    return irrs, moics


# --------------------------------------------------------------------------
# The pin
# --------------------------------------------------------------------------


def test_the_assumption_set_carries_the_pinned_combination(assumptions: AssumptionSet) -> None:
    """What ships is what the sweep chose."""
    assert assumptions.interpretation.lcoe_opex_basis == PINNED_LCOE_BASIS
    assert assumptions.interpretation.exit_year_fcfe_included is PINNED_EXIT_YEAR_FCFE_INCLUDED


def test_the_pinned_lcoe_basis_reproduces_every_reference_lcoe(
    oracle: dict[str, Any],
    files: dict[str, dict[str, Any]],
    modelled: dict[str, ProjectStatements],
    assumptions: AssumptionSet,
) -> None:
    """Exactly -- LCOE is rounded to a whole EUR/MWh, so there is no tolerance to hide in."""
    assert lcoe_matches(PINNED_LCOE_BASIS, oracle, files, modelled, assumptions) == 48


def test_the_default_basis_is_used_when_none_is_named(
    oracle: dict[str, Any],
    files: dict[str, dict[str, Any]],
    modelled: dict[str, ProjectStatements],
    assumptions: AssumptionSet,
) -> None:
    """The flag is read, not bypassed: a caller that passes nothing gets the pin."""
    entry = oracle["projects"][0]
    from_flag = lcoe(
        inputs_from_file(files[entry["id"]], assumptions), modelled[entry["id"]], assumptions
    )
    assert from_flag == entry["lcoeEURPerMWh"]


def test_the_pinned_exit_reading_reproduces_every_hold_case(
    oracle: dict[str, Any], modelled: dict[str, ProjectStatements], assumptions: AssumptionSet
) -> None:
    irrs, moics = hold_cases(PINNED_EXIT_YEAR_FCFE_INCLUDED, oracle, modelled, assumptions)
    assert (irrs, moics) == (144, 144)


def test_the_thirty_year_irr_reproduces_the_oracle_exactly(
    oracle: dict[str, Any], modelled: dict[str, ProjectStatements]
) -> None:
    """No terminal value, so no second derivation of the debt balance, so no ulp.

    This is what says the bisection itself is right rather than merely close --
    including that the NPV is summed in order, as the reference sums it.
    """
    assumptions = load_default()
    for entry in oracle["projects"]:
        assert irr(modelled[entry["id"]].fcfe, assumptions) == entry["irr30yNoTerminalValue"], (
            entry["id"]
        )


# --------------------------------------------------------------------------
# ...and that the alternatives really are wrong
# --------------------------------------------------------------------------


@pytest.mark.parametrize("basis", [b for b in LCOE_BASES if b != PINNED_LCOE_BASIS])
def test_every_other_lcoe_basis_fails(
    basis: str,
    oracle: dict[str, Any],
    files: dict[str, dict[str, Any]],
    modelled: dict[str, ProjectStatements],
    assumptions: AssumptionSet,
) -> None:
    """A pin is only meaningful if the alternatives are shown to miss.

    Both alternatives are the readings issue #8 actually names, which is the
    point: the one that matches is a third one.
    """
    assert lcoe_matches(basis, oracle, files, modelled, assumptions) < 48


def test_excluding_the_exit_year_fcfe_fails_everywhere(
    oracle: dict[str, Any], modelled: dict[str, ProjectStatements], assumptions: AssumptionSet
) -> None:
    irrs, moics = hold_cases(not PINNED_EXIT_YEAR_FCFE_INCLUDED, oracle, modelled, assumptions)
    assert (irrs, moics) == (0, 0)


def test_the_two_flags_together_admit_exactly_one_combination(
    oracle: dict[str, Any],
    files: dict[str, dict[str, Any]],
    modelled: dict[str, ProjectStatements],
    assumptions: AssumptionSet,
) -> None:
    """The sweep's own claim: one combination of the six reproduces the oracle."""
    winners = [
        (basis, included)
        for basis in LCOE_BASES
        for included in (True, False)
        if lcoe_matches(basis, oracle, files, modelled, assumptions) == 48
        and hold_cases(included, oracle, modelled, assumptions) == (144, 144)
    ]
    assert winners == [(PINNED_LCOE_BASIS, PINNED_EXIT_YEAR_FCFE_INCLUDED)]


# --------------------------------------------------------------------------
# The two series stay apart (A-6)
# --------------------------------------------------------------------------


def test_the_thirty_year_series_never_carries_a_terminal_value(
    oracle: dict[str, Any], modelled: dict[str, ProjectStatements], assumptions: AssumptionSet
) -> None:
    """The single most likely silent bug in the feature, per epic §5.

    The 30-year series in a file has no terminal value; the hold-truncated one
    does. They are different lengths and different numbers, and neither is
    derived from the other by slicing.
    """
    for entry in oracle["projects"]:
        statements = modelled[entry["id"]]
        case = entry["byExitMultipleBase"]["9"]
        held = hold_truncated_fcfe(
            statements,
            hold_years=oracle["holdYears"],
            multiple=exit_multiple(TECHNOLOGY[entry["technology"]], case["base"], assumptions),
            exit_year_fcfe_included=True,
        )
        assert held.size == oracle["holdYears"]
        assert statements.fcfe.size == 30
        assert held[-1] > statements.fcfe[held.size - 1]
        assert np.array_equal(held[:-1], statements.fcfe[: held.size - 1])


def test_an_undefined_irr_is_nan_not_zero(assumptions: AssumptionSet) -> None:
    """§13 and epic §5: an em dash, never a zero, never a sentinel.

    An all-positive series has no sign change in the bracket, so it has no IRR.
    """
    assert np.isnan(irr(np.ones(5, dtype=np.float64), assumptions))
    assert np.isnan(irr(-np.ones(5, dtype=np.float64), assumptions))
    assert np.isnan(moic(np.ones(3, dtype=np.float64)))
