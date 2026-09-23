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

from terrafolio.domain.results import ConvergencePoint
from terrafolio.store import (
    DuplicateGenerationError,
    RunAlreadyFinishedError,
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


def matching_curve() -> tuple[RunEvent, ...]:
    """The log a worker would have written for ``completed()``'s convergence.

    The two have to agree now: `finish_run` reconciles the persisted curve
    against the served one point by point.
    """
    return (RunEvent(generation=1, best_fitness=-12.4, mean_fitness=-31.2, summary_json="{}"),)


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
        with pytest.raises(DuplicateGenerationError) as caught:
            append_events(connection, run_id=RUN_ID, events=curve(2, 3, 1))
        # The generation it collided on, not the first one in the batch.
        assert caught.value.generation == 1
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


@pytest.mark.parametrize("fitness", [float("nan"), float("inf"), float("-inf")])
def test_a_fitness_that_is_not_a_number_is_refused(tmp_path: Path, fitness: float) -> None:
    """The result models set ``allow_inf_nan=False``; the storage layer says it
    again, because a curve is written by a worker and not every path there goes
    through a model. NaN needs no CHECK of its own -- SQLite stores it as NULL,
    which ``NOT NULL`` already refuses."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        open_run(connection, submission())
        event = RunEvent(generation=1, best_fitness=fitness, mean_fitness=0.0, summary_json="{}")
        with pytest.raises(sqlite3.IntegrityError):
            append_events(connection, run_id=RUN_ID, events=[event])
        assert latest_generation(connection, run_id=RUN_ID) == 0


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


def test_a_partial_curve_is_refused(tmp_path: Path) -> None:
    """The log and the stored ``convergence`` are two records of one thing. If
    they diverge, the run is not what either of them says it is."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        append_events(connection, run_id=RUN_ID, events=curve(1, 2, 3))
        with pytest.raises(RunIdentityChangedError, match="convergence"):
            # `completed` carries a single convergence point.
            finish_run(connection, record=completed(stored.record), finished_at=CREATED_AT)


def test_a_curve_of_the_right_length_but_the_wrong_values_is_refused(
    tmp_path: Path,
) -> None:
    """A length check passes a worker that logged the right number of wrong
    points, and the result would then show one shape live and another on
    reload."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        append_events(connection, run_id=RUN_ID, events=curve(1))
        record = completed(
            stored.record,
            convergence=(ConvergencePoint(generation=1, best_fitness=99.0, mean_fitness=99.0),),
        )
        with pytest.raises(RunIdentityChangedError, match="convergence"):
            finish_run(connection, record=record, finished_at=CREATED_AT)


def test_a_curve_logged_under_different_generation_numbers_is_refused(
    tmp_path: Path,
) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        append_events(connection, run_id=RUN_ID, events=curve(2))
        with pytest.raises(RunIdentityChangedError, match="convergence"):
            finish_run(connection, record=completed(stored.record), finished_at=CREATED_AT)


def test_a_curve_that_matches_the_result_is_accepted(tmp_path: Path) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        logged = curve(1, 2)
        append_events(connection, run_id=RUN_ID, events=logged)
        record = completed(
            stored.record,
            convergence=tuple(
                ConvergencePoint(
                    generation=event.generation,
                    best_fitness=event.best_fitness,
                    mean_fitness=event.mean_fitness,
                )
                for event in logged
            ),
        )
        assert finish_run(
            connection, record=record, finished_at=CREATED_AT
        ).generations_used == len(logged)


def test_a_run_with_no_subscriber_logs_nothing_and_still_finishes(
    tmp_path: Path,
) -> None:
    """An empty log is an absent one, not a disagreeing one: a run executed
    without anybody watching the stream — the CLI path — writes no events, and
    its curve reopens from the stored result."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        assert latest_generation(connection, run_id=RUN_ID) == 0
        done = finish_run(connection, record=completed(stored.record), finished_at=CREATED_AT)
        assert done.record.convergence


def test_the_log_closes_when_the_run_finishes(tmp_path: Path) -> None:
    """An event delivered after the result was served would grow the curve of a
    run whose result has already been read. The foreign key only asks whether
    the run exists, so the check is its own trigger."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        append_events(connection, run_id=RUN_ID, events=matching_curve())
        finish_run(connection, record=completed(stored.record), finished_at=CREATED_AT)
        with pytest.raises(RunAlreadyFinishedError, match="succeeded"):
            append_events(connection, run_id=RUN_ID, events=curve(2))
        assert latest_generation(connection, run_id=RUN_ID) == 1
