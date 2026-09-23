"""The run lifecycle: open at submission, finish once, never again.

``POST /optimisations`` answers **202 with a run id** before any result exists
(``api.md`` §6), and a run still in flight answers ``GET /optimisations/{id}``
with a valid body (§8). So a run is a real, readable record from the moment it
is queued, and the reference it carries into its export has to be minted then
too — which is why the store hands out the number before the row is written
rather than reading it back afterwards.

Everything the run needs in order to be reproducible is therefore known at
submission: the resolved seed above all. ``api.md`` §6.2 already requires the
server to draw and record one when the client sends none, and epic §5 is blunt
that a run with no recorded seed is not a valid run — so the seed is drawn in
the request handler, not in the worker.

``result_json`` is the truth and is never re-serialised on read. The other
columns are indexes over those bytes, and ``schema.sql``'s ``json_extract``
CHECKs make a divergent write impossible rather than merely unlikely.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from typing import Final

from pydantic import BaseModel

from terrafolio.config.hashing import canonical_json
from terrafolio.domain.enums import Effort, RunStatus
from terrafolio.domain.results import FeasibilityWarning, RunRecord
from terrafolio.store.db import SCHEMA_VERSION, from_db_time, to_db_time, writing
from terrafolio.store.errors import (
    DuplicateRunError,
    RunAlreadyFinishedError,
    RunIdentityChangedError,
    RunNotFinishedError,
    RunNotFoundError,
    StoreError,
    UnknownSnapshotError,
)
from terrafolio.store.events import read_events
from terrafolio.store.ids import RUN_REF_SERIES, format_run_ref, new_ulid
from terrafolio.store.records import RunFailure, RunSubmission, RunSummary, StoredRun

__all__ = [
    "finish_run",
    "in_flight_runs",
    "list_runs",
    "load_result_json",
    "load_run",
    "load_run_by_ref",
    "open_run",
    "start_run",
]

IN_FLIGHT_STATUSES: Final = frozenset({RunStatus.QUEUED, RunStatus.RUNNING})
TERMINAL_STATUSES: Final = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED})
"""The two halves of ``RunStatus``. A test asserts they partition it, so a
status added later cannot land in neither and be silently treated as in-flight
by one check and terminal by another."""


def status_list_sql(statuses: frozenset[RunStatus]) -> str:
    """A status set as a SQL list, so the vocabulary is written once in Python.

    ``schema.sql`` states it a second time, in a CHECK and two triggers, which
    SQL gives no way to derive; a test compares the two.
    """
    return ", ".join(f"'{status.value}'" for status in sorted(statuses))


_IN_FLIGHT_SQL: Final = f"status IN ({status_list_sql(IN_FLIGHT_STATUSES)})"


def _ids_json(ids: Sequence[str]) -> str:
    return canonical_json(list(ids))


def _warnings_json(warnings: Sequence[FeasibilityWarning]) -> str:
    """Warnings as JSON, with every code by **name**.

    ``WarningCode`` is an ``IntEnum``, and C-9 requires that no stored artefact
    carries the ordinal: runs are addressable indefinitely, so a code whose
    number shifted in a later release would rewrite the meaning of a stored run.
    ``WarningCodeName``'s serialiser is what guarantees the name here.
    """
    return canonical_json([warning.model_dump(mode="json") for warning in warnings])


def _next_reference(connection: sqlite3.Connection, series: str) -> int:
    """Claim the next reference. Must run inside an IMMEDIATE transaction.

    The write lock is already held by the time this reads, so the increment is
    serialised against every other process — which is the whole reason the
    reference comes from a counter rather than from ``MAX() + 1``.
    """
    row = connection.execute(
        "UPDATE run_sequence SET last_reference = last_reference + 1 "
        "WHERE series = ? RETURNING last_reference",
        (series,),
    ).fetchone()
    if row is None:
        raise UnknownSnapshotError("reference series", series)
    return int(row["last_reference"])


def open_run(
    connection: sqlite3.Connection,
    submission: RunSubmission,
    *,
    series: str = RUN_REF_SERIES,
) -> StoredRun:
    """Mint the id and the reference, and write the queued run.

    One IMMEDIATE transaction: claim the reference, build the record, insert.
    The record this returns is byte-for-byte what ``GET /optimisations/{id}``
    serves until the run finishes.
    """
    provenance = submission.provenance
    identifier = new_ulid() if submission.run_id is None else submission.run_id
    try:
        with writing(connection) as transaction:
            _verify_snapshot_matches_provenance(transaction, submission)
            reference = _next_reference(transaction, series)
            record = RunRecord(
                run_id=identifier,
                run_ref=format_run_ref(reference, series=series),
                status=RunStatus.QUEUED,
                created_at=submission.created_at,
                duration_ms=None,
                mandate=submission.mandate,
                locked_ids=tuple(submission.locked_ids),
                excluded_ids=tuple(submission.excluded_ids),
                effort=submission.effort,
                selected_ids=(),
                aggregates=None,
                holdings=(),
                # The serialisation aliases, not the attribute names: A-6
                # keeps the 30-year series and the hold-truncated one apart
                # on the wire, and these are the wire's names for them.
                cashflow30Y_m=(),
                cashflowHold_m=(),
                convergence=(),
                provenance=provenance,
            )
            result_json = record.model_dump_json()
            transaction.execute(
                """
                INSERT INTO run (
                    run_id, run_reference, run_ref, status, created_at, created_by,
                    finished_at, duration_ms, mandate_json, effort,
                    locked_ids_json, excluded_ids_json, eligible_ids_json,
                    seed, pipeline_hash, assumption_snapshot_hash, assumption_set_id,
                    engine_version, numpy_version, blas_threads, deterministic_reduction,
                    python_version, platform, population_size, generations_planned,
                    generations_used, selected_ids_json, cashflow_30y_json,
                    cashflow_hold_json, warnings_json, error_code, error_message,
                    schema_version, result_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, NULL, ?, ?, ?, ?, NULL, NULL, ?, ?
                )
                """,
                (
                    identifier,
                    reference,
                    record.run_ref,
                    record.status.value,
                    to_db_time(submission.created_at),
                    submission.created_by,
                    record.mandate.model_dump_json(),
                    submission.effort.value,
                    _ids_json(record.locked_ids),
                    _ids_json(record.excluded_ids),
                    _ids_json(sorted(set(submission.eligible_ids))),
                    provenance.seed,
                    provenance.pipeline_hash,
                    submission.assumption_snapshot_hash,
                    provenance.assumption_set_id,
                    provenance.engine_version,
                    provenance.numpy_version,
                    provenance.blas_threads,
                    int(submission.deterministic_reduction),
                    provenance.python_version,
                    provenance.platform,
                    submission.population_size,
                    submission.generations_planned,
                    _ids_json(()),
                    canonical_json([]),
                    canonical_json([]),
                    canonical_json([]),
                    SCHEMA_VERSION,
                    result_json,
                ),
            )
    except sqlite3.IntegrityError as error:
        message = str(error)
        if "FOREIGN KEY" in message:
            raise UnknownSnapshotError(
                "pipeline or assumption-set", provenance.pipeline_hash
            ) from error
        if "run.run_id" in message:
            raise DuplicateRunError(identifier) from error
        raise
    return _audit(connection, identifier, record, result_json)


def _failure_columns(failure: RunFailure | None) -> tuple[str | None, str | None]:
    """The error pair as the row stores it."""
    return (None, None) if failure is None else (failure.code, failure.message)


def _assert_curve_agrees(connection: sqlite3.Connection, record: RunRecord) -> None:
    """The persisted curve and the one in the result must be the same curve.

    They are two records of one search: `run_event` is what a subscriber
    replayed while it ran, `convergence` is what `GET /optimisations/{id}`
    serves afterwards. §6 requires the curve to reopen with the run, so a
    difference between them is a run that shows one shape live and another on
    reload — and nothing would notice until somebody compared the two.

    Compared generation by generation, including the fitness values: a length
    check passes a worker that logged the right number of wrong points.

    An **empty** log is allowed. It means the run was executed without a
    subscriber — the CLI path, and any run whose stream nobody opened — which
    is an absent log rather than a disagreeing one. A partial log is not
    allowed, and is what the comparison catches.
    """
    logged = read_events(connection, run_id=record.run_id)
    if not logged:
        return
    persisted = tuple(
        (event.generation, event.best_fitness, event.mean_fitness) for event in logged
    )
    served = tuple(
        (point.generation, point.best_fitness, point.mean_fitness) for point in record.convergence
    )
    if persisted != served:
        at = _first_difference(persisted, served)
        raise RunIdentityChangedError(
            record.run_id,
            "convergence",
            _describe_curve(persisted, at),
            _describe_curve(served, at),
        )


_Curve = tuple[tuple[int, float, float], ...]


def _first_difference(persisted: _Curve, served: _Curve) -> int:
    """The index the two curves first disagree at."""
    for index, (left, right) in enumerate(zip(persisted, served, strict=False)):
        if left != right:
            return index
    return min(len(persisted), len(served))


def _describe_curve(curve: _Curve, at: int) -> str:
    """A curve summarised *at the point it differs*.

    Length and endpoints alone are not enough: two curves that diverge in the
    middle produce the same string on both sides of the message, and the error
    then proves a disagreement exists while withholding where it is.
    """
    if not curve:
        return "no generations"
    span = f"{curve[0][0]}-{curve[-1][0]}" if len(curve) > 1 else str(curve[0][0])
    if at >= len(curve):
        return f"{len(curve)} generations ({span}), nothing at index {at}"
    generation, best, mean = curve[at]
    return (
        f"{len(curve)} generations ({span}); at index {at}, "
        f"generation {generation} best {best} mean {mean}"
    )


def _verify_snapshot_matches_provenance(
    connection: sqlite3.Connection, submission: RunSubmission
) -> None:
    """The named snapshot must be the calibration the provenance describes.

    The foreign key proves the snapshot exists; this proves it is the right
    one. Without it a caller could name snapshot A while the record it serves
    claims the id and hash of snapshot B, and every column would still agree
    with itself.
    """
    row = connection.execute(
        "SELECT assumption_set_id, assumption_set_hash FROM assumption_set WHERE snapshot_hash = ?",
        (submission.assumption_snapshot_hash,),
    ).fetchone()
    if row is None:
        raise UnknownSnapshotError("assumption-set", submission.assumption_snapshot_hash)
    provenance = submission.provenance
    if (row["assumption_set_id"], row["assumption_set_hash"]) != (
        provenance.assumption_set_id,
        provenance.assumption_set_hash,
    ):
        raise UnknownSnapshotError(
            "assumption-set",
            f"{submission.assumption_snapshot_hash} carries "
            f"{row['assumption_set_id']}/{row['assumption_set_hash']}, but the run claims "
            f"{provenance.assumption_set_id}/{provenance.assumption_set_hash}",
        )


def start_run(connection: sqlite3.Connection, *, run_id: str) -> StoredRun:
    """``queued`` -> ``running``. A no-op on a run that is already running.

    A supervisor re-delivering a start signal is normal; a start on a finished
    run is not, and raises.
    """
    with writing(connection) as transaction:
        stored = _row(transaction, run_id)
        status = RunStatus(stored["status"])
        if status is RunStatus.RUNNING:
            return load_run(connection, run_id=run_id)
        if status in TERMINAL_STATUSES:
            raise RunAlreadyFinishedError(run_id, status.value)
        record = RunRecord.model_validate_json(stored["result_json"])
        running = record.model_copy(update={"status": RunStatus.RUNNING})
        payload = running.model_dump_json()
        transaction.execute(
            "UPDATE run SET status = ?, result_json = ? "
            f"WHERE run_id = ? AND status = '{RunStatus.QUEUED.value}'",
            (RunStatus.RUNNING.value, payload, run_id),
        )
    return _audit(connection, run_id, running, payload)


def finish_run(
    connection: sqlite3.Connection,
    *,
    record: RunRecord,
    finished_at: datetime,
    warnings_raised: Sequence[FeasibilityWarning] = (),
    failure: RunFailure | None = None,
) -> StoredRun:
    """Write the terminal result of a run that was opened earlier.

    The caller builds the completed ``RunRecord``: it owns the aggregates, the
    holdings and the two cash-flow series, and the model is what validates
    them. The store's job is to prove this is the *same run* that was opened —
    identity, inputs and provenance all have to match what was recorded at
    submission, or nothing is written.

    Calling this twice is the case worth knowing about. If the bytes are
    identical it is a redelivered acknowledgement and the stored run comes back
    unchanged; if they differ, two workers believe they own the run, and that
    raises rather than quietly rewriting an audit record.
    """
    if record.status not in TERMINAL_STATUSES:
        raise RunNotFinishedError(record.run_id, f"the record is {record.status.value}")
    if record.duration_ms is None:
        # §12 audits how long a run took, and the schema pairs finished_at with
        # duration_ms, so a result that reports neither cannot be stored.
        raise RunNotFinishedError(record.run_id, "the record reports no duration")
    payload = record.model_dump_json()
    with writing(connection) as transaction:
        stored = _row(transaction, record.run_id)
        _assert_same_run(stored, record)
        if RunStatus(stored["status"]) in TERMINAL_STATUSES:
            # A redelivery repeats the whole outcome, not just the record: the
            # failure reason and the warnings are stored beside `result_json`
            # and are not in it, so two calls carrying one record and different
            # reasons are two different outcomes.
            was = (stored["result_json"], stored["error_code"], stored["error_message"])
            now = (payload, *_failure_columns(failure))
            if was == now and stored["warnings_json"] == _warnings_json(warnings_raised):
                return load_run(connection, run_id=record.run_id)
            raise RunAlreadyFinishedError(record.run_id, str(stored["status"]))
        eligible = tuple(json.loads(stored["eligible_ids_json"]))
        holdings = tuple(holding.id for holding in record.holdings)
        if record.status is RunStatus.SUCCEEDED and holdings != eligible:
            raise RunIdentityChangedError(record.run_id, "eligibleIds", eligible, holdings)
        _assert_curve_agrees(transaction, record)
        transaction.execute(
            f"""
            UPDATE run SET
                status = ?, finished_at = ?, duration_ms = ?, generations_used = ?,
                selected_ids_json = ?, cashflow_30y_json = ?, cashflow_hold_json = ?,
                warnings_json = ?, error_code = ?, error_message = ?, result_json = ?
            WHERE run_id = ? AND {_IN_FLIGHT_SQL}
            """,
            (
                record.status.value,
                to_db_time(finished_at),
                record.duration_ms,
                len(record.convergence),
                _ids_json(record.selected_ids),
                canonical_json(list(record.cashflow_30y_m)),
                canonical_json(list(record.cashflow_hold_m)),
                _warnings_json(warnings_raised),
                *_failure_columns(failure),
                payload,
                record.run_id,
            ),
        )
    return _audit(connection, record.run_id, record, payload)


def _assert_same_run(stored: sqlite3.Row, record: RunRecord) -> None:
    """Every input fixed at submission must still say the same thing.

    Compared against the record the run was *opened* with — parsed back out of
    the row rather than against the denormalised columns, because those cover
    only three of the ten provenance fields. A worker returning a different
    ``fileHashes``, ``numpyVersion`` or ``blasThreads`` would otherwise be
    stored happily, and the run would claim to be reproducible from inputs it
    never saw.
    """
    opened = RunRecord.model_validate_json(stored["result_json"])
    checks: tuple[tuple[str, object, object], ...] = (
        ("runId", opened.run_id, record.run_id),
        ("runRef", opened.run_ref, record.run_ref),
        ("createdAt", opened.created_at, record.created_at),
        ("effort", opened.effort, record.effort),
        ("mandate", opened.mandate, record.mandate),
        ("lockedIds", opened.locked_ids, record.locked_ids),
        ("excludedIds", opened.excluded_ids, record.excluded_ids),
        ("provenance", opened.provenance, record.provenance),
    )
    for field, was, offered in checks:
        if was != offered:
            raise RunIdentityChangedError(record.run_id, field, _brief(was), _brief(offered))


def _brief(value: object) -> str:
    """A value short enough to read in an error message."""
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    return str(value)


def _row(connection: sqlite3.Connection, value: str, *, column: str = "run_id") -> sqlite3.Row:
    """The run row addressed by ``run_id`` or by ``run_ref``.

    Parameterised rather than written twice: the not-found path and any future
    change to what is selected then cannot reach one caller and miss the other.
    ``column`` is never caller-supplied — the two call sites pass a literal.
    """
    cursor = connection.execute(f"SELECT * FROM run WHERE {column} = ?", (value,))
    row: sqlite3.Row | None = cursor.fetchone()
    if row is None:
        raise RunNotFoundError(value)
    return row


_AUDIT_COLUMNS: Final = (
    "run_reference, created_by, finished_at, population_size, generations_planned, "
    "generations_used, eligible_ids_json, warnings_json, assumption_snapshot_hash, "
    "deterministic_reduction, error_code, error_message, schema_version, pipeline_hash"
)
"""Everything a ``StoredRun`` needs except the record itself.

Named so the mutating calls can read them **without** ``result_json``: they
already hold the record and its bytes, and re-selecting a payload that
``api.md`` §8.2 sizes at ~400 KB only to parse it a second time is a round trip
and a parse per run on the path that answers the user's search.
"""


def _compose(
    connection: sqlite3.Connection, row: sqlite3.Row, record: RunRecord, payload: str
) -> StoredRun:
    """A ``StoredRun`` from a record already in hand and its audit columns."""
    snapshot = connection.execute(
        "SELECT base_year FROM pipeline_snapshot WHERE pipeline_hash = ?", (row["pipeline_hash"],)
    ).fetchone()
    if snapshot is None:
        # Unreachable through this package -- the foreign key and the
        # never-delete trigger both forbid it -- but `load_run` is also pointed
        # at backup copies and at databases #9 did not create, and an opaque
        # TypeError from a read path is not something §11's 500 can key on.
        raise UnknownSnapshotError("pipeline", str(row["pipeline_hash"]))
    return StoredRun(
        record=record,
        result_json=payload,
        run_reference=row["run_reference"],
        created_by=row["created_by"],
        finished_at=None if row["finished_at"] is None else from_db_time(row["finished_at"]),
        population_size=row["population_size"],
        generations_planned=row["generations_planned"],
        generations_used=row["generations_used"],
        eligible_ids=tuple(json.loads(row["eligible_ids_json"])),
        warnings_raised=tuple(
            FeasibilityWarning.model_validate(item) for item in json.loads(row["warnings_json"])
        ),
        base_year=int(snapshot["base_year"]),
        assumption_snapshot_hash=row["assumption_snapshot_hash"],
        deterministic_reduction=bool(row["deterministic_reduction"]),
        error_code=row["error_code"],
        error_message=row["error_message"],
        schema_version=row["schema_version"],
    )


def _audit(
    connection: sqlite3.Connection, run_id: str, record: RunRecord, payload: str
) -> StoredRun:
    """The stored run, without re-reading or re-parsing the record we just wrote."""
    cursor = connection.execute(
        f"SELECT {_AUDIT_COLUMNS} FROM run WHERE run_id = ?",
        (run_id,),
    )
    row: sqlite3.Row | None = cursor.fetchone()
    if row is None:
        raise RunNotFoundError(run_id)
    return _compose(connection, row, record, payload)


def _stored_run(connection: sqlite3.Connection, row: sqlite3.Row) -> StoredRun:
    payload = str(row["result_json"])
    return _compose(connection, row, RunRecord.model_validate_json(payload), payload)


def load_run(connection: sqlite3.Connection, *, run_id: str) -> StoredRun:
    """The stored run, with the audit fields the wire contract does not carry."""
    return _stored_run(connection, _row(connection, run_id))


def load_run_by_ref(connection: sqlite3.Connection, *, run_ref: str) -> StoredRun:
    """The same, addressed by the label the export carries (§7.6)."""
    return _stored_run(connection, _row(connection, run_ref, column="run_ref"))


def load_result_json(connection: sqlite3.Connection, *, run_id: str) -> str:
    """The exact bytes ``GET /optimisations/{id}`` serves.

    No pydantic on this path on purpose. If the API re-validates and
    re-serialises these bytes, §11's "the same id always reopens the same
    result" holds only as long as no dependency ever changes how a float or a
    timestamp is rendered — so the endpoint should return this string as the
    response body rather than a model.
    """
    return str(_row(connection, run_id)["result_json"])


def _summary(row: sqlite3.Row) -> RunSummary:
    return RunSummary(
        run_id=row["run_id"],
        run_reference=row["run_reference"],
        run_ref=row["run_ref"],
        status=RunStatus(row["status"]),
        created_at=from_db_time(row["created_at"]),
        created_by=row["created_by"],
        effort=Effort(row["effort"]),
        duration_ms=row["duration_ms"],
    )


def list_runs(
    connection: sqlite3.Connection, *, limit: int, before_reference: int | None = None
) -> tuple[RunSummary, ...]:
    """Newest first, paginated on the reference rather than on a clock.

    A negative ``limit`` is refused rather than passed through: SQLite reads it
    as *no* limit, so a caller computing a remaining page size and reaching -1
    would receive every run ever stored, each carrying its own result payload.
    """
    if limit < 0:
        raise StoreError(f"a page size cannot be negative; got {limit}")
    sql = "SELECT * FROM run"
    parameters: tuple[object, ...] = ()
    if before_reference is not None:
        sql += " WHERE run_reference < ?"
        parameters = (before_reference,)
    sql += " ORDER BY run_reference DESC LIMIT ?"
    return tuple(_summary(row) for row in connection.execute(sql, (*parameters, limit)))


def in_flight_runs(connection: sqlite3.Connection) -> tuple[RunSummary, ...]:
    """Everything queued or running — what a restarted runner reconciles."""
    return tuple(
        _summary(row)
        for row in connection.execute(
            f"SELECT * FROM run WHERE {_IN_FLIGHT_SQL} ORDER BY run_reference"
        )
    )
