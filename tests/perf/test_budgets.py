"""§12's and epic §7's wall-clock budgets, measured and printed.

These are the numbers a nightly job exists for. They are **not** run per commit:
``TERRAFOLIO_PERF=1`` turns them on, and ``.github/workflows/nightly.yml`` sets it.
A wall-clock assertion on a shared CI runner fails for reasons that have nothing to do
with the code, gets marked flaky, gets skipped, and then measures nothing — while the
guards in ``test_engine_guards.py`` catch the regressions that actually matter without
looking at a clock at all.

Gated by an environment variable rather than by a pytest marker because
``--strict-markers`` is on and the marker list lives in ``pyproject.toml``, which is
1A's file.

Every assertion prints its measurement first, and the machine state it was taken on,
because a timing without a load average is not a result (``docs/decisions.md``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from engine import SEED
from pool import SHIPPED_PIPELINE, build_pool, describe_environment, load_shipped
from wire import pipeline_payload

from terrafolio.domain.enums import Effort
from terrafolio.domain.scalars import MandateScalars
from terrafolio.optimiser.aggregate import aggregate
from terrafolio.optimiser.ga import SearchControls, evolve
from terrafolio.optimiser.objective import quantise, score

HERE = Path(__file__).parent
HARNESS = HERE / "budgets.js"

STANDARD_RUN_BUDGET_S = 5.0
"""§10.3: Standard effort, 90x60, at 500 candidates."""

GENERATION_GAP_BUDGET_MS = 100.0
"""§10.3: the search screen must hear from the run at least this often."""

MANDATE_FEEDBACK_BUDGET_MS = 100.0
"""§12: why the footer is computed client-side at all."""

TABLE_BUDGET_MS = 50.0
"""§12: sort and filter at 500 rows."""

CORE_ARRAYS_BUDGET_MB = 16.0
"""Epic §7, at 2,000 candidates. See the memory test for what "core" is taken to mean."""

ENABLED = os.environ.get("TERRAFOLIO_PERF") == "1"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    not ENABLED, reason="wall-clock budgets run nightly; set TERRAFOLIO_PERF=1"
)


def _statement_bytes(arrays: Any) -> int:
    """Every 30-year matrix the loaded pipeline holds, found by walking the blocks.

    Enumerated rather than listed, so a matrix added to ``StatementArrays`` is counted
    without anyone remembering to add it here.
    """
    statements = arrays.statements
    blocks = [statements, *(getattr(statements, name) for name in statements.__slots__)]
    total = 0
    for block in blocks:
        for name in getattr(block, "__slots__", ()):
            value = getattr(block, name, None)
            if isinstance(value, np.ndarray):
                total += value.nbytes
    return total


def _shipped_or_skip() -> tuple[Any, Any]:
    if not SHIPPED_PIPELINE.is_dir() or len(list(SHIPPED_PIPELINE.glob("*.json"))) < 100:
        pytest.skip("the shipped 300-file pipeline is not present")
    return load_shipped()


def test_a_standard_run_at_five_hundred_candidates_is_under_five_seconds(
    mandate: MandateScalars,
) -> None:
    loaded, assumptions = _shipped_or_skip()
    pool = build_pool(loaded, mandate, assumptions, candidates=500)

    gaps: list[float] = []
    started = last = time.perf_counter()
    search = evolve(pool, mandate, assumptions, SearchControls(effort=Effort.STANDARD, seed=SEED))
    while True:
        try:
            next(search)
        except StopIteration as finished:
            outcome = finished.value
            break
        now = time.perf_counter()
        gaps.append((now - last) * 1000)
        last = now
    elapsed = time.perf_counter() - started

    print(
        f"\nstandard at 500: {elapsed:.2f} s for {outcome.generations} generations"
        f" | gap median {float(np.median(gaps)):.1f} ms, worst {max(gaps):.1f} ms"
        f" | fitness {outcome.fitness:.3f}, {int(outcome.selection.sum())} held"
        f"\n  {describe_environment()}"
    )
    assert elapsed < STANDARD_RUN_BUDGET_S, f"the run took {elapsed:.2f} s"
    assert max(gaps) < GENERATION_GAP_BUDGET_MS, f"the worst gap was {max(gaps):.1f} ms"


def test_the_core_arrays_stay_under_sixteen_megabytes_at_two_thousand(
    mandate: MandateScalars,
) -> None:
    """Epic §7's memory budget, on both readings of it, because both hold.

    "Core arrays" could mean the search's working set — the feature matrices at both
    precisions, the population and the aggregation's per-generation temporaries — or
    that plus the pipeline's own 30-year statements. Measured: 3.8 MB for the working
    set, and 12.0 MB of statements extrapolated from the shipped 300 files to 2,000.
    So the budget holds narrowly (3.8 of 16) and inclusively (15.8 of 16), and there is
    no need to choose the reading that passes.

    Both are asserted. The inclusive one has about 1% of headroom, which is itself the
    finding: one more 30-year matrix on ``StatementArrays`` would breach epic §7 at
    2,000 candidates, and a nightly going red for that is exactly the signal the budget
    exists to give. Recorded as docs/decisions.md 4B-4.
    """
    loaded, assumptions = _shipped_or_skip()
    pool = build_pool(loaded, mandate, assumptions, candidates=2000)
    population_size = assumptions.ga.effort[Effort.EXHAUSTIVE].population

    features_bytes = (
        sum(array.nbytes for array in (pool.fit, pool.country, pool.capex, pool.combined))
        + pool.combined_f32.nbytes
    )
    population = np.ones((population_size, pool.project_count), dtype=np.bool_)
    totals = aggregate(pool, population.astype(np.float32))
    quantise(score(totals, mandate, assumptions), assumptions)
    temporaries = totals.project_shares.nbytes + totals.country_shares.nbytes
    working_set = features_bytes + population.nbytes + temporaries

    statements = _statement_bytes(loaded.arrays)
    per_project = statements / max(loaded.arrays.count, 1)

    print(
        f"\ncore arrays at 2,000 candidates: {working_set / 1e6:.2f} MB"
        f" (features {features_bytes / 1e6:.2f}, population {population.nbytes / 1e6:.2f},"
        f" per-generation temporaries {temporaries / 1e6:.2f})"
        f"\n  the pipeline's own statements, every matrix, at {loaded.arrays.count} files:"
        f" {statements / 1e6:.2f} MB — which extrapolates to"
        f" {per_project * 2000 / 1e6:.2f} MB at 2,000"
        f"\n  {describe_environment()}"
    )
    inclusive = (working_set + per_project * 2000) / 1e6
    print(f"  both readings: {working_set / 1e6:.2f} MB narrow, {inclusive:.2f} MB inclusive")
    assert working_set / 1e6 < CORE_ARRAYS_BUDGET_MB, (
        f"the search's working set alone is {working_set / 1e6:.2f} MB"
    )
    assert inclusive < CORE_ARRAYS_BUDGET_MB, (
        f"the working set plus the statements extrapolated to 2,000 candidates is"
        f" {inclusive:.2f} MB. If a statement matrix was added on purpose, epic §7's"
        f" 16 MB needs revisiting; it had about 1% of headroom when #12 measured it."
    )


@pytest.mark.skipif(NODE is None, reason="node is needed for the front-end budgets")
def test_the_front_end_budgets_hold_at_five_hundred(
    mandate: MandateScalars, tmp_path: Path
) -> None:
    """§12's two client-side budgets, measured against a 500-project payload.

    The payload is built here from the shipped pipeline rather than committed, because
    it is 500 projects of load generator and has no business in the repository. The ids
    are reassigned to a uniform width so the JavaScript's canonical sort is honest.
    """
    loaded, assumptions = _shipped_or_skip()
    payload = pipeline_payload(loaded, assumptions, hold_years=mandate.hold_years)
    source = payload["projects"]
    payload["projects"] = [
        {**source[index % len(source)], "id": f"Q{index:04d}"} for index in range(500)
    ]
    payload["projectCount"] = 500

    pair = json.loads((HERE.parent / "parity" / "pairs" / "01-baseline.json").read_text())
    payload_path, mandate_path = tmp_path / "payload.json", tmp_path / "mandate.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    mandate_path.write_text(json.dumps(pair["mandate"]), encoding="utf-8")

    assert NODE is not None
    finished = subprocess.run(
        [NODE, str(HARNESS), str(payload_path), str(mandate_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert finished.returncode == 0, finished.stderr
    measured = json.loads(finished.stdout)
    feedback, table = measured["mandateFeedback"], measured["holdingsTable"]

    print(
        f"\nmandate feedback at {feedback['projects']} projects:"
        f" {feedback['medianMs']:.2f} ms median, {feedback['worstMs']:.2f} ms worst"
        f" ({feedback['eligible']} eligible)"
        f"\nholdings table at {table['rows']} rows, sorted on {table['field']}:"
        f" {table['medianMs']:.2f} ms median, {table['worstMs']:.2f} ms worst"
        f"\n  node {measured['node']} | web/js/table.js present:"
        f" {table['tableJsPresent']}"
        f"\n  {describe_environment()}"
    )
    assert feedback["medianMs"] < MANDATE_FEEDBACK_BUDGET_MS
    assert table["medianMs"] < TABLE_BUDGET_MS
    assert not table["tableJsPresent"], (
        "web/js/table.js now exists, so this budget must be extended to measure #11's"
        " own sort and filter rather than only the per-row work in controls.js"
    )
