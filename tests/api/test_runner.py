"""The runner: a pinned worker, a real pool, and a failure that is still a run.

``inline`` carries the rest of the suite because it is deterministic. These
tests exercise what inline cannot: a genuine spawned process, where the BLAS pin
happens **before** numpy is imported and §12's bit-exact guarantee is real.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from conftest import GOLDEN_PIPELINE, mandate, settings_for
from pydantic import ValidationError

from terrafolio.api.app import create_app
from terrafolio.api.service import build_service
from terrafolio.api.wire import OptimisationRequest
from terrafolio.domain.enums import Effort, RunStatus
from terrafolio.domain.mandate import Mandate
from terrafolio.runner.modes import RunnerMode
from terrafolio.runner.pool import worker_count
from terrafolio.runner.threads import (
    THREAD_VARIABLES,
    observed_threads,
    threads_are_pinned,
)
from terrafolio.runner.worker import PipelineMovedError, RunPayload, execute
from terrafolio.store.runs import load_run

TERMINAL = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
POLL_LIMIT = 400
"""How many times to look for a terminal state before giving up. At 50 ms a
poll that is twenty seconds, which is far past a Fast run on any machine."""


async def _await_terminal(client: httpx.AsyncClient, run_id: str) -> dict[str, object]:
    for _ in range(POLL_LIMIT):
        body = (await client.get(f"/optimisations/{run_id}")).json()
        if RunStatus(str(body["status"])) in TERMINAL:
            return body
        await asyncio.sleep(0.05)
    pytest.fail(f"run {run_id} never reached a terminal state")


async def test_a_pool_worker_runs_single_threaded(tmp_path: Path) -> None:
    """The acceptance criterion: the worker's BLAS thread count is 1.

    A real spawned process, not an assertion about configuration. Every
    threading library reads its variable once, when its shared object loads on
    the first ``import numpy`` — so this is only true because the pin runs at
    the top of ``runner/worker.py`` and as the pool's initializer.

    ``deterministicReduction`` on the run row records the same fact, because
    multi-threaded BLAS has no fixed reduction order and the GA's trajectory
    turns on ``f[a] >= f[b]``.
    """
    settings = settings_for(tmp_path, runner_mode=RunnerMode.PROCESS, max_workers=1)
    service = build_service(settings)
    try:
        app = create_app(settings, service=service)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                started = await client.post(
                    "/optimisations", json={"mandate": mandate(), "effort": "fast", "seed": 42}
                )
                assert started.status_code == 202
                body = await _await_terminal(client, started.json()["runId"])

        assert body["status"] == "succeeded", body
        assert body["provenance"]["blasThreads"] == 1  # type: ignore[index]
        with closing(service.connect()) as connection:
            stored = load_run(connection, run_id=str(body["runId"]))
        assert stored.deterministic_reduction is True
    finally:
        service.shutdown()


def test_the_pin_is_reported_honestly_when_it_came_too_late() -> None:
    """numpy is long since imported in a test process, so the pin did not take.

    ``observed_threads`` says so rather than claiming one thread. A run record
    that asserted a determinism the run did not have would invite a reproduction
    attempt that cannot succeed and give no clue why — which is worse than an
    honest eight.
    """
    assert threads_are_pinned() is False
    assert observed_threads() >= 1
    assert set(THREAD_VARIABLES) >= {
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    }


@pytest.mark.parametrize(("requested", "expected"), [(1, 1), (4, 4), (0, 1), (-2, 1)])
def test_an_explicit_worker_count_is_honoured_and_never_zero(requested: int, expected: int) -> None:
    assert worker_count(requested) == expected


def test_the_default_worker_count_leaves_a_core_for_the_server() -> None:
    assert worker_count(None) >= 1


def test_the_worker_refuses_a_pipeline_that_moved_under_it(tmp_path: Path) -> None:
    """The hash is verified in the worker, not merely at the front door.

    409 catches a client holding a stale hash. It cannot catch a directory
    edited between acceptance and execution — and a run whose provenance names
    one snapshot while its numbers came from another is not auditable.
    """
    directory = tmp_path / "pipeline"
    shutil.copytree(GOLDEN_PIPELINE, directory)
    settings = settings_for(tmp_path, pipeline=directory)
    service = build_service(settings)
    try:
        payload = RunPayload(
            run_id="01JB2QNOTAREALRUNIDXXXXXXX",
            database_path=settings.database_path,
            pipeline_dir=directory,
            pipeline_hash="sha256:" + "0" * 64,
            assumption_set=None,
            mandate=Mandate.model_validate(mandate()),
            effort=Effort.FAST,
            seed=1,
            locked_ids=(),
            excluded_ids=(),
        )
        with pytest.raises(PipelineMovedError, match="now hashes to"):
            execute(payload)
    finally:
        service.shutdown()


async def test_a_run_that_fails_is_recorded_rather_than_lost(tmp_path: Path) -> None:
    """A failed run is still audit trail (§12): the row persists, with a reason.

    Provoked by the real mechanism rather than a stub — the directory is edited
    after the server loaded it, so the worker's own hash check rejects it. The
    run is accepted (the server's hash still matches what it holds) and then
    fails in the worker, which is exactly the race the check exists for.
    """
    directory = tmp_path / "pipeline"
    shutil.copytree(GOLDEN_PIPELINE, directory)
    settings = settings_for(tmp_path, pipeline=directory, runner_mode=RunnerMode.THREAD)
    service = build_service(settings)
    try:
        app = create_app(settings, service=service)
        async with app.router.lifespan_context(app):
            # The server keeps the snapshot it loaded; the directory does not.
            copied = json.loads((directory / "P01-almonte-solar.json").read_text())
            copied["id"] = "P98"
            copied["name"] = "Copy of Almonte"
            (directory / "P98-copy.json").write_text(json.dumps(copied))

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                started = await client.post(
                    "/optimisations", json={"mandate": mandate(), "effort": "fast", "seed": 3}
                )
                assert started.status_code == 202
                body = await _await_terminal(client, started.json()["runId"])

        assert body["status"] == "failed"
        assert body["error"]["code"] == "ENGINE_ERROR"  # type: ignore[index]
        assert "now hashes to" in body["error"]["message"]  # type: ignore[index]
        # The run keeps everything that explains it.
        assert body["provenance"]["seed"] == 3  # type: ignore[index]
        assert body["aggregates"] is None
        assert body["mandate"]["holdYears"] == mandate()["holdYears"]  # type: ignore[index]
    finally:
        service.shutdown()


async def test_the_inline_runner_is_the_same_engine(tmp_path: Path) -> None:
    """Inline is not a substitute implementation (#9's own words).

    The same mandate and seed through the pool and through the inline path
    select the same portfolio — which is the only thing that makes the rest of
    this suite's use of inline meaningful.
    """
    results = {}
    for mode in (RunnerMode.INLINE, RunnerMode.PROCESS):
        settings = settings_for(tmp_path / mode.value, runner_mode=mode, max_workers=1)
        (tmp_path / mode.value).mkdir(parents=True, exist_ok=True)
        service = build_service(settings)
        try:
            app = create_app(settings, service=service)
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                    started = await client.post(
                        "/optimisations",
                        json={"mandate": mandate(), "effort": "fast", "seed": 2024},
                    )
                    body = await _await_terminal(client, started.json()["runId"])
            results[mode] = body
        finally:
            service.shutdown()

    inline, pooled = results[RunnerMode.INLINE], results[RunnerMode.PROCESS]
    assert inline["status"] == pooled["status"] == "succeeded"
    assert inline["selectedIds"] == pooled["selectedIds"]
    assert inline["convergence"] == pooled["convergence"]


def test_a_service_uses_one_connection_per_caller(tmp_path: Path) -> None:
    """``sqlite3.Connection`` is single-threaded; there is no shared handle.

    The worker is a different process and the completion callbacks arrive on an
    executor thread, so a shared connection would be a data race rather than an
    optimisation.
    """
    service = build_service(settings_for(tmp_path))
    try:
        first, second = service.connect(), service.connect()
        assert first is not second
        first.close()
        second.close()
    finally:
        service.shutdown()


def test_the_optimisation_request_refuses_a_seed_the_store_cannot_hold() -> None:
    """``run.seed`` is a SQLite INTEGER, so a seed must fit in 63 bits.

    Caught on the wire rather than at the insert, where an otherwise valid
    request would surface as a 500.
    """
    with pytest.raises(ValidationError):
        OptimisationRequest.model_validate({"mandate": mandate(), "seed": 2**64})
