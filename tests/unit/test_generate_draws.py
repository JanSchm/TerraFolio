"""The draw plan: the order and the count, against the reference's own record.

``js_prng.json`` records, for all 48 reference projects, which of the nine draws
were taken and how many that came to. Two of the nine are conditional, so a port
that reads a value but takes it anyway diverges from the first project -- and
does so silently, because the numbers stay plausible.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

import pytest

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.config.loader import load_default
from terrafolio.domain.enums import Stage
from terrafolio.generate.draws import project_stream, reference_stream
from terrafolio.generate.project import draw_project
from terrafolio.generate.sites import reference_sites

FIXTURES: Final = Path(__file__).resolve().parents[1] / "golden" / "fixtures"


@pytest.fixture(scope="module")
def assumptions() -> AssumptionSet:
    return load_default()


@pytest.fixture(scope="module")
def recorded() -> dict[str, Any]:
    spec = json.loads((FIXTURES / "js_prng.json").read_text(encoding="utf-8"))
    return {entry["id"]: entry for entry in spec["projectSeeds"]}


def test_the_reference_seeds_each_project_from_its_index(recorded: dict[str, Any]) -> None:
    """``i * 97 + 13`` -- recorded per project, so the stride is pinned not assumed."""
    for index, site in enumerate(reference_sites()):
        assert recorded[site.id]["seed"] == index * 97 + 13


def test_every_project_consumes_the_recorded_number_of_draws(
    recorded: dict[str, Any], assumptions: AssumptionSet
) -> None:
    for index, site in enumerate(reference_sites()):
        drawn = draw_project(site, reference_stream(index), assumptions)
        assert drawn.draws_consumed == recorded[site.id]["drawsConsumed"], site.id


def test_the_conditional_draws_are_the_two_the_plan_names(
    recorded: dict[str, Any], assumptions: AssumptionSet
) -> None:
    """Seven, eight or nine draws, and which depends on stage.

    A greenfield project draws for its grid connection; anything past greenfield
    has one by definition. Anything not yet under construction draws for its O&M
    agreement; a construction project has one.
    """
    for index, site in enumerate(reference_sites()):
        expected = 7
        if site.stage is Stage.GREENFIELD:
            expected += 1
        if site.stage is not Stage.CONSTRUCTION:
            expected += 1
        drawn = draw_project(site, reference_stream(index), assumptions)
        assert drawn.draws_consumed == expected == recorded[site.id]["drawsConsumed"], site.id


def test_the_recorded_plan_agrees_with_the_conditions_we_implement(
    recorded: dict[str, Any],
) -> None:
    """Read the fixture's own `drawPlan` rather than only its total.

    The count alone would pass if two conditions were swapped.
    """
    for site in reference_sites():
        plan = {entry["field"]: entry["drawn"] for entry in recorded[site.id]["drawPlan"]}
        assert plan["gridSecured"] is (site.stage is Stage.GREENFIELD), site.id
        assert plan["omPartner"] is (site.stage is not Stage.CONSTRUCTION), site.id
        for always in ("cf", "opexKw", "devRisk", "ppaShare", "ppaPrice", "yieldTarget"):
            assert plan[always] is True, f"{site.id} {always}"


# --------------------------------------------------------------------------
# The shipped stream
# --------------------------------------------------------------------------


def test_a_project_stream_depends_on_the_id_and_the_calibration() -> None:
    """The property the whole seeding design exists for."""
    first = [project_stream("P001", "0011223344556677")() for _ in range(4)]
    again = [project_stream("P001", "0011223344556677")() for _ in range(4)]
    other_id = [project_stream("P002", "0011223344556677")() for _ in range(4)]
    other_set = [project_stream("P001", "7766554433221100")() for _ in range(4)]
    assert first == again
    assert first != other_id
    assert first != other_set


def test_neighbouring_ids_are_not_correlated() -> None:
    """What a naive ``seed + i`` scheme gets wrong.

    The reference needs a ``* 97`` stride precisely because xorshift32 seeded
    with adjacent numbers starts out correlated. SeedSequence mixes properly, so
    ``P001`` and ``P002`` share nothing.
    """
    streams = [
        [project_stream(f"P{n:03d}", "abcdef0123456789")() for _ in range(6)] for n in range(1, 9)
    ]
    for index, first in enumerate(streams):
        for second in streams[index + 1 :]:
            assert first != second


def test_an_attempt_gives_a_different_stream_for_the_same_project() -> None:
    """The bounded resample, without disturbing any other project."""
    base = [project_stream("P001", "abcdef0123456789", 0)() for _ in range(4)]
    retry = [project_stream("P001", "abcdef0123456789", 1)() for _ in range(4)]
    assert base != retry
    assert base == [project_stream("P001", "abcdef0123456789", 0)() for _ in range(4)]


def test_the_stream_does_not_use_python_s_salted_hash() -> None:
    """A per-process salt would make a pipeline irreproducible between runs.

    Asserted across a real subprocess boundary, because that is the only place
    ``PYTHONHASHSEED`` actually bites.
    """
    script = (
        "from terrafolio.generate.draws import project_stream;"
        "print([project_stream('P001', 'abcdef0123456789')() for _ in range(3)])"
    )
    runs = {
        subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
        ).stdout
        for seed in ("0", "1", "12345")
    }
    assert len(runs) == 1
