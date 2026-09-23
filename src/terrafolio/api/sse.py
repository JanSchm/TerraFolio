"""The search stream, and the race the specification does not mention.

A Standard run finishes in about **2.5 seconds**. A browser that POSTs and then
opens the stream will routinely miss the first generations, or the entire run.
An in-memory fan-out loses those events permanently, and no amount of buffering
fixes it for a subscriber that arrives after the buffer was discarded.

**So the event log is the source of truth.** The worker appends each generation
to ``run_event`` as the search produces it; this module tails that table from the
beginning, or from ``Last-Event-ID`` if the client is resuming. Replay, late
subscribers, several simultaneous subscribers and survival across a server
restart are then all the same mechanism rather than four.

``id:`` is the generation number, which is what turns ``Last-Event-ID`` into a
resume cursor. The ``status``, ``done`` and ``failed`` frames deliberately carry
**no** id: an id on a terminal frame would collide with the generation sequence,
and under the EventSource specification a frame without one leaves the client's
cursor where it was — which is what a reconnect after ``done`` needs.

There is **no artificial delay**. §6 requires the curves be driven by real
streamed data rather than a simulated animation, and they are. Pacing them over
the 1.5 s minimum screen hold is presentation, and belongs in the client.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import sqlite3
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Final

from terrafolio.domain.enums import RunStatus
from terrafolio.store.db import from_db_time
from terrafolio.store.events import read_events
from terrafolio.store.records import RunEvent
from terrafolio.store.runs import TERMINAL_STATUSES

__all__ = ["RunPulse", "event_stream", "read_pulse", "resume_from"]

MILLISECONDS_PER_SECOND: Final = 1000
FIRST_GENERATION: Final = 1
"""Generations are 1-based, so ``0`` is the "from the beginning" cursor."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RunPulse:
    """The few columns the poll loop needs, without materialising the result.

    ``load_run`` parses ``result_json`` into a validated ``RunRecord`` — 500
    holdings of forty fields each — and a stream polling several times a second
    for several subscribers cannot pay for that to learn one word. So this reads
    the four columns it needs directly. It is a primary-key read of columns the
    schema constrains; nothing here interprets the result.
    """

    status: RunStatus
    created_at: dt.datetime
    duration_ms: int | None
    total_generations: int
    error_code: str | None
    error_message: str | None

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL_STATUSES


def read_pulse(connection: sqlite3.Connection, run_id: str) -> RunPulse | None:
    """One run's state, or ``None`` if there is no such run."""
    row = connection.execute(
        "SELECT status, created_at, duration_ms, generations_planned, "
        "error_code, error_message FROM run WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    return RunPulse(
        status=RunStatus(row["status"]),
        created_at=from_db_time(row["created_at"]),
        duration_ms=row["duration_ms"],
        total_generations=row["generations_planned"],
        error_code=row["error_code"],
        error_message=row["error_message"],
    )


def resume_from(last_event_id: str | None) -> int:
    """The cursor a reconnecting client implies, or 0 for a fresh subscriber.

    A header that is not a generation number is treated as no header at all:
    replaying the whole curve is always correct, where trusting a malformed
    cursor could silently skip the beginning of a run.
    """
    if last_event_id is None:
        return 0
    try:
        cursor = int(last_event_id.strip())
    except ValueError:
        return 0
    return max(cursor, 0)


def _frame(event: str, data: str, *, identifier: int | None = None) -> bytes:
    lines = [] if identifier is None else [f"id: {identifier}"]
    lines.append(f"event: {event}")
    lines.append(f"data: {data}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def _generation_frame(event: RunEvent, total: int) -> bytes:
    """§7's frame. ``summary_json`` is spliced in rather than re-parsed.

    The worker already serialised ``best`` exactly as the wire wants it, and a
    stream with several subscribers would otherwise parse and re-serialise the
    same object once per subscriber per generation for no change in output.
    """
    data = (
        f'{{"generation":{event.generation},"totalGenerations":{total},'
        f'"bestFitness":{json.dumps(event.best_fitness)},'
        f'"meanFitness":{json.dumps(event.mean_fitness)},'
        f'"best":{event.summary_json}}}'
    )
    return _frame("generation", data, identifier=event.generation)


def _status_frame(pulse: RunPulse) -> bytes:
    return _frame(
        "status",
        json.dumps(
            {"status": pulse.status.value, "startedAt": pulse.created_at.isoformat()},
            separators=(",", ":"),
        ),
    )


def _terminal_frame(run_id: str, pulse: RunPulse) -> bytes:
    if pulse.status is RunStatus.SUCCEEDED:
        return _frame(
            "done",
            json.dumps(
                {
                    "runId": run_id,
                    "status": pulse.status.value,
                    "durationMs": pulse.duration_ms,
                    "resultUrl": f"/optimisations/{run_id}",
                },
                separators=(",", ":"),
            ),
        )
    return _frame(
        "failed",
        json.dumps(
            {
                "runId": run_id,
                "status": pulse.status.value,
                "error": {
                    "code": pulse.error_code or "ENGINE_ERROR",
                    "message": pulse.error_message or "The search did not complete.",
                },
            },
            separators=(",", ":"),
        ),
    )


KEEPALIVE: Final = b":keepalive\n\n"
"""A comment frame. Conforming clients ignore it; proxies see traffic."""


async def event_stream(
    open_connection: Callable[[], sqlite3.Connection],
    run_id: str,
    *,
    after_generation: int,
    poll_ms: int,
    keepalive_ms: int,
) -> AsyncIterator[bytes]:
    """Frames for one subscriber, from ``after_generation`` to the end of the run.

    The connection is opened **inside** the generator and closed when it is
    exhausted or the client disconnects, because a stream outlives its request
    handler and ``sqlite3.Connection`` is single-threaded.

    It stays in autocommit throughout. A read wrapped in a transaction would pin
    the reader to the WAL snapshot it opened with, so the worker's appends would
    be invisible and the run would look stalled for ever — the one mistake this
    design makes easy.
    """
    connection = open_connection()
    try:
        pulse = read_pulse(connection, run_id)
        if pulse is None:  # pragma: no cover - the route checks first
            return
        yield _status_frame(pulse)

        cursor = after_generation
        last_sent = time.monotonic()
        while True:
            fresh: Sequence[RunEvent] = read_events(
                connection, run_id=run_id, after_generation=cursor
            )
            for event in fresh:
                cursor = event.generation
                yield _generation_frame(event, pulse.total_generations)
                last_sent = time.monotonic()

            pulse = read_pulse(connection, run_id) or pulse
            if pulse.finished and not fresh:
                # One last drain before the terminal frame. Once the status is
                # terminal no further event can arrive — the log is closed by a
                # trigger — so this read is guaranteed complete rather than
                # merely likely to be.
                for event in read_events(connection, run_id=run_id, after_generation=cursor):
                    cursor = event.generation
                    yield _generation_frame(event, pulse.total_generations)
                yield _terminal_frame(run_id, pulse)
                return

            if not fresh:
                if (time.monotonic() - last_sent) * MILLISECONDS_PER_SECOND >= keepalive_ms:
                    yield KEEPALIVE
                    last_sent = time.monotonic()
                await asyncio.sleep(poll_ms / MILLISECONDS_PER_SECOND)
    finally:
        connection.close()
