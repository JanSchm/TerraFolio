"""``WarningCode``: the enum ordering must be spec §5.4's severity ordering.

§5.4 lists the warnings "in this order of severity", so the enum's own ordering
carries that fact and ``sorted(codes)`` is display order. The integers are
internal: every boundary carries the name.
"""

from __future__ import annotations

import json
from itertools import pairwise
from typing import Final

import pytest

from terrafolio.domain.enums import SEVERITY_ORDER, WarningCode, WarningSeverity
from terrafolio.domain.results import FeasibilityWarning

SPEC_5_4_ORDER: Final[tuple[str, ...]] = (
    # "no candidate passes the screens — the run button is disabled"
    "NO_CANDIDATES",
    # §13 / ui-contract §3.6: locked projects alone exceed available capital.
    # Blocking for the same reason, and the API returns 422 for it.
    "LOCKS_EXCEED_CAPITAL",
    # "eligible capacity is below the capacity target"
    "CAPACITY_BELOW_TARGET",
    # "the minimum leverage exceeds what the eligible pool can support"
    "LEVERAGE_UNREACHABLE",
    # "the solar target is more than 20 points away from the pool's own mix"
    "SOLAR_MIX_UNREACHABLE",
    # "the eligible pool absorbs less than 90% of available capital"
    "CAPITAL_UNDERUSED",
    # "projects are locked or excluded — an informational count"
    "LOCKS_PRESENT",
)


def test_enum_order_is_severity_order() -> None:
    assert [code.name for code in sorted(WarningCode)] == list(SPEC_5_4_ORDER)


def test_the_written_order_and_the_values_agree() -> None:
    """Redundant on purpose: a reorder must change both, or fail here."""
    assert sorted(WarningCode) == list(SEVERITY_ORDER)


def test_every_code_is_ranked() -> None:
    """A code added without a considered place in §5.4's order fails here."""
    assert set(SEVERITY_ORDER) == set(WarningCode)
    assert len(SEVERITY_ORDER) == len(WarningCode)


def test_the_most_severe_comes_first() -> None:
    assert sorted(WarningCode)[0] is WarningCode.NO_CANDIDATES


def test_only_two_codes_stop_a_run() -> None:
    """Everything else is advisory: §5.4 lets the user run an infeasible mandate."""
    blocking = {code for code in WarningCode if code.disables_run}
    assert blocking == {WarningCode.NO_CANDIDATES, WarningCode.LOCKS_EXCEED_CAPITAL}


@pytest.mark.parametrize("code", sorted(WarningCode))
def test_every_code_has_a_wire_severity(code: WarningCode) -> None:
    """``docs/api.md`` allows two: alert and info."""
    assert code.severity in (WarningSeverity.ALERT, WarningSeverity.INFO)


def test_blocking_is_not_a_severity() -> None:
    """A blocking warning is still ``alert`` on the wire; ``runnable`` carries it."""
    assert WarningCode.NO_CANDIDATES.severity is WarningSeverity.ALERT


def test_inserting_a_code_would_not_renumber_its_neighbours() -> None:
    """Values are banded with gaps, so a later insertion is additive."""
    values = [code.value for code in sorted(WarningCode)]
    gaps = [later - earlier for earlier, later in pairwise(values)]
    assert gaps and all(gap >= 10 for gap in gaps)


# --------------------------------------------------------------------------
# The ordinal never leaves the process
# --------------------------------------------------------------------------


def test_a_warning_serialises_by_name() -> None:
    """Runs are stored indefinitely; a shifting ordinal would rewrite their meaning."""
    warning = FeasibilityWarning(
        code=WarningCode.CAPACITY_BELOW_TARGET,
        severity=WarningSeverity.ALERT,
        message="Eligible pipeline is 1,180 MW — below the 1,500 MW target.",
    )
    payload = json.loads(warning.model_dump_json())
    assert payload["code"] == "CAPACITY_BELOW_TARGET"


def test_no_stored_artefact_carries_the_ordinal() -> None:
    for code in WarningCode:
        warning = FeasibilityWarning(code=code, severity=code.severity, message="x")
        assert str(code.value) not in warning.model_dump_json()


def test_a_warning_round_trips_through_its_name() -> None:
    warning = FeasibilityWarning(
        code=WarningCode.LOCKS_PRESENT, severity=WarningSeverity.INFO, message="x"
    )
    again = FeasibilityWarning.model_validate(json.loads(warning.model_dump_json()))
    assert again.code is WarningCode.LOCKS_PRESENT


def test_an_unknown_code_names_the_ones_that_exist() -> None:
    with pytest.raises(ValueError, match="NO_CANDIDATES"):
        FeasibilityWarning.model_validate({"code": "MADE_UP", "severity": "info", "message": "x"})
