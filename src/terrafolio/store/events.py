"""The generation log: append from the worker, tail for the stream.

``api.md`` §7 requires that "a client joining late receives the events already
emitted, then continues live". One mechanism serves that, a replay of a
finished run and two simultaneous subscribers: rows keyed ``(run_id, gen)``,
read as a range scan from whatever generation the caller last saw.

Issue 3A owns the SSE stream, the poll interval and the terminal ``done`` and
``failed`` events — those come from ``run.status``, not from this table. This
module owns the append and the range read.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from terrafolio.store.db import writing
from terrafolio.store.errors import DuplicateGenerationError, RunNotFoundError
from terrafolio.store.records import RunEvent

__all__ = ["append_events", "latest_generation", "read_events"]


def append_events(
    connection: sqlite3.Connection, *, run_id: str, events: Sequence[RunEvent]
) -> int:
    """Append one or more generations. Returns the number of rows written.

    Called from the worker process on its own connection. One transaction and
    one ``executemany`` per call, because a per-generation commit is still a
    WAL write and an exhaustive run emits 110 of them inside a five-second
    budget (§10.3).

    A duplicate generation **raises** rather than being ignored. It means the
    worker restarted mid-run or double-emitted, and swallowing it would let the
    persisted curve disagree with ``RunRecord.convergence`` — which is what §6's
    chart and every cross-release regression test read, so the disagreement
    would stay invisible until someone compared the two.
    """
    if not events:
        return 0
    rows = [
        (run_id, event.generation, event.best_fitness, event.mean_fitness, event.summary_json)
        for event in events
    ]
    try:
        with writing(connection) as transaction:
            transaction.executemany(
                "INSERT INTO run_event (run_id, gen, best_fitness, mean_fitness, summary_json) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
    except sqlite3.IntegrityError as error:
        message = str(error)
        if "FOREIGN KEY" in message:
            raise RunNotFoundError(run_id) from error
        generations = sorted(event.generation for event in events)
        raise DuplicateGenerationError(run_id, generations[0]) from error
    return len(rows)


def read_events(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    after_generation: int = 0,
    limit: int | None = None,
) -> tuple[RunEvent, ...]:
    """Every event after ``after_generation``, in ascending generation order.

    This is the resume primitive: a late subscriber passes the last generation
    it saw — 0 for a fresh one — and gets the backlog, then polls with the
    highest generation it has received.

    **The caller must not hold a transaction open across polls.** A connection
    inside a deferred transaction is pinned to the WAL snapshot it opened with
    and will never see the worker's appends, which looks exactly like a run
    that has stalled. :func:`terrafolio.store.db.connect` returns an autocommit
    connection for this reason.
    """
    sql = (
        "SELECT gen, best_fitness, mean_fitness, summary_json FROM run_event "
        "WHERE run_id = ? AND gen > ? ORDER BY gen"
    )
    parameters: tuple[object, ...] = (run_id, after_generation)
    if limit is not None:
        sql += " LIMIT ?"
        parameters = (*parameters, limit)
    return tuple(
        RunEvent(
            generation=row["gen"],
            best_fitness=row["best_fitness"],
            mean_fitness=row["mean_fitness"],
            summary_json=row["summary_json"],
        )
        for row in connection.execute(sql, parameters)
    )


def latest_generation(connection: sqlite3.Connection, *, run_id: str) -> int:
    """The highest generation logged for this run, or 0 if none is.

    One indexed lookup — what a poll loop calls to decide whether to read.
    """
    row = connection.execute(
        "SELECT COALESCE(MAX(gen), 0) AS latest FROM run_event WHERE run_id = ?", (run_id,)
    ).fetchone()
    return int(row["latest"])
