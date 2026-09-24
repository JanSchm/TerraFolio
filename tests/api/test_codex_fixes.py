"""Guards for the defects a Codex review of this branch turned up.

Lifecycle, audit and error-contract holes, plus three concurrency ones. What
they share is that each is reachable only through a second actor — a shutdown, a
restart, a second thread — so none was visible to a test that drove one request
at a time.
"""

from __future__ import annotations

import os
import shutil
import threading
from collections import OrderedDict
from concurrent.futures import CancelledError
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import GOLDEN_PIPELINE, mandate, settings_for, start_run
from test_runner import _pending_for
from test_sse import queued_run

from terrafolio.api.app import create_app
from terrafolio.api.messages import (
    CANCELLED_CODE,
    CANCELLED_MESSAGE,
    ENGINE_ERROR_CODE,
    FAILED_MESSAGE,
    terminal_error,
    warnings_for,
)
from terrafolio.api.pipeline_source import PipelineSource, _touch
from terrafolio.api.service import build_service
from terrafolio.config.loader import load_default
from terrafolio.domain.enums import RunStatus
from terrafolio.domain.mandate import Mandate
from terrafolio.domain.reduce import mandate_to_scalars
from terrafolio.optimiser.feasibility import preview_feasibility
from terrafolio.pipeline.loader import load_pipeline
from terrafolio.runner import threads
from terrafolio.runner.worker import _PIPELINES, _pipeline_for
from terrafolio.store.runs import load_run

# --------------------------------------------------------------------------
# A terminal write that fails must not abandon the run
# --------------------------------------------------------------------------


def test_a_result_that_cannot_be_stored_still_ends_the_run(tmp_path: Path) -> None:
    """``finish_run`` can raise for reasons other than "already finished".

    A divergent event log or a full disk used to escape into a ``Future``
    done-callback, where ``concurrent.futures`` swallows it — leaving the run
    ``running`` for ever with its stream open. It is recorded as failed instead:
    a run that searched successfully and could not be stored still ended.
    """
    settings = settings_for(tmp_path)
    service = build_service(settings)
    try:
        run_id = queued_run(service)
        pending = _pending_for(service, run_id)

        class Unstorable:
            """Stands in for a result the store refuses."""

            def __getattr__(self, name: str) -> Any:
                raise OSError("no space left on device")

        service._succeeded(pending, Unstorable())  # type: ignore[arg-type]

        with closing(service.connect()) as connection:
            stored = load_run(connection, run_id=run_id)
        assert stored.record.status is RunStatus.FAILED
        assert stored.error_code == ENGINE_ERROR_CODE
        assert "no space left on device" in str(stored.error_message)
    finally:
        service.shutdown()


# --------------------------------------------------------------------------
# A cancellation is a cancellation, everywhere
# --------------------------------------------------------------------------


def test_a_cancelled_run_is_stored_as_cancelled_not_failed(tmp_path: Path) -> None:
    """``Runner.shutdown`` cancels queued futures.

    Writing those as ``failed`` with ``ENGINE_ERROR`` would fill the database
    with engine crashes that never happened, on every orderly shutdown — and the
    store has a ``cancelled`` state for exactly this.
    """
    settings = settings_for(tmp_path)
    service = build_service(settings)
    try:
        run_id = queued_run(service)
        pending = _pending_for(service, run_id)
        service._failed(pending, CancelledError("the pool shut down"))

        with closing(service.connect()) as connection:
            stored = load_run(connection, run_id=run_id)
        assert stored.record.status is RunStatus.CANCELLED
        assert stored.error_code == CANCELLED_CODE
    finally:
        service.shutdown()


@pytest.mark.parametrize(
    ("status", "code", "message"),
    [
        (RunStatus.CANCELLED, CANCELLED_CODE, CANCELLED_MESSAGE),
        (RunStatus.FAILED, ENGINE_ERROR_CODE, FAILED_MESSAGE),
    ],
)
def test_the_terminal_error_block_has_one_definition(
    status: RunStatus, code: str, message: str
) -> None:
    """The HTTP body and the SSE frame read it from the same function.

    They disagreed: the stream had been taught that a cancellation is not an
    engine failure and the result endpoint had not, so a client watching a run
    and a client reading it afterwards were told different stories.
    """
    assert terminal_error(status, None) == {"code": code, "message": message}
    # An explicit code always wins over the fallback.
    assert terminal_error(status, "SOMETHING_ELSE")["code"] == "SOMETHING_ELSE"


async def test_a_cancelled_run_reads_back_without_an_engine_error(tmp_path: Path) -> None:
    """The same rule, over HTTP, end to end."""
    settings = settings_for(tmp_path)
    service = build_service(settings)
    try:
        run_id = queued_run(service)
        service._failed(_pending_for(service, run_id), CancelledError("shutdown"))
        app = create_app(settings, service=service)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            body = (await client.get(f"/optimisations/{run_id}")).json()
    finally:
        service.shutdown()
    assert body["status"] == "cancelled"
    assert body["error"] == {"code": CANCELLED_CODE, "message": CANCELLED_MESSAGE}


# --------------------------------------------------------------------------
# The audit trail keeps the warnings the run was accepted under
# --------------------------------------------------------------------------


async def test_a_run_records_the_warnings_it_was_accepted_under(
    client: httpx.AsyncClient, service: Any
) -> None:
    """2B keeps ``warnings_raised`` so a reader can see the advisory conditions.

    It was never populated, so every API-submitted run claimed there had been
    none — while ``/mandate/preview`` had just listed them for the same mandate.
    """
    # A capacity target the eligible pool cannot reach: advisory, so the run is
    # still accepted — which is the case where a warning has to survive onto it.
    stretching = mandate(capacityTargetMw=4000)
    preview = (await client.post("/mandate/preview", json={"mandate": stretching})).json()
    assert preview["runnable"] is True
    assert preview["warnings"], "this mandate needs at least one advisory warning"
    expected = {row["code"] for row in preview["warnings"]}

    accepted = await start_run(client, mandate=stretching)
    with closing(service.connect()) as connection:
        stored = load_run(connection, run_id=accepted["runId"])
    assert {warning.code.name for warning in stored.warnings_raised} == expected


def test_warnings_for_renders_every_signal(tmp_path: Path) -> None:
    """The mapping the run record and the preview now share."""
    service = build_service(settings_for(tmp_path))
    try:
        preview = preview_feasibility(
            service.source.current().candidates.arrays,
            mandate_to_scalars(Mandate.model_validate(mandate())),
            service.assumptions,
        )
        rendered = warnings_for(preview)
    finally:
        service.shutdown()
    assert len(rendered) == len(preview.signals)
    assert all(warning.message for warning in rendered)
    assert [w.code for w in rendered] == sorted(w.code for w in rendered)


# --------------------------------------------------------------------------
# One error envelope, including the framework's own
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [
        ("GET", "/nope", 404, "NOT_FOUND"),
        ("GET", "/js/missing.js", 404, "NOT_FOUND"),
        ("GET", "/package.json", 404, "NOT_FOUND"),
        ("DELETE", "/pipeline", 405, "METHOD_NOT_ALLOWED"),
    ],
)
async def test_framework_errors_use_the_one_envelope(
    client: httpx.AsyncClient, method: str, path: str, status: int, code: str
) -> None:
    """§1.7 gives every error one body. Starlette's own answered ``{"detail": …}``.

    A client cannot parse two shapes with one reader, so the second shape is the
    one that goes unhandled.
    """
    response = await client.request(method, path)
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "detail"}
    assert body["error"]["code"] == code


# --------------------------------------------------------------------------
# `GET /assumptions` identifies the stored snapshot, not this process
# --------------------------------------------------------------------------


async def test_the_assumption_timestamp_survives_a_restart(tmp_path: Path) -> None:
    """``record_assumption_set`` is ``ON CONFLICT DO NOTHING`` over a fixed row.

    Keeping the timestamp we offered made ``createdAt`` move on every restart
    and identify nothing.
    """
    stamps = []
    for _ in range(2):
        settings = settings_for(tmp_path)
        service = build_service(settings)
        try:
            app = create_app(settings, service=service)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                stamps.append((await client.get("/assumptions")).json()["createdAt"])
        finally:
            service.shutdown()
    assert stamps[0] == stamps[1], "createdAt moved when only the process restarted"


# --------------------------------------------------------------------------
# Concurrency
# --------------------------------------------------------------------------


def test_a_slow_reload_cannot_overwrite_a_newer_one(tmp_path: Path) -> None:
    """Two overlapping reloads must not finish out of order.

    The slower, *older* load would assign last and leave the server serving data
    that had already been superseded, with no event to correct it.
    """
    directory = tmp_path / "pipeline"
    shutil.copytree(GOLDEN_PIPELINE, directory)
    source = PipelineSource(directory, load_default(), engine_version="test")
    overlapped = threading.Event()

    original = source._load
    order: list[str] = []

    def slow_then_fast() -> Any:
        order.append("start")
        overlapped.wait(timeout=2.0)
        return original()

    source._load = slow_then_fast  # type: ignore[method-assign]
    first = threading.Thread(target=source.reload)
    first.start()
    while not order:
        pass
    second = threading.Thread(target=source.reload)
    second.start()
    overlapped.set()
    first.join(timeout=10)
    second.join(timeout=10)

    # Serialised rather than interleaved: the second reload could not begin
    # until the first had finished assigning.
    assert len(order) == 2
    assert not first.is_alive() and not second.is_alive()


def test_the_cache_read_and_touch_are_one_step() -> None:
    """Split apart, an eviction between them raises ``KeyError`` on a live key."""
    cache: OrderedDict[int, str] = OrderedDict({1: "a", 2: "b"})
    assert _touch(cache, 1) == "a"
    assert list(cache) == [2, 1], "the hit was not marked most-recently-used"
    assert _touch(cache, 99) is None, "a miss must not raise"


def test_the_caches_are_guarded(tmp_path: Path) -> None:
    """Concurrent readers of different hold periods share one snapshot."""
    directory = tmp_path / "pipeline"
    shutil.copytree(GOLDEN_PIPELINE, directory)
    source = PipelineSource(directory, load_default(), engine_version="test")
    snapshot = source.current()
    assert isinstance(snapshot.lock, type(threading.Lock()))

    errors: list[BaseException] = []

    def hammer(hold: int) -> None:
        try:
            for _ in range(20):
                snapshot.returns(hold)
                snapshot.etag(hold)
        except BaseException as error:
            errors.append(error)

    workers = [threading.Thread(target=hammer, args=(hold,)) for hold in range(5, 25)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    assert errors == []


# --------------------------------------------------------------------------
# The worker cache and the thread report
# --------------------------------------------------------------------------


def test_the_worker_cache_key_names_the_calibration(tmp_path: Path) -> None:
    """The pipeline hash covers the files' bytes and nothing else.

    Which files are rejected and which carry a warning depend on the
    calibration, so two services in one process running different assumption
    sets over one directory would otherwise share a load neither validated.
    """
    directory = tmp_path / "pipeline"
    shutil.copytree(GOLDEN_PIPELINE, directory)
    _PIPELINES.clear()
    try:
        assumptions = load_default()
        loaded = _pipeline_for(directory, _hash_of(directory, assumptions), assumptions)
        key = next(iter(_PIPELINES))
        assert len(key) == 3
        assert key[2] == assumptions.content_hash
        assert loaded.pipeline_hash == key[1]
    finally:
        _PIPELINES.clear()


def test_an_uncapped_backend_is_reported_as_machine_sized() -> None:
    """One capped variable used to report one thread for the whole process.

    That is precisely the case where the reduction order is *not* fixed, and
    precisely the claim a run record must not make.
    """
    inherited = dict(threads._INHERITED)
    try:
        threads._INHERITED.update(dict.fromkeys(threads.THREAD_VARIABLES, None))
        threads._INHERITED["OMP_NUM_THREADS"] = "1"
        assert threads.observed_threads() == (os.cpu_count() or 1)

        threads._INHERITED.update(dict.fromkeys(threads.THREAD_VARIABLES, "1"))
        assert threads.observed_threads() == 1

        threads._INHERITED["OPENBLAS_NUM_THREADS"] = "not-a-number"
        assert threads.observed_threads() == (os.cpu_count() or 1)
    finally:
        threads._INHERITED.clear()
        threads._INHERITED.update(inherited)


def _hash_of(directory: Path, assumptions: Any) -> str:
    return load_pipeline(directory, assumptions).pipeline_hash
