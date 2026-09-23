"""The stream, and the race the specification does not mention.

A Standard run finishes in about 2.5 s, so a browser that POSTs and then
subscribes routinely misses generations or the whole run. Every test here is a
consequence of answering that with a durable event log rather than an in-memory
fan-out: replay, several subscribers, and resume are the same mechanism.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from conftest import frames, mandate, payload, settings_for, start_run

from terrafolio.api.app import create_app
from terrafolio.api.records import build_provenance
from terrafolio.api.service import SUBMITTED_BY, Service, build_service
from terrafolio.api.sse import KEEPALIVE, event_stream, resume_from
from terrafolio.domain.enums import Effort
from terrafolio.domain.mandate import Mandate
from terrafolio.store.records import RunSubmission
from terrafolio.store.runs import open_run


async def _stream(client: httpx.AsyncClient, run_id: str, **headers: str) -> list[dict[str, str]]:
    response = await client.get(f"/optimisations/{run_id}/stream", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    return frames(response.text)


async def test_subscribing_after_the_run_finished_replays_every_generation(
    client: httpx.AsyncClient,
) -> None:
    """The acceptance criterion the whole design exists for.

    The run has already completed by the time the stream is opened — inline mode
    makes that certain rather than likely — and the subscriber still receives
    generation 1 onwards, followed by exactly one ``done``.
    """
    accepted = await start_run(client)
    parsed = await _stream(client, accepted["runId"])

    generations = [frame for frame in parsed if frame.get("event") == "generation"]
    assert [int(frame["id"]) for frame in generations] == list(
        range(1, accepted["totalGenerations"] + 1)
    )
    assert [frame.get("event") for frame in parsed].count("done") == 1
    assert parsed[-1]["event"] == "done"
    assert parsed[0]["event"] == "status"


async def test_a_generation_frame_carries_what_the_search_screen_shows(
    client: httpx.AsyncClient,
) -> None:
    """§7's fields, and §6's five live figures from the running best portfolio."""
    accepted = await start_run(client)
    parsed = await _stream(client, accepted["runId"])
    first = payload(next(frame for frame in parsed if frame.get("event") == "generation"))
    assert first["generation"] == 1
    assert first["totalGenerations"] == accepted["totalGenerations"]
    assert set(first["best"]) == {"projectCount", "capacityMw", "equity_m", "blendedIrr"}
    assert isinstance(first["bestFitness"], float)
    # Quantised to 6 dp, as the engine compares them (epic §5): one last-bit
    # difference flips a tournament and diverges the whole run.
    assert round(first["bestFitness"], 6) == first["bestFitness"]
    assert round(first["meanFitness"], 6) == first["meanFitness"]


async def test_terminal_frames_carry_no_id(client: httpx.AsyncClient) -> None:
    """An id on ``done`` would collide with the generation sequence.

    Under the EventSource specification a frame without ``id:`` leaves the
    client's cursor where it was, which is exactly what a reconnect after
    ``done`` needs: it resumes from its last *generation*, not from the end.
    """
    accepted = await start_run(client)
    parsed = await _stream(client, accepted["runId"])
    for frame in parsed:
        if frame.get("event") in {"status", "done", "failed"}:
            assert "id" not in frame


async def test_two_simultaneous_subscribers_both_receive_every_generation(
    client: httpx.AsyncClient,
) -> None:
    accepted = await start_run(client)
    first, second = await asyncio.gather(
        _stream(client, accepted["runId"]), _stream(client, accepted["runId"])
    )
    ids = [int(f["id"]) for f in first if f.get("event") == "generation"]
    assert ids == [int(f["id"]) for f in second if f.get("event") == "generation"]
    assert len(ids) == accepted["totalGenerations"]


async def test_last_event_id_resumes_at_the_next_generation(
    client: httpx.AsyncClient,
) -> None:
    """``Last-Event-ID: 20`` resumes from generation 21."""
    accepted = await start_run(client, effort="standard")
    assert accepted["totalGenerations"] > 20
    parsed = await _stream(client, accepted["runId"], **{"Last-Event-ID": "20"})
    ids = [int(frame["id"]) for frame in parsed if frame.get("event") == "generation"]
    assert ids[0] == 21
    assert ids == list(range(21, accepted["totalGenerations"] + 1))
    assert parsed[-1]["event"] == "done"


@pytest.mark.parametrize(
    ("header", "expected"), [(None, 0), ("20", 20), ("  7 ", 7), ("nonsense", 0), ("-4", 0)]
)
def test_a_malformed_resume_cursor_replays_from_the_beginning(
    header: str | None, expected: int
) -> None:
    """Replaying the whole curve is always correct; trusting a bad cursor is not."""
    assert resume_from(header) == expected


async def test_the_stream_of_an_unknown_run_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/optimisations/01JB2QNOTAREALRUNIDXXXXXXX/stream")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RUN_NOT_FOUND"


def queued_run(service: Service) -> str:
    """Record a run and never start it, so its stream has nothing to end on.

    Every other test here watches a run that has already finished, which is the
    interesting case for replay but the useless one for a keepalive: a stream
    that terminates immediately never idles. This is the only way to get a
    genuinely open stream without waiting for a real search to be slow.
    """
    provenance = build_provenance(
        seed=1,
        loaded=service.source.result,
        assumptions=service.assumptions,
        blas_threads=service.predicted_blas_threads(),
    )
    params = service.assumptions.ga.effort[Effort.FAST]
    submission = RunSubmission(
        created_at=dt.datetime.now(tz=dt.UTC),
        created_by=SUBMITTED_BY,
        mandate=Mandate.model_validate(mandate()),
        effort=Effort.FAST,
        provenance=provenance,
        assumption_snapshot_hash=service.assumption_snapshot,
        eligible_ids=tuple(service.source.result.arrays.ids[:2]),
        population_size=params.population,
        generations_planned=params.generations,
        deterministic_reduction=provenance.blas_threads == 1,
    )
    with closing(service.connect()) as connection:
        return open_run(connection, submission).record.run_id


async def test_a_keepalive_frame_goes_out_while_nothing_is_happening(tmp_path: Path) -> None:
    """The comment frame proxies need during an Exhaustive run (§7).

    Driven by a queued run and a zero keepalive interval rather than by waiting
    fifteen seconds: the assertion is that an idle stream emits one, not that it
    waits the configured time to do so.
    """
    settings = settings_for(tmp_path, sse_keepalive_ms=0, sse_poll_ms=1)
    service = build_service(settings)
    try:
        run_id = queued_run(service)
        stream = event_stream(
            service.connect,
            run_id,
            after_generation=0,
            poll_ms=settings.sse_poll_ms,
            keepalive_ms=settings.sse_keepalive_ms,
        )
        seen: list[bytes] = []
        try:
            async for chunk in stream:
                seen.append(chunk)
                if chunk == KEEPALIVE:
                    break
        finally:
            await stream.aclose()
        assert seen[0].startswith(b"event: status")
        assert KEEPALIVE in seen
    finally:
        service.shutdown()


async def test_an_in_flight_run_reads_200_with_its_progress(tmp_path: Path) -> None:
    """§8: a run that has not finished is 200 with ``status``, not 202.

    The run is the resource; 202 would say the request to *read* it had been
    accepted. ``generation`` is what a non-streaming client polls.
    """
    settings = settings_for(tmp_path)
    service = build_service(settings)
    try:
        app = create_app(settings, service=service)
        run_id = queued_run(service)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get(f"/optimisations/{run_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "queued"
        assert body["generation"] == 0
        assert body["totalGenerations"] > 0
        assert "aggregates" not in body
        assert response.headers["cache-control"] == "no-store"
    finally:
        service.shutdown()
