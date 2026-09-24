"""The two budgets this issue owns, measured against the shipped 300-file pipeline.

§12 budgets ``GET /pipeline`` for the mandate screen, which hits it on every
load, and epic §7 budgets the load itself at under 2 s because it runs on every
reload. Both are measured here rather than asserted about; #12 owns the
performance suite proper, and this is the guard that stops an obvious regression
reaching it.

Generous thresholds on purpose. A timing test that fails on a loaded laptop gets
disabled, and a disabled test measures nothing — so these are set where only a
change of algorithm trips them, and the PR carries the actual numbers.
"""

from __future__ import annotations

import time
from statistics import median

import httpx
import pytest
from conftest import SHIPPED_PIPELINE, settings_for

from terrafolio.api.app import create_app
from terrafolio.api.service import build_service

WARM_BUDGET_MS = 200
"""§12, via #9: ``GET /pipeline`` at 300 projects, warm."""

LOAD_BUDGET_MS = 2000
"""Epic §7: 300 files parsed, validated and tied out."""

SAMPLES = 9

pytestmark = pytest.mark.skipif(
    not SHIPPED_PIPELINE.is_dir() or len(list(SHIPPED_PIPELINE.glob("*.json"))) < 100,
    reason="the shipped 300-file pipeline is not present",
)


async def test_get_pipeline_is_under_the_budget_warm_at_three_hundred(
    tmp_path: pytest.TempPathFactory,
) -> None:
    settings = settings_for(tmp_path, pipeline=SHIPPED_PIPELINE)  # type: ignore[arg-type]
    started = time.perf_counter()
    service = build_service(settings)
    load_ms = (time.perf_counter() - started) * 1000
    try:
        app = create_app(settings, service=service)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                cold = await _timed(client)
                warm = [await _timed(client) for _ in range(SAMPLES)]
                response = await client.get("/pipeline")
    finally:
        service.shutdown()

    projects = response.json()["projects"]
    assert len(projects) >= 100
    # Scalars only: the arrays would be four and a half times the rest (§2).
    assert "statements" not in response.text
    print(
        f"\nload {load_ms:,.0f} ms for {len(projects)} files"
        f" | GET /pipeline cold {cold:,.1f} ms, warm median {median(warm):,.1f} ms"
        f" ({len(response.content) / 1024:,.0f} KB)"
    )
    assert load_ms < LOAD_BUDGET_MS, f"the load took {load_ms:,.0f} ms"
    assert median(warm) < WARM_BUDGET_MS, f"warm median {median(warm):,.1f} ms"


async def _timed(client: httpx.AsyncClient) -> float:
    started = time.perf_counter()
    response = await client.get("/pipeline")
    assert response.status_code == 200
    return (time.perf_counter() - started) * 1000
