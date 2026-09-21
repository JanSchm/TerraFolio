"""Connections, pragmas and the backup command.

Three of the settings asserted here are invisible until they are wrong, and
each produces a symptom that looks like something else: a foreign key that is
decoration, an immutability trigger that one statement walks around, and a
reader that never sees the worker's appends and so looks like a stalled run.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_store_runs import CREATED_AT, RUN_ID, finished_run, opened_store

from terrafolio.config.hashing import ASSUMPTION_SET_ID_LENGTH
from terrafolio.store import (
    SCHEMA_VERSION,
    SchemaUnsupportedError,
    StoreError,
    backup_into,
    from_db_time,
    initialise,
    load_result_json,
    load_run,
    open_store,
    to_db_time,
    writing,
)
from terrafolio.store.db import read_schema_sql


def test_a_fresh_database_comes_up_in_wal_mode(tmp_path: Path) -> None:
    """One writer, many readers, and readers never blocked by the writer —
    which is what lets the stream tail a run while the worker appends to it."""
    with closing(open_store(tmp_path / "runs.db")) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_every_connection_enforces_foreign_keys(tmp_path: Path) -> None:
    """Off by default and per-connection, so this is not a property of the file."""
    with closing(open_store(tmp_path / "runs.db")) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(
                "INSERT INTO run_event (run_id, gen, best_fitness, mean_fitness, summary_json) "
                "VALUES ('01NOSUCHRUN', 1, 0.0, 0.0, '{}')"
            )


def test_every_connection_enables_recursive_triggers(tmp_path: Path) -> None:
    """Without it, the delete inside ``INSERT OR REPLACE`` does not fire a
    ``BEFORE DELETE`` trigger — so every append-only guarantee in the schema
    would hold only against callers that never use that statement."""
    with closing(open_store(tmp_path / "runs.db")) as connection:
        assert connection.execute("PRAGMA recursive_triggers").fetchone()[0] == 1


def test_initialise_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "runs.db"
    with closing(opened_store(path)) as connection:
        finished_run(connection)
        initialise(connection)
        assert load_run(connection, run_id=RUN_ID).record.run_ref == "A-1"


def test_a_database_from_a_newer_release_is_refused(tmp_path: Path) -> None:
    """A file written by a schema this release does not understand must not be
    opened hopefully: §12 keeps runs indefinitely, so old files outlive code."""
    path = tmp_path / "runs.db"
    with closing(open_store(path)) as connection:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with (
        closing(sqlite3.connect(path, autocommit=True)) as connection,
        pytest.raises(SchemaUnsupportedError, match="schema version"),
    ):
        initialise(connection)


def test_the_schema_is_packaged_alongside_the_code(tmp_path: Path) -> None:
    """Read as package data rather than relative to ``__file__``, so it still
    resolves from an installed wheel."""
    assert "CREATE TABLE IF NOT EXISTS run" in read_schema_sql()


def test_the_schema_and_the_hashing_module_agree_on_an_id_length() -> None:
    """The schema states the length as a CHECK, and it is the one number in it
    that duplicates a Python constant. A silent divergence is the failure the
    hashing module's own docstring is about: a run that cannot find its inputs."""
    assert f"length(assumption_set_id) = {ASSUMPTION_SET_ID_LENGTH}" in read_schema_sql()


# --------------------------------------------------------------------------
# Times
# --------------------------------------------------------------------------


def test_a_naive_time_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="timezone-aware"):
        to_db_time(datetime(2026, 9, 21, 9, 22, 11))


def test_a_stored_time_round_trips_and_sorts_chronologically() -> None:
    later = CREATED_AT + timedelta(days=1)
    assert from_db_time(to_db_time(CREATED_AT)) == CREATED_AT
    assert to_db_time(CREATED_AT) < to_db_time(later)


def test_a_time_in_another_zone_is_normalised_to_utc() -> None:
    """Two hosts in different zones must not produce two orderings."""
    elsewhere = CREATED_AT.astimezone(UTC) - timedelta(hours=0)
    assert to_db_time(elsewhere) == to_db_time(CREATED_AT)


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------


def test_the_writing_context_rolls_back_on_an_exception(tmp_path: Path) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        before = connection.execute("SELECT last_reference FROM run_sequence").fetchone()[0]
        with pytest.raises(RuntimeError), writing(connection) as transaction:
            transaction.execute("UPDATE run_sequence SET last_reference = last_reference + 1")
            raise RuntimeError("something went wrong mid-write")
        after = connection.execute("SELECT last_reference FROM run_sequence").fetchone()[0]
        assert after == before


def test_a_reader_sees_another_connection_s_commit_immediately(tmp_path: Path) -> None:
    """The reader must be in autocommit. Holding a transaction open across polls
    pins it to one WAL snapshot, and the run looks stalled rather than live."""
    path = tmp_path / "runs.db"
    with closing(opened_store(path)) as writer, closing(open_store(path)) as reader:
        assert reader.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 0
        finished_run(writer)
        assert reader.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 1


# --------------------------------------------------------------------------
# Backup
# --------------------------------------------------------------------------


def test_a_backup_is_a_readable_database_carrying_every_run(tmp_path: Path) -> None:
    path = tmp_path / "runs.db"
    destination = tmp_path / "archive" / "terrafolio.db"
    with closing(opened_store(path)) as connection:
        stored = finished_run(connection)
        backup_into(connection, destination)
    with closing(sqlite3.connect(destination, autocommit=True)) as copy:
        copy.row_factory = sqlite3.Row
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        # The check people forget: proves the copy is coherent, not merely
        # parseable.
        assert copy.execute("PRAGMA foreign_key_check").fetchall() == []
        assert load_result_json(copy, run_id=RUN_ID) == stored.result_json
        assert load_run(copy, run_id=RUN_ID).record == stored.record


def test_a_backup_excludes_work_that_has_not_been_committed(tmp_path: Path) -> None:
    """A hot backup takes a read transaction, so it is a consistent snapshot
    rather than a file copy that could catch a half-written run."""
    path = tmp_path / "runs.db"
    destination = tmp_path / "archive" / "terrafolio.db"
    with closing(opened_store(path)) as connection, closing(open_store(path)) as reader:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE run_sequence SET last_reference = 99")
        backup_into(reader, destination)
        connection.execute("ROLLBACK")
    with closing(sqlite3.connect(destination, autocommit=True)) as copy:
        assert copy.execute("SELECT last_reference FROM run_sequence").fetchone()[0] == 0


def test_a_backup_cannot_be_taken_inside_a_transaction(tmp_path: Path) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(StoreError, match="inside a transaction"):
                backup_into(connection, tmp_path / "copy.db")
        finally:
            connection.execute("ROLLBACK")


def test_a_backup_will_not_overwrite_an_existing_file(tmp_path: Path) -> None:
    """There is no overwrite in ``VACUUM INTO``, and an archive that silently
    replaced last night's is worse than one that refuses."""
    destination = tmp_path / "copy.db"
    destination.write_bytes(b"")
    with (
        closing(opened_store(tmp_path / "runs.db")) as connection,
        pytest.raises(StoreError, match="already exists"),
    ):
        backup_into(connection, destination)
