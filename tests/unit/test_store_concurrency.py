"""The reference under parallel writers, and the stream under a live worker.

Separate **processes**, not threads, and by ``subprocess`` rather than
``multiprocessing``: SQLite's WAL locking is POSIX-advisory and per file
descriptor, so thread contention inside one process exercises a different path
and proves nothing about the case that actually happens — a uvicorn worker and
a search process writing to one file. ``multiprocessing`` is avoided too,
because its default start method on Linux is ``fork``, and forking a
multi-threaded process warns; this suite turns warnings into failures, so that
would pass here and fail on CI. ``tests/unit/test_import_boundaries.py`` uses
the same subprocess idiom.
"""

from __future__ import annotations

import subprocess
import sys
from contextlib import closing
from pathlib import Path

from test_store_runs import RUN_ID, opened_store, submission

from terrafolio.store import (
    append_events,
    latest_generation,
    list_runs,
    open_run,
    read_events,
)

WRITERS = 4
RUNS_EACH = 5

_OPEN_RUNS = """
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, {tests!r})
from test_store_runs import submission
from terrafolio.store import connect, open_run

connection = connect(Path({path!r}))
try:
    for _ in range({count}):
        stored = open_run(connection, submission(run_id=None))
        print(stored.run_reference, stored.record.run_ref)
finally:
    connection.close()
"""

_APPEND_EVENTS = """
import json, sys, time
from pathlib import Path

sys.path.insert(0, {tests!r})
from terrafolio.store import RunEvent, append_events, connect

connection = connect(Path({path!r}))
try:
    for generation in range(1, {count} + 1):
        append_events(connection, run_id={run_id!r}, events=[RunEvent(
            generation=generation, best_fitness=float(generation),
            mean_fitness=-float(generation), summary_json=json.dumps({{"g": generation}}))])
        time.sleep(0.01)
finally:
    connection.close()
"""


def _tests_dir() -> str:
    return str(Path(__file__).parent)


def _spawn(program: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", program],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_concurrent_writers_never_mint_the_same_reference(tmp_path: Path) -> None:
    """The acceptance criterion, under real contention.

    The reference is claimed by an ``UPDATE ... RETURNING`` inside an IMMEDIATE
    transaction, so the write lock is already held when it reads. A deferred
    transaction would take a snapshot first and fail with a conflict the busy
    handler does not retry — which is why this is worth a process test rather
    than an argument.
    """
    path = tmp_path / "runs.db"
    with closing(opened_store(path)):
        pass  # The schema and both snapshots, written once by the parent.

    program = _OPEN_RUNS.format(tests=_tests_dir(), path=str(path), count=RUNS_EACH)
    writers = [_spawn(program) for _ in range(WRITERS)]
    outputs = []
    for writer in writers:
        stdout, stderr = writer.communicate(timeout=120)
        assert writer.returncode == 0, stderr
        outputs.append(stdout)

    references = [int(line.split()[0]) for out in outputs for line in out.splitlines()]
    labels = [line.split()[1] for out in outputs for line in out.splitlines()]
    expected = WRITERS * RUNS_EACH
    assert len(references) == expected
    assert sorted(references) == list(range(1, expected + 1))
    assert len(set(labels)) == expected


def test_every_reference_that_was_minted_reached_a_run(tmp_path: Path) -> None:
    """A reference claimed by a transaction that then rolled back would leave a
    gap. Nothing is deleted, so the table is the sequence."""
    path = tmp_path / "runs.db"
    with closing(opened_store(path)):
        pass
    program = _OPEN_RUNS.format(tests=_tests_dir(), path=str(path), count=RUNS_EACH)
    for writer in [_spawn(program) for _ in range(WRITERS)]:
        assert writer.communicate(timeout=120)[0] is not None

    with closing(opened_store(path)) as connection:
        stored = list_runs(connection, limit=WRITERS * RUNS_EACH + 1)
        assert [item.run_reference for item in stored] == list(range(WRITERS * RUNS_EACH, 0, -1))
        sequence = connection.execute(
            "SELECT last_reference FROM run_sequence WHERE series = 'A'"
        ).fetchone()[0]
        assert sequence == WRITERS * RUNS_EACH


def test_a_reader_tails_a_curve_while_another_process_appends_to_it(
    tmp_path: Path,
) -> None:
    """3A's shape: the worker writes generations, the stream reads them as they
    land. The reader must be in autocommit or it stays pinned to the snapshot it
    opened with and the run looks stalled."""
    path = tmp_path / "runs.db"
    generations = 12
    with closing(opened_store(path)) as connection:
        open_run(connection, submission())

        worker = _spawn(
            _APPEND_EVENTS.format(
                tests=_tests_dir(), path=str(path), count=generations, run_id=RUN_ID
            )
        )
        seen: list[int] = []
        try:
            while worker.poll() is None or latest_generation(connection, run_id=RUN_ID) > len(seen):
                fresh = read_events(
                    connection, run_id=RUN_ID, after_generation=seen[-1] if seen else 0
                )
                seen.extend(event.generation for event in fresh)
                if len(seen) >= generations:
                    break
            _, stderr = worker.communicate(timeout=120)
            assert worker.returncode == 0, stderr
        finally:
            if worker.poll() is None:  # pragma: no cover - only on a timeout
                worker.kill()

        assert seen == list(range(1, generations + 1))


def test_a_worker_process_appends_to_a_run_the_parent_opened(tmp_path: Path) -> None:
    """The real lifecycle crosses a process boundary: the request handler opens
    the run, a separate process does the search."""
    path = tmp_path / "runs.db"
    with closing(opened_store(path)) as connection:
        open_run(connection, submission())
    worker = _spawn(
        _APPEND_EVENTS.format(tests=_tests_dir(), path=str(path), count=3, run_id=RUN_ID)
    )
    _, stderr = worker.communicate(timeout=120)
    assert worker.returncode == 0, stderr
    with closing(opened_store(path)) as connection:
        assert latest_generation(connection, run_id=RUN_ID) == 3
        assert append_events(connection, run_id=RUN_ID, events=()) == 0
