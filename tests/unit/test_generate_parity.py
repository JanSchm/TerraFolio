"""Seeded to match the reference, the generator reproduces 1C's 48 files.

Issue #8 calls this "the parity test for this whole issue", and it is: it
exercises the PRNG, the draw plan and its two conditional draws, every formula
in the entry pricing and the debt sizing, the whole 30-year model, and the
serialiser, against an oracle produced by a different implementation in a
different language.

**What "field for field" means here.** Every leaf outside ``provenance`` is
compared with ``==``, not a tolerance: 38,880 leaves across the 48 files, and
the key sets must match exactly in both directions so that a missing or extra
field fails rather than passes quietly.

``provenance`` is excluded, and deliberately. It records *who produced a file
and how firm each number is*, and these files were produced by the house model,
not by 1C's extractor -- so ``preparedBy`` and ``modelVersion`` name the house
model, and the estimate bases follow the stage scheme issue #8 specifies rather
than the flat scheme 1C used. Copying 1C's notes across to make the comparison
total would put false provenance in every generated file. The exclusion is
asserted to be exactly that block, and its size is pinned, so it cannot quietly
widen to cover a number.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.config.loader import load_default
from terrafolio.generate.pipeline import as_file, reference_pipeline

FIXTURES: Final = Path(__file__).resolve().parents[1] / "golden" / "fixtures" / "pipeline"

EXPECTED_PROJECTS: Final = 48
EXPECTED_LEAVES: Final = 38880
"""Pinned so that a comparison which silently stopped comparing fails."""


def leaves(node: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    """Every scalar in a nested structure, with its dotted path."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaves(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from leaves(value, f"{path}[{index}]")
    else:
        yield path, node


@pytest.fixture(scope="module")
def assumptions() -> AssumptionSet:
    return load_default()


@pytest.fixture(scope="module")
def paired(assumptions: AssumptionSet) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Each generated file alongside the committed golden file of the same id."""
    golden = {
        path.stem.split("-")[0]: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(FIXTURES.glob("*.json"))
    }
    assert len(golden) == EXPECTED_PROJECTS
    return [
        (built.site.id, as_file(built, assumptions), golden[built.site.id])
        for built in reference_pipeline(assumptions)
    ]


def test_the_same_projects_are_produced(
    paired: list[tuple[str, dict[str, Any], dict[str, Any]]],
) -> None:
    assert len(paired) == EXPECTED_PROJECTS


def test_the_key_set_matches_exactly_in_both_directions(
    paired: list[tuple[str, dict[str, Any], dict[str, Any]]],
) -> None:
    """A closed template (§3): an extra key is as much a failure as a missing one."""
    for project_id, mine, reference in paired:
        missing = set(dict(leaves(reference))) - set(dict(leaves(mine)))
        extra = set(dict(leaves(mine))) - set(dict(leaves(reference)))
        assert not missing, f"{project_id} is missing {sorted(missing)[:5]}"
        assert not extra, f"{project_id} carries unexpected {sorted(extra)[:5]}"


def test_every_field_outside_provenance_is_identical(
    paired: list[tuple[str, dict[str, Any], dict[str, Any]]],
) -> None:
    """The parity criterion. ``==`` on every leaf, no tolerance anywhere."""
    compared = 0
    differences: list[str] = []
    for project_id, mine, reference in paired:
        right = dict(leaves(reference))
        for path, value in leaves(mine):
            if path.startswith(".provenance"):
                continue
            compared += 1
            if value != right[path]:
                differences.append(f"{project_id}{path}: {value!r} != {right[path]!r}")
    assert not differences, "\n".join(differences[:10])
    assert compared > 0


def test_the_comparison_really_did_cover_the_whole_file(
    paired: list[tuple[str, dict[str, Any], dict[str, Any]]],
) -> None:
    """Guards the test above against passing by comparing nothing."""
    total = sum(len(dict(leaves(mine))) for _, mine, _ in paired)
    assert total == EXPECTED_LEAVES


def test_only_provenance_differs_and_it_differs_for_a_stated_reason(
    paired: list[tuple[str, dict[str, Any], dict[str, Any]]],
) -> None:
    """The exclusion is a deliberate difference, so it is asserted to be present.

    If provenance ever *did* match, either the house model had adopted 1C's
    notes or this comparison had stopped looking -- both worth knowing.
    """
    differing: set[str] = set()
    for _, mine, reference in paired:
        right = dict(leaves(reference))
        differing |= {
            path
            for path, value in leaves(mine)
            if path.startswith(".provenance") and value != right[path]
        }
    assert ".provenance.preparedBy" in differing
    assert ".provenance.modelVersion" in differing
    assert any(path.endswith(".estimateBasis") for path in differing)


def test_the_generated_files_carry_no_derived_field(
    paired: list[tuple[str, dict[str, Any], dict[str, Any]]],
) -> None:
    """§9's reject list, checked on what is emitted rather than on what loads.

    ``ProjectFile`` already refuses these, but a generator that emitted one
    would fail at validation with no clue where it came from.
    """
    banned = {
        "irr",
        "moic",
        "terminalvalue",
        "exitvalue",
        "payback",
        "paybackyear",
        "mindscr",
        "lcoe",
        "leverage",
        "gearing",
        "equity",
        "capexperkw",
        "cashflowschedule",
    }
    for project_id, mine, _ in paired:
        for path, _value in leaves(mine):
            key = path.rsplit(".", 1)[-1].split("[")[0].casefold()
            assert key not in banned, f"{project_id} carries a derived field at {path}"
