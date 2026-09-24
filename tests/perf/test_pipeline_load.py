"""Epic §7: 300 files parsed, validated and tied out in under 2 s.

Per-commit, unlike the budgets in ``test_budgets.py``, because this one runs on every
page load in production and because the threshold is nowhere near the measurement —
only a change of algorithm trips it. ``tests/api/test_performance.py`` measures the
same thing through ``build_service``; this measures ``load_pipeline`` itself, so a
regression in the loader and a regression in the service layer read differently.
"""

from __future__ import annotations

import time
from statistics import median

import pytest
from pool import SHIPPED_PIPELINE, describe_environment

from terrafolio.config.loader import load_default
from terrafolio.pipeline.loader import load_pipeline

LOAD_BUDGET_MS = 2000.0
SAMPLES = 3

pytestmark = pytest.mark.skipif(
    not SHIPPED_PIPELINE.is_dir() or len(list(SHIPPED_PIPELINE.glob("*.json"))) < 100,
    reason="the shipped 300-file pipeline is not present",
)


def test_three_hundred_files_load_validate_and_tie_out_under_two_seconds() -> None:
    assumptions = load_default()
    timings: list[float] = []
    for _ in range(SAMPLES):
        started = time.perf_counter()
        loaded = load_pipeline(SHIPPED_PIPELINE, assumptions)
        timings.append((time.perf_counter() - started) * 1000)

    print(
        f"\npipeline load: {median(timings):,.0f} ms median of {SAMPLES}"
        f" for {loaded.loaded_count} files"
        f" ({len(loaded.rejected)} rejected, {len(loaded.warnings)} warnings)"
        f"\n  {describe_environment()}"
    )
    assert loaded.loaded_count >= 100
    assert not loaded.rejected, f"a shipped file failed its tie-outs: {loaded.rejected[:2]}"
    assert median(timings) < LOAD_BUDGET_MS, f"{median(timings):,.0f} ms"
