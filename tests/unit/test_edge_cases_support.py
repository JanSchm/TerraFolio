"""Builders shared by the four ``test_edge_cases_*`` modules.

Carries a ``test_`` prefix because epic §8 gives 4C the path
``tests/unit/test_edge_cases*`` and nothing else under ``tests/unit/``. pytest
therefore collects this module; it defines no ``test_`` function and no ``Test``
class, so it contributes no tests.

**Why this duplicates ``tests/api/conftest.py`` rather than importing it.** That
file is 3A's, and under pytest's prepend import mode it resolves only from inside
``tests/api/``: there is no root ``conftest.py`` and no ``pythonpath`` setting, so
``from conftest import settings_for`` here would work during a full run — because
``tests/api`` sorts first and lands on ``sys.path`` — and fail the moment anyone
ran ``pytest tests/unit`` alone. Adding a second ``conftest.py`` under
``tests/unit/test_edge_cases/`` would be worse: pytest keys conftest modules
outside a package on the bare name ``conftest``, so 3A's own
``from conftest import …`` would become dependent on collection order.

Everything here is a plain function. A ``@pytest.fixture`` imported by name into a
test module reads as an unused import to ruff's ``F401`` unless it is also
referenced, which is exactly the friction that makes people silence the linter.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, closing, contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from terrafolio.api.app import create_app
from terrafolio.api.service import Service, build_service
from terrafolio.api.settings import Settings
from terrafolio.config.assumptions import AssumptionSet
from terrafolio.config.loader import load_default
from terrafolio.pipeline.loader import LoadResult, load_pipeline
from terrafolio.runner.modes import RunnerMode
from terrafolio.store.runs import StoredRun, load_run

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PIPELINE = REPO_ROOT / "tests" / "golden" / "fixtures" / "pipeline"
EDGE_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "edge"
BASE_FIXTURE = GOLDEN_PIPELINE / "P01-almonte-solar.json"

TRACKED_STYLESHEET = REPO_ROOT / "web" / "src" / "fonts.css"
"""The pack's stylesheet, for the same reason ``tests/api/conftest.py`` gives:
``web/dist/app.css`` is a build artefact that ``web/.gitignore`` ignores, so it is
absent in CI and in any clean checkout."""

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
"""§5's defaults. A test that varies one says which one it varied."""


def mandate(**overrides: Any) -> dict[str, Any]:
    return {**MANDATE, **overrides}


# ---------------------------------------------------------------------------
# The fixture library, and its provenance
# ---------------------------------------------------------------------------

EdgePointers = tuple[tuple[str | int, ...], ...]
"""A path into a project file. An ``int`` step indexes a statement series.

Naming the index matters: a pointer that stops at the array would let a fixture
carrying *two* edits inside one series pass a guard whose docstring promises the
file is broken in exactly one way.
"""

EDGE_PROVENANCE: Mapping[str, EdgePointers] = {
    "P49-fails-a-tie-out.json": (("statements", "debtSchedule", "closing", 5),),
    "P50-supplies-derived-results.json": (("irr",), ("moic",), ("terminalValue",)),
    "P51-twenty-nine-statement-years.json": (("statements", "years"),),
    "P52-thirty-one-statement-years.json": (("statements", "years"),),
    "P53-declares-outlier-assumptions.json": (("assumptions", "taxRate"),),
    "P01-duplicates-an-existing-id.json": (),
}
"""Every path in each edge fixture that differs from ``BASE_FIXTURE``.

``tests/fixtures/edge/README.md`` carries the same table in prose. The guard in
``test_edge_cases_ingestion.py`` repairs each pointer from the base and asserts
the result is the base again, which proves in one assertion that a fixture is
current with 1A's schema, current with 1C's corpus, and broken in exactly one way.
``id`` and ``name`` are excluded by the guard rather than listed here: every file
changes ``name``, and all but the duplicate change ``id``.
"""

_IDENTITY_KEYS = ("id", "name")


def base_payload() -> dict[str, Any]:
    """A fresh copy of the file every edge fixture derives from."""
    payload: dict[str, Any] = json.loads(BASE_FIXTURE.read_text(encoding="utf-8"))
    return payload


def edge_payload(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((EDGE_FIXTURES / name).read_text(encoding="utf-8"))
    return payload


def repair(payload: dict[str, Any], base: dict[str, Any], pointers: EdgePointers) -> dict[str, Any]:
    """Copy each pointer's value back from ``base``, deleting what the base lacks.

    A trailing ``int`` addresses one element of a series, so repairing it leaves
    every other element to be compared — which is what makes "broken in exactly
    one way" an assertion rather than a claim.
    """
    for pointer in pointers:
        node: Any = payload
        source: Any = base
        for key in pointer[:-1]:
            node = node[key]
            source = source[key]
        leaf = pointer[-1]
        # An int indexes a series, so the base always has it; a str may name a
        # key the base never carried, which is a fixture that *added* a field.
        if isinstance(leaf, int) or leaf in source:
            node[leaf] = source[leaf]
        else:
            del node[leaf]
    for key in _IDENTITY_KEYS:
        payload[key] = base[key]
    return payload


def edge_corpus(tmp_path: Path, *, add: Sequence[str] = ()) -> Path:
    """The 48 golden files in a writable directory, plus named edge fixtures.

    Deriving from the golden corpus rather than from a bare directory is what
    makes "the rest of the pipeline is untouched" assertable: 48 valid companions
    come along free, and every one of them has a width-3 id, which
    ``_require_uniform_id_width`` demands (C-5).
    """
    directory = tmp_path / "pipeline"
    directory.mkdir(parents=True, exist_ok=True)
    for path in sorted(GOLDEN_PIPELINE.glob("*.json")):
        shutil.copy(path, directory / path.name)
    for name in add:
        shutil.copy(EDGE_FIXTURES / name, directory / name)
    return directory


@lru_cache(maxsize=1)
def assumptions() -> AssumptionSet:
    return load_default()


@lru_cache(maxsize=1)
def golden_load() -> LoadResult:
    """One load of the untouched corpus, shared by every test that only reads it.

    ``lru_cache`` rather than a module-scoped fixture, because a fixture imported
    by name is the friction this module's docstring explains.
    """
    return load_pipeline(GOLDEN_PIPELINE, assumptions())


# ---------------------------------------------------------------------------
# A real app, over a real pipeline, real store, real engine
# ---------------------------------------------------------------------------


def settings_for(tmp_path: Path, *, pipeline: Path | None = None, **overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "pipeline_dir": pipeline or GOLDEN_PIPELINE,
        "database_path": tmp_path / "runs.db",
        "web_dir": REPO_ROOT / "web",
        # The same engine, run synchronously. That is what makes an assertion
        # about a finished run deterministic without waiting on a process.
        "runner_mode": RunnerMode.INLINE,
        "warm_workers": False,
        "stylesheet": TRACKED_STYLESHEET,
    }
    return Settings(**{**defaults, **overrides})


@contextmanager
def opened_service(settings: Settings) -> Iterator[Service]:
    """A service that is always shut down.

    ``filterwarnings = ["error"]`` turns an unclosed sqlite connection surfacing
    at collection time into a failure in whichever test happens to be running,
    which is close to undiagnosable. Nothing here builds a service without this.
    """
    service = build_service(settings)
    try:
        yield service
    finally:
        service.shutdown()


@asynccontextmanager
async def opened_client(settings: Settings, service: Service) -> AsyncIterator[httpx.AsyncClient]:
    """An app and a client over it, with the lifespan actually run.

    ``httpx.ASGITransport`` rather than ``starlette.testclient``: the latter emits
    a deprecation warning that ``filterwarnings = ["error"]`` turns into a failure.
    """
    app = create_app(settings, service=service)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://terrafolio") as client:
            yield client


async def completed_run(client: httpx.AsyncClient, **body: Any) -> dict[str, Any]:
    """POST a run, assert it was accepted and succeeded, and return its result body."""
    request = {"mandate": mandate(), "effort": "fast", "seed": 42, **body}
    accepted = await client.post("/optimisations", json=request)
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["runId"]
    finished = await client.get(f"/optimisations/{run_id}")
    assert finished.status_code == 200, finished.text
    result: dict[str, Any] = finished.json()
    assert result["status"] == "succeeded", result
    return result


def stored_run(service: Service, run_id: str) -> StoredRun:
    with closing(service.connect()) as connection:
        return load_run(connection, run_id=run_id)
