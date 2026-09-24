"""1D's `feasibility.js` and 2A's `feasibility.py` answer the same question. Twice.

The mandate footer is computed client-side because §12 budgets it at under 100 ms, and
the run is screened server-side because that is where the pipeline lives. Two
implementations of §5.2's nine screens and §5.4's seven warnings, and if they drift the
footer promises a candidate count the run will not deliver — the worst possible bug in a
tool whose pitch is "every constraint enforced".

So: thirty committed pairs spanning every screen, every warning and four boundaries.
The node harness calls `web/js/feasibility.js` directly; pytest calls
`preview_feasibility`; the two answers are compared.

WHAT IS COMPARED EXACTLY, AND WHAT IS NOT
=========================================

Exactly: the eligible count, the total, `runnable`, the warning codes in order, the
eligible ids, and each project's failed screens. Those are the answer.

Within a tolerance: the six footer figures. Python aggregates in euros and converts at
the API boundary; the JavaScript aggregates in €m because that is what the wire carries.
`(x·10^6)/(y·10^6)` is not obliged to equal `x/y` to the last bit, and neither is a
pairwise sum obliged to equal a sequential one, so exact equality here would be
demanding something floating point does not offer. Recorded as docs/decisions.md 4B-5.

Both sides read the same projects: the committed payload is generated from these very
files by `api/scalars.py`, and `test_the_committed_payload_is_what_the_api_would_serve`
fails if it ever stops being.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pool import GOLDEN_PIPELINE
from wire import CONTINUOUS, JS_SCREEN_NAMES, parity_view, pipeline_payload

from terrafolio.config.loader import load_default
from terrafolio.domain.mandate import Mandate
from terrafolio.domain.reduce import mandate_to_scalars
from terrafolio.optimiser.feasibility import preview_feasibility
from terrafolio.optimiser.screens import SCREEN_NAMES
from terrafolio.pipeline.loader import load_pipeline

HERE = Path(__file__).parent
PAIRS_DIR = HERE / "pairs"
HARNESS = HERE / "harness.js"

RELATIVE_TOLERANCE = 1e-12
"""How far the two aggregations may differ. Five orders of magnitude tighter than any
figure the UI renders — €1,246.4162355799467m prints as `€1,246m` — and four wider than
the ~1e-16 a unit conversion and a different summation order can introduce."""

PAIRS = sorted(path for path in PAIRS_DIR.glob("*.json") if not path.name.startswith("payload-"))

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is needed to run web/js/feasibility.js")


def _pair(path: Path) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return parsed


def _payload(pair: dict[str, Any]) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads((PAIRS_DIR / pair["payload"]).read_text(encoding="utf-8"))
    return parsed


def _files_by_id() -> dict[str, Path]:
    return {
        json.loads(path.read_text(encoding="utf-8"))["id"]: path
        for path in GOLDEN_PIPELINE.glob("*.json")
    }


def _loaded_for(ids: list[str], tmp_path: Path) -> Any:
    """Load exactly the projects the payload carries, through the real loader.

    A subset is materialised as a directory rather than sliced out of `ProjectArrays`,
    because the loader is what derives `minDscr` from the statements and normalises the
    country codes — and a parity test that reimplemented either would be testing its own
    reimplementation.
    """
    catalogue = _files_by_id()
    if set(ids) == set(catalogue):
        return load_pipeline(GOLDEN_PIPELINE, load_default())
    subset = tmp_path / "pipeline"
    subset.mkdir(parents=True, exist_ok=True)
    for project_id in ids:
        shutil.copyfile(catalogue[project_id], subset / catalogue[project_id].name)
    return load_pipeline(subset, load_default())


def _python_view(pair: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    assumptions = load_default()
    payload = _payload(pair)
    ids = [project["id"] for project in payload["projects"]]
    loaded = _loaded_for(ids, tmp_path)
    scalars = mandate_to_scalars(Mandate.model_validate(pair["mandate"]))
    locks = pair["locks"]
    preview = preview_feasibility(
        loaded.arrays,
        scalars,
        assumptions,
        locked_ids=locks["lockedIds"],
        excluded_ids=locks["excludedIds"],
    )
    return parity_view(preview, ids=loaded.arrays.ids)


def _node_view(path: Path) -> dict[str, Any]:
    assert NODE is not None
    finished = subprocess.run(
        [NODE, str(HARNESS), str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert finished.returncode == 0, f"the harness failed:\n{finished.stderr}"
    parsed: dict[str, Any] = json.loads(finished.stdout)
    return parsed


EMPTY_POOL_RATIOS = ("eligibleSolarShare", "eligibleGearing")
"""The two figures the two sides deliberately spell differently when there is no pool.

Neither side is wrong and neither is mine to change, so the divergence is asserted
exactly rather than waved through — see
``test_an_empty_pool_says_no_mix_in_each_sides_own_vocabulary``.
"""


def _close(left: object, right: object) -> bool:
    first, second = (
        float(left if left is not None else math.nan),
        float(right if right is not None else math.nan),
    )
    if math.isnan(first) and math.isnan(second):
        return True
    return math.isclose(first, second, rel_tol=RELATIVE_TOLERANCE, abs_tol=0.0)


def test_there_are_thirty_pairs_covering_every_screen_and_every_warning() -> None:
    """The suite is only as good as its coverage, so the coverage is asserted."""
    assert len(PAIRS) == 30, f"expected thirty pairs, found {len(PAIRS)}"

    seen_screens: set[str] = set()
    seen_warnings: set[str] = set()
    for path in PAIRS:
        pair = _pair(path)
        assert pair["why"].strip(), f"{path.name} does not say what it is for"
        node = _node_view(path)
        seen_warnings.update(node["warnings"])
        for names in node["failedScreens"].values():
            seen_screens.update(names)

    assert seen_screens == set(SCREEN_NAMES), (
        f"screens never exercised: {sorted(set(SCREEN_NAMES) - seen_screens)}"
    )
    expected_warnings = {
        "NO_CANDIDATES",
        "LOCKS_EXCEED_CAPITAL",
        "CAPACITY_BELOW_TARGET",
        "LEVERAGE_UNREACHABLE",
        "SOLAR_MIX_UNREACHABLE",
        "CAPITAL_UNDERUSED",
        "LOCKS_PRESENT",
    }
    assert seen_warnings == expected_warnings, (
        f"warnings never exercised: {sorted(expected_warnings - seen_warnings)}"
    )


@pytest.mark.parametrize("path", PAIRS, ids=lambda path: path.stem)
def test_the_two_implementations_agree(path: Path, tmp_path: Path) -> None:
    pair = _pair(path)
    expected = _python_view(pair, tmp_path)
    actual = _node_view(path)
    because = f"\n{path.name}: {pair['why']}"

    assert set(actual) == set(expected), f"the two views have different keys{because}"

    for key in ("eligibleCount", "totalCount", "runnable", "warnings", "eligibleIds"):
        assert actual[key] == expected[key], f"{key} differs{because}"
    assert actual["failedScreens"] == expected["failedScreens"], (
        f"the per-project screen verdicts differ{because}"
    )
    empty = expected["eligibleCount"] == 0
    for key in CONTINUOUS:
        if empty and key in EMPTY_POOL_RATIOS:
            continue  # asserted exactly by the empty-pool test below
        assert _close(actual[key], expected[key]), (
            f"{key}: js {actual[key]!r} vs python {expected[key]!r}{because}"
        )


def test_the_committed_payload_is_what_the_api_would_serve() -> None:
    """The pairs are only a parity test while both sides read the same projects.

    If the golden fixtures or `api/scalars.py` move and the payload is not regenerated,
    the JavaScript side would be screening yesterday's pipeline against today's Python.
    """
    assumptions = load_default()
    loaded = load_pipeline(GOLDEN_PIPELINE, assumptions)
    committed = json.loads((PAIRS_DIR / "payload-golden48.json").read_text(encoding="utf-8"))
    fresh = pipeline_payload(loaded, assumptions, hold_years=committed["holdYears"])

    assert fresh["projects"] == committed["projects"], (
        "payload-golden48.json is stale; re-run tests/parity/generate_pairs.py"
    )
    assert fresh["assumptions"] == committed["assumptions"]
    assert fresh["pipelineHash"] == committed["pipelineHash"]


def test_the_screen_name_map_is_a_bijection_onto_the_wire_names() -> None:
    """A map is a place for a name to go missing, so both ends are pinned."""
    assert set(JS_SCREEN_NAMES.values()) == set(SCREEN_NAMES)
    assert len(JS_SCREEN_NAMES) == len(SCREEN_NAMES) == 9

    harness = HARNESS.read_text(encoding="utf-8")
    for js_name, wire_name in JS_SCREEN_NAMES.items():
        assert f"{js_name}: '{wire_name}'" in harness, (
            f"the node harness maps {js_name} differently from wire.py"
        )


@pytest.mark.parametrize(
    "path",
    [path for path in PAIRS if "no-candidates" in path.name],
    ids=lambda path: path.stem,
)
def test_an_empty_pool_says_no_mix_in_each_sides_own_vocabulary(path: Path, tmp_path: Path) -> None:
    """The one divergence #12 found and deliberately did not close.

    With no eligible pool there is no solar mix and no gearing, and the two sides say
    so differently:

    * ``feasibility.js`` returns ``NaN``, which ``format.js`` renders as an em dash.
      That is epic §5's rule — undefined is a dash, never a zero — and 1D asserts it
      directly ("there is no mix without a pool", "never 0%").
    * ``feasibility.py`` returns ``0.0``, because ``PreviewResponse`` types both
      fields as ``float`` under ``allow_inf_nan=False``: the wire cannot carry a
      ``NaN``, so the server has no way to say "undefined" here.

    Closing this means either dropping 1D's em dash or making the two fields nullable
    on the wire, and the wire is a shared contract with a named owner. So the
    divergence is asserted exactly — each side must produce its own documented value
    and nothing else — and the contract question is raised on #1 rather than decided
    here (docs/decisions.md 4B-6).

    The assertion is *tighter* than the tolerance it replaces: a JavaScript ``0`` or a
    Python ``NaN`` both fail.
    """
    pair = _pair(path)
    expected = _python_view(pair, tmp_path)
    actual = _node_view(path)

    assert expected["eligibleCount"] == 0, "this pair is meant to have an empty pool"
    assert actual["eligibleCount"] == 0
    for key in EMPTY_POOL_RATIOS:
        assert actual[key] is None, f"the JavaScript side must send null for {key}, not 0"
        assert expected[key] == 0.0, f"the Python side must send 0.0 for {key}, not null"
