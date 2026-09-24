"""One whole run, over the real service, reduced to bytes that can be compared.

§12's claim is about the *result*, not about the optimiser in isolation: "mandate +
pipeline hash + assumption set + seed determines the result exactly". So the
reproducibility tests submit over HTTP and compare what ``GET /optimisations/{id}``
actually served, which is the one artefact a client, a CSV export and a committee pack
are all derived from.

Four fields are volatile by design and are dropped before comparison — the ULID, the
human reference, the submission timestamp and the duration. Everything else, including
the whole provenance block, has to match.

This deliberately does not import ``tests/api/conftest.py``. That is 3A's harness for
3A's assertions, and a reproducibility suite that silently inherited a change to its
defaults would stop testing what it says it tests.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any, Final

import httpx
from pool import GOLDEN_PIPELINE, repo_root

from terrafolio.api.app import create_app
from terrafolio.api.service import build_service
from terrafolio.api.settings import Settings
from terrafolio.runner.modes import RunnerMode
from terrafolio.store.runs import load_result_json, load_run

__all__ = [
    "MANDATE",
    "VOLATILE",
    "ServedRun",
    "mandate",
    "served_run",
    "settings_for",
    "stable",
]

COUNTRIES: Final = (
    "ES", "PT", "IT", "GR", "FR", "DE", "PL", "RO", "NL", "DK", "IE", "SE", "FI", "GB",
)  # fmt: skip

MANDATE: Final[Mapping[str, Any]] = {
    "availableCapital_m": 1200,
    "capacityTargetMw": 1500,
    "solarShare": 0.45,
    "targetIrr": 0.11,
    "holdYears": 10,
    "countries": list(COUNTRIES),
    "stages": ["greenfield", "ready_to_build", "construction"],
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
"""§5's default mandate in the ``api.md`` §6.1 wire shape."""

VOLATILE: Final = ("runId", "runRef", "createdAt", "durationMs")
"""What two identical runs are allowed to differ by, and nothing else.

``runId`` is a ULID and ``runRef`` counts submissions, so both move with the clock and
the database. ``createdAt`` and ``durationMs`` are wall clock. A field added here in
future is a field §12 stops guaranteeing, so the list is short on purpose.
"""


def mandate(**overrides: Any) -> dict[str, Any]:
    return {**MANDATE, **overrides}


def settings_for(tmp_path: Path, *, pipeline: Path | None = None, **overrides: Any) -> Settings:
    """A service over ``pipeline``, running searches inline so a test can await them.

    ``INLINE`` rather than ``PROCESS`` because these tests assert on values, not on
    thread counts: inline is the same engine driven synchronously. The one test that
    cares what BLAS actually did is ``tests/api/test_runner.py``, which spawns.
    """
    # Several tests want two independent services, so they pass two subdirectories of
    # one `tmp_path`; SQLite will not create a missing parent for them.
    tmp_path.mkdir(parents=True, exist_ok=True)
    defaults: dict[str, Any] = {
        "pipeline_dir": pipeline or GOLDEN_PIPELINE,
        "database_path": tmp_path / "runs.db",
        "web_dir": repo_root() / "web",
        "runner_mode": RunnerMode.INLINE,
        "warm_workers": False,
    }
    return Settings(**{**defaults, **overrides})


class ServedRun:
    """A finished run, as its bytes, its parsed body and its stored row."""

    __slots__ = ("body", "raw", "run_id", "stored_json")

    def __init__(self, *, raw: str, body: dict[str, Any], run_id: str, stored_json: str) -> None:
        self.raw = raw
        self.body = body
        self.run_id = run_id
        self.stored_json = stored_json

    @property
    def comparable(self) -> str:
        """The served body with the four volatile fields dropped, canonically keyed."""
        return stable(self.body)

    @property
    def record(self) -> dict[str, Any]:
        """The stored row's own view, parsed — what a re-execution has to work from."""
        parsed: dict[str, Any] = json.loads(self.stored_json)
        return parsed

    @property
    def selected_ids(self) -> list[str]:
        ids: list[str] = self.body["selectedIds"]
        return ids


def stable(body: Mapping[str, Any]) -> str:
    """Canonical JSON of a result, minus what two identical runs may differ by."""
    trimmed = {key: value for key, value in body.items() if key not in VOLATILE}
    return json.dumps(trimmed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


async def served_run(
    settings: Settings,
    *,
    seed: int = 42,
    effort: str = "fast",
    locked_ids: Sequence[str] = (),
    excluded_ids: Sequence[str] = (),
    **overrides: Any,
) -> ServedRun:
    """Submit a run, wait for it, and return what the API served for it."""
    service = build_service(settings)
    try:
        app = create_app(settings, service=service)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                accepted = await client.post(
                    "/optimisations",
                    json={
                        "mandate": mandate(**overrides),
                        "effort": effort,
                        "seed": seed,
                        "lockedIds": list(locked_ids),
                        "excludedIds": list(excluded_ids),
                    },
                )
                assert accepted.status_code == 202, accepted.text
                run_id = accepted.json()["runId"]
                served = await client.get(f"/optimisations/{run_id}")
        assert served.status_code == 200, served.text
        body = served.json()
        assert body["status"] == "succeeded", body.get("error")
        with closing(service.connect()) as connection:
            stored = load_run(connection, run_id=run_id)
            stored_json = load_result_json(connection, run_id=run_id)
        assert stored.record.provenance.seed == seed
    finally:
        service.shutdown()
    return ServedRun(raw=served.text, body=body, run_id=run_id, stored_json=stored_json)
