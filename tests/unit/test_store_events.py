"""The generation log: appended by the worker, tailed for the stream.

``api.md`` §7 requires that a client joining late "receives the events already
emitted, then continues live". One mechanism has to serve that, a replay of a
finished run, and two simultaneous subscribers — which is what the range read
here is.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from test_store_runs import CREATED_AT, RUN_ID, completed, opened_store, submission

from terrafolio.store import (
    DuplicateGenerationError,
    RunEvent,
    RunIdentityChangedError,
    RunNotFoundError,
    append_events,
    finish_run,
    latest_generation,
    open_run,
    read_events,
)


def curve(*generations: int) -> tuple[RunEvent, ...]:
    """A stretch of the search, in the shape ``api.md`` §7 streams."""
    return tuple(
        RunEvent(
            generation=generation,
            best_fitness=round(-12.4 + generation, 6),
            mean_fitness=round(-31.2 + generation, 6),
            summary_json=json.dumps({"projectCount": generation, "capacityMw": 100 * generation}),
        )
        for generation in generations
    )


def test_events_are_read_back_in_generation_order(tmp_path: Path) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        open_run(connection, submission())
        append_events(connection, run_id=RUN_ID, events=curve(3, 1, 2))
        assert [event.generation for event in read_events(connection, run_id=RUN_ID)] == [
            1,
            2,
            3,
        ]


def test_a_late_subscriber_resumes_from_an_arbitrary_generation(tmp_path: Path) -> None:
    """The resume primitive: the subscriber passes the last generation it saw."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        open_run(connection, submission())
        append_events(connection, run_id=RUN_ID, events=curve(1, 2, 3, 4, 5))
        resumed = read_events(connection, run_id=RUN_ID, after_generation=3)
        assert [event.generation for event in resumed] == [4, 5]


def test_resuming_from_zero_returns_the_whole_curve(tmp_path: Path) -> None:
    """Generations are 1-based, so 0 is the "from the beginning" sentinel a
    fresh subscriber sends."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        open_run(connection, submission())
        append_events(connection, run_id=RUN_ID, events=curve(1, 2, 3))
        assert len(read_events(connection, run_id=RUN_ID, after_generation=0)) == 3


def test_a_read_can_be_limited(tmp_path: Path) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        open_run(connection, submission())
        append_events(connection, run_id=RUN_ID, events=curve(1, 2, 3, 4))
        assert len(read_events(connection, run_id=RUN_ID, limit=2)) == 2


def test_the_summary_survives_unparsed(tmp_path: Path) -> None:
    """The running-best summary belongs to the stream's shape, not the store's."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        open_run(connection, submission())
        append_events(connection, run_id=RUN_ID, events=curve(7))
        event = read_events(connection, run_id=RUN_ID)[0]
        assert json.loads(event.summary_json)["capacityMw"] == 700
        assert event.best_fitness == round(-12.4 + 7, 6)


def test_a_duplicate_generation_is_refused(tmp_path: Path) -> None:
    """Swallowing it would let the persisted curve disagree with the
    ``convergence`` in the stored result, and nothing would notice until
    somebody compared the two."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        open_run(connection, submission())
        append_events(connection, run_id=RUN_ID, events=curve(1))
        with pytest.raises(DuplicateGenerationError):
            append_events(connection, run_id=RUN_ID, events=curve(1))


def test_a_rejected_batch_writes_none_of_itself(tmp_path: Path) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        open_run(connection, submission())
        append_events(connection, run_id=RUN_ID, events=curve(1))
        with pytest.raises(DuplicateGenerationError):
            append_events(connection, run_id=RUN_ID, events=curve(2, 3, 1))
        assert latest_generation(connection, run_id=RUN_ID) == 1


def test_an_event_for_an_unknown_run_is_refused(tmp_path: Path) -> None:
    with (
        closing(opened_store(tmp_path / "runs.db")) as connection,
        pytest.raises(RunNotFoundError),
    ):
        append_events(connection, run_id="01NOSUCHRUNNOSUCHRUNNOSUCH", events=curve(1))


def test_appending_nothing_writes_nothing(tmp_path: Path) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        open_run(connection, submission())
        assert append_events(connection, run_id=RUN_ID, events=()) == 0


def test_the_latest_generation_is_zero_before_the_search_reports(
    tmp_path: Path,
) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        open_run(connection, submission())
        assert latest_generation(connection, run_id=RUN_ID) == 0


def test_the_log_is_append_only_and_kept_with_its_run(tmp_path: Path) -> None:
    """§6's curves have to reopen with the run."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        open_run(connection, submission())
        append_events(connection, run_id=RUN_ID, events=curve(1))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE run_event SET best_fitness = 0.0")
        with pytest.raises(sqlite3.IntegrityError, match="kept with its run"):
            connection.execute("DELETE FROM run_event")


def test_a_curve_that_disagrees_with_the_result_is_refused(tmp_path: Path) -> None:
    """The log and the stored ``convergence`` are two records of one thing. If
    they diverge, the run is not what either of them says it is."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        append_events(connection, run_id=RUN_ID, events=curve(1, 2, 3))
        with pytest.raises(RunIdentityChangedError, match="convergence"):
            # `completed` carries a single convergence point.
            finish_run(connection, record=completed(stored.record), finished_at=CREATED_AT)
