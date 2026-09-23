"""Fixtures for the API suite: a real app, over a real pipeline, real store, real engine.

There is no fake mode in this project. The ``inline`` runner executes the same
search as the pool, synchronously, which is what makes these tests deterministic
without waiting on a process; ``tests/api/test_runner.py`` exercises the pool
itself.

The 48 golden fixture files stand in for the shipped 300 everywhere the count
does not matter, because loading 300 files per test would dominate the suite.
``test_performance.py`` uses the real ``pipeline/``.

``httpx.ASGITransport`` rather than ``starlette.testclient``: the latter emits a
deprecation warning, and ``filterwarnings = ["error"]`` turns that into a failure
in every test that touches it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from terrafolio.api.app import create_app
from terrafolio.api.service import Service, build_service
from terrafolio.api.settings import Settings
from terrafolio.runner.modes import RunnerMode

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PIPELINE = REPO_ROOT / "tests" / "golden" / "fixtures" / "pipeline"
SHIPPED_PIPELINE = REPO_ROOT / "pipeline"

TRACKED_STYLESHEET = REPO_ROOT / "web" / "src" / "fonts.css"
"""The stylesheet the pack tests inline, in place of Tailwind's build output.

``web/dist/app.css`` is a build artefact that ``web/.gitignore`` ignores, so it
is absent in CI and in any clean checkout — and a suite that skipped its most
important assertions there would be asserting nothing where it matters most.
``web/src/fonts.css`` is tracked, is the source of the ten ``@font-face`` rules
the real output carries, and exercises the font inlining in full.
``test_committee_pack.py`` checks the real artefact separately, when it exists.
"""

ALL_COUNTRIES = (
    "ES", "PT", "IT", "GR", "FR", "DE", "PL", "RO", "NL", "DK", "IE", "SE", "FI", "GB",
)  # fmt: skip
ALL_STAGES = ("greenfield", "ready_to_build", "construction")

MANDATE: Mapping[str, Any] = {
    "availableCapital_m": 1200,
    "capacityTargetMw": 1500,
    "solarShare": 0.45,
    "targetIrr": 0.11,
    "holdYears": 10,
    "countries": list(ALL_COUNTRIES),
    "stages": list(ALL_STAGES),
    "minLeverage": 0.6,
    "minDscr": 1.25,
    "maxMerchantShare": 0.35,
    "maxCountryShare": 0.35,
    "maxProjectShare": 0.15,
    "codFrom": 2027,
    "codTo": 2032,
    "riskAppetite": "balanced",
    "gridSecuredOnly": False,
    "eurRevenueOnly": False,
    "omContractedOnly": False,
}
"""§5's defaults, spelled out. A test that varies one says which one it varied."""


def mandate(**overrides: Any) -> dict[str, Any]:
    return {**MANDATE, **overrides}


def settings_for(tmp_path: Path, *, pipeline: Path | None = None, **overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "pipeline_dir": pipeline or GOLDEN_PIPELINE,
        "database_path": tmp_path / "runs.db",
        "web_dir": REPO_ROOT / "web",
        # Inline unless a test says otherwise: the same engine, run
        # synchronously, which is what makes an API assertion deterministic.
        "runner_mode": RunnerMode.INLINE,
        "warm_workers": False,
        "stylesheet": TRACKED_STYLESHEET,
    }
    return Settings(**{**defaults, **overrides})


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return settings_for(tmp_path)


@pytest.fixture
def service(settings: Settings) -> Iterator[Service]:
    built = build_service(settings)
    try:
        yield built
    finally:
        built.shutdown()


@pytest.fixture
async def client(settings: Settings, service: Service) -> AsyncIterator[httpx.AsyncClient]:
    """An app and a client over it, with the lifespan actually run.

    The lifespan is what warms the workers and shuts the pool down, so a test
    that skipped it would not be exercising the application the server runs.
    """
    app = create_app(settings, service=service)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://terrafolio") as opened:
            yield opened


async def start_run(
    client: httpx.AsyncClient, *, effort: str = "fast", **body: Any
) -> dict[str, Any]:
    """POST a run and return the 202 body, failing loudly on anything else."""
    response = await client.post(
        "/optimisations", json={"mandate": mandate(), "effort": effort, "seed": 42, **body}
    )
    assert response.status_code == 202, response.text
    accepted: dict[str, Any] = response.json()
    return accepted


def frames(text: str) -> list[dict[str, str]]:
    """Parse an SSE body into ``{id, event, data}`` records, comments kept apart.

    Hand-rolled rather than pulled in as a dependency: the whole point of these
    tests is that the bytes on the wire are the shape ``docs/api.md`` §7 pins,
    and a parser that normalised them away would assert nothing.
    """
    parsed: list[dict[str, str]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        record: dict[str, str] = {}
        for line in block.splitlines():
            if line.startswith(":"):
                record["comment"] = line[1:]
                continue
            field, _, value = line.partition(": ")
            record[field] = value
        parsed.append(record)
    return parsed


def payload(frame: Mapping[str, str]) -> dict[str, Any]:
    decoded: dict[str, Any] = json.loads(frame["data"])
    return decoded
