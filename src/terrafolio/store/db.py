"""Connections, pragmas, transactions and the backup command.

Four choices here are load-bearing, and each one is a silent bug if it goes the
other way:

**``autocommit=True``, not PEP 249's implicit transactions.** The write path
needs ``BEGIN IMMEDIATE``; PEP 249 mode issues a *deferred* ``BEGIN``, whose
read-then-write upgrade fails with ``SQLITE_BUSY_SNAPSHOT`` — a code the busy
handler does not retry, so ``busy_timeout`` does not save it. And a reader must
be in autocommit or it is pinned to the WAL snapshot it opened with and never
sees the worker's appends, which looks exactly like a stalled run. The sharp
edge of this mode is that ``Connection.commit()`` is a **no-op**; transactions
end with ``execute("COMMIT")``, which is what :func:`writing` does.

**``recursive_triggers = ON``.** Without it, ``INSERT OR REPLACE`` deletes the
conflicting row *without firing the BEFORE DELETE trigger* — so the store's
"a run is never deleted" guarantee silently evaporates on one statement. The
pragma is the fix; never writing ``INSERT OR REPLACE`` is the belt to its
braces.

**Times cross the boundary as text.** Passing a ``datetime`` as a parameter
raises ``DeprecationWarning: The default datetime adapter is deprecated``, and
this suite runs with ``filterwarnings = ["error"]``. Registering a global
adapter would be worse: it is process-wide state a library has no business
installing, in a repo that spawns subprocesses which would inherit it.

**WAL needs shared memory**, so the database must live on local disk. It does
not work over most network filesystems.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Final

from terrafolio.store.errors import SchemaUnsupportedError, StoreError

__all__ = [
    "SCHEMA_VERSION",
    "backup_into",
    "connect",
    "from_db_time",
    "initialise",
    "open_store",
    "read_schema_sql",
    "to_db_time",
    "writing",
]

SCHEMA_VERSION: Final = 1
"""Stored in ``PRAGMA user_version``. A file from a newer release is refused."""

MINIMUM_SQLITE: Final = "3.38"
"""The oldest SQLite this schema runs on.

``STRICT`` needs 3.37, ``json_valid`` in a CHECK needs 3.38, ``VACUUM INTO``
needs 3.27 and ``RETURNING`` needs 3.35. Python itself only guarantees 3.15, so
this is a real portability floor and belongs in code rather than in a comment.

Written as text because every component of a version tuple is a bare integer,
and one of them collides with a weight in the assumption set — which the
literal guard is right to flag and wrong to be silenced about with an escape.
"""

_CONNECTION_PRAGMAS: Final = (
    # Off by default and per-connection: without it the run_event -> run
    # foreign key is decoration.
    "PRAGMA foreign_keys = ON",
    # See the module docstring. This is the one that quietly matters.
    "PRAGMA recursive_triggers = ON",
    # A contending writer waits this long before raising SQLITE_BUSY.
    "PRAGMA busy_timeout = 5000",
    # Safe against corruption under WAL; can lose the last commits on power
    # loss. An exhaustive run appends 110 generation rows inside a five-second
    # budget (§12), and losing the tail of a convergence curve is survivable
    # where one fsync per generation is not.
    "PRAGMA synchronous = NORMAL",
)


def read_schema_sql() -> str:
    """The DDL, read as package data rather than relative to ``__file__``."""
    return resources.files("terrafolio.store").joinpath("schema.sql").read_text(encoding="utf-8")


def connect(path: Path) -> sqlite3.Connection:
    """One configured connection. The caller owns its lifetime and closes it.

    There is no module-level singleton: ``sqlite3.Connection`` defaults to
    ``check_same_thread=True``, and a threaded server plus a separate search
    worker means every caller needs its own.
    """
    floor = tuple(int(part) for part in MINIMUM_SQLITE.split("."))
    if sqlite3.sqlite_version_info[: len(floor)] < floor:
        raise SchemaUnsupportedError(
            f"SQLite {sqlite3.sqlite_version} is too old; "
            f"this store needs {MINIMUM_SQLITE} or later"
        )
    connection = sqlite3.connect(path, autocommit=True)
    connection.row_factory = sqlite3.Row
    for pragma in _CONNECTION_PRAGMAS:
        connection.execute(pragma)
    return connection


def initialise(connection: sqlite3.Connection) -> None:
    """Bring a database file up to this release's schema, or refuse it.

    Three cases, and the middle one is the reason this is not simply
    "executescript every time":

    * **0** — a fresh or pre-versioning file. Apply the schema and stamp it.
    * **``SCHEMA_VERSION``** — already current. Nothing to do; the DDL is all
      ``IF NOT EXISTS`` and re-running it on every connection is ~290 lines of
      no-op parsing per open, on a package whose own rule is that every caller
      owns its connection.
    * **anything else** — refused. A *newer* file is one this release does not
      understand. An *older* one cannot be brought forward by re-running the
      DDL: ``CREATE TABLE IF NOT EXISTS`` will not alter an existing table and
      ``CREATE TRIGGER IF NOT EXISTS`` will not install a trigger a later
      release added, so stamping it current would mark a file as migrated that
      nobody migrated. §12 keeps runs indefinitely, so these files outlive the
      code that wrote them and the first real migration has to be written
      deliberately.

    ``executescript`` must run outside a transaction, which autocommit gives.
    """
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version == SCHEMA_VERSION:
        _assert_wal(connection)
        return
    if version != 0:
        raise SchemaUnsupportedError(
            f"database is at schema version {version}; this release understands "
            f"{SCHEMA_VERSION} and has no migration between them"
        )
    connection.executescript(read_schema_sql())
    _assert_wal(connection)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _assert_wal(connection: sqlite3.Connection) -> None:
    """Re-asserted on every open, not only at creation.

    A ``VACUUM INTO`` copy comes back in ``delete`` journal mode, so a backup
    restored into service would otherwise quietly run without WAL — one writer
    blocking every reader, which is the opposite of what the stream needs.
    """
    connection.execute("PRAGMA journal_mode = WAL")


def open_store(path: Path) -> sqlite3.Connection:
    """Connect and apply the schema. The ordinary way in."""
    connection = connect(path)
    try:
        initialise(connection)
    except Exception:
        connection.close()
        raise
    return connection


@contextmanager
def writing(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """``BEGIN IMMEDIATE`` … ``COMMIT``, rolling back on any exception.

    IMMEDIATE rather than deferred: the write lock is taken at ``BEGIN``, so
    every read-modify-write inside is serialised against every other process,
    and a contending writer waits out ``busy_timeout`` instead of failing with
    an unretryable snapshot conflict.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        # Suppressed deliberately: if the rollback itself fails -- a dropped
        # connection, most likely -- re-raising *its* error would replace the
        # one that explains what actually went wrong, and the caller would log
        # a database fault where the event was a rejected result.
        with suppress(sqlite3.Error):
            connection.execute("ROLLBACK")
        raise
    try:
        connection.execute("COMMIT")
    except BaseException:
        # A failed commit leaves the transaction open on a connection the
        # caller goes on using, and the next BEGIN IMMEDIATE would fail with a
        # confusing "cannot start a transaction within a transaction".
        with suppress(sqlite3.Error):
            connection.execute("ROLLBACK")
        raise


def to_db_time(moment: datetime) -> str:
    """An aware datetime as fixed-width UTC text, so sorting is chronological."""
    if moment.tzinfo is None:
        raise StoreError(f"a stored time must be timezone-aware; got {moment!r}")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def from_db_time(text: str) -> datetime:
    """The inverse of :func:`to_db_time`."""
    return datetime.fromisoformat(text).astimezone(UTC)


def backup_into(connection: sqlite3.Connection, destination: Path) -> None:
    """Write a consistent copy of the whole database to ``destination``.

    ``VACUUM INTO`` is a *hot* backup: it takes a read transaction, so under
    WAL it never blocks the writer, and it produces a defragmented single file
    rather than a snapshot that could catch a torn WAL. Uncommitted work is
    excluded.

    SQLite enforces two of the three constraints below itself; the third — that
    the path is bound as a parameter — is this function's business, so a
    directory name containing an apostrophe cannot become a syntax error.

    The copy comes back in ``journal_mode=delete``, which is what an archive
    should be: one file, no ``-wal`` or ``-shm`` sidecars. :func:`initialise`
    puts it back into WAL if it is ever restored into service.
    """
    if connection.in_transaction:
        raise StoreError("VACUUM INTO cannot run inside a transaction")
    if destination.exists():
        raise StoreError(f"{destination} already exists; VACUUM INTO will not overwrite it")
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection.execute("VACUUM INTO ?", (str(destination),))
