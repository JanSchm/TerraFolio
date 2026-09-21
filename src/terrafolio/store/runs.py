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
from typing import Any, Final

from terrafolio.config.hashing import canonical_json
from terrafolio.domain.enums import Effort, RunStatus
from terrafolio.domain.results import FeasibilityWarning, RunProvenance, RunRecord
from terrafolio.store.db import SCHEMA_VERSION, from_db_time, to_db_time, writing
from terrafolio.store.errors import (
    DuplicateRunError,
    RunAlreadyFinishedError,
    RunIdentityChangedError,
    RunNotFoundError,
    UnknownSnapshotError,
)
from terrafolio.store.events import latest_generation
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

TERMINAL_STATUSES: Final = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED})

_IN_FLIGHT_SQL: Final = "status IN ('queued', 'running')"


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
                    _assumption_snapshot_hash(transaction, provenance),
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
    return load_run(connection, run_id=identifier)


def _assumption_snapshot_hash(connection: sqlite3.Connection, provenance: RunProvenance) -> str:
    """The snapshot row this run's calibration was stored as.

    A set may have been snapshotted more than once under one
    ``assumption_set_id`` — the loader's id excludes ``[meta]`` by design — so
    the most recent snapshot carrying both the id and the hash is the one this
    run saw.
    """
    row = connection.execute(
        "SELECT snapshot_hash FROM assumption_set "
        "WHERE assumption_set_id = ? AND assumption_set_hash = ? "
        "ORDER BY recorded_at DESC LIMIT 1",
        (provenance.assumption_set_id, provenance.assumption_set_hash),
    ).fetchone()
    if row is None:
        raise UnknownSnapshotError("assumption-set", provenance.assumption_set_id)
    return str(row["snapshot_hash"])


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
        transaction.execute(
            "UPDATE run SET status = ?, result_json = ? WHERE run_id = ? AND status = 'queued'",
            (RunStatus.RUNNING.value, running.model_dump_json(), run_id),
        )
    return load_run(connection, run_id=run_id)


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
        raise RunAlreadyFinishedError(record.run_id, record.status.value)
    if record.duration_ms is None:
        raise RunIdentityChangedError(record.run_id, "durationMs", "a duration", None)
    payload = record.model_dump_json()
    with writing(connection) as transaction:
        stored = _row(transaction, record.run_id)
        _assert_same_run(stored, record)
        if RunStatus(stored["status"]) in TERMINAL_STATUSES:
            if stored["result_json"] == payload:
                return load_run(connection, run_id=record.run_id)
            raise RunAlreadyFinishedError(record.run_id, str(stored["status"]))
        eligible = tuple(json.loads(stored["eligible_ids_json"]))
        holdings = tuple(holding.id for holding in record.holdings)
        if record.status is RunStatus.SUCCEEDED and holdings != eligible:
            raise RunIdentityChangedError(record.run_id, "eligibleIds", eligible, holdings)
        logged = latest_generation(transaction, run_id=record.run_id)
        if logged and logged != len(record.convergence):
            raise RunIdentityChangedError(
                record.run_id, "convergence", logged, len(record.convergence)
            )
        transaction.execute(
            """
            UPDATE run SET
                status = ?, finished_at = ?, duration_ms = ?, generations_used = ?,
                selected_ids_json = ?, cashflow_30y_json = ?, cashflow_hold_json = ?,
                warnings_json = ?, error_code = ?, error_message = ?, result_json = ?
            WHERE run_id = ? AND status IN ('queued', 'running')
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
                None if failure is None else failure.code,
                None if failure is None else failure.message,
                payload,
                record.run_id,
            ),
        )
    return load_run(connection, run_id=record.run_id)


def _assert_same_run(stored: sqlite3.Row, record: RunRecord) -> None:
    """Every input fixed at submission must still say the same thing."""
    offered: dict[str, Any] = {
        "runRef": record.run_ref,
        "createdAt": to_db_time(record.created_at),
        "effort": record.effort.value,
        "mandate": record.mandate.model_dump_json(),
        "lockedIds": _ids_json(record.locked_ids),
        "excludedIds": _ids_json(record.excluded_ids),
        "seed": record.provenance.seed,
        "pipelineHash": record.provenance.pipeline_hash,
        "assumptionSetId": record.provenance.assumption_set_id,
    }
    columns = {
        "runRef": "run_ref",
        "createdAt": "created_at",
        "effort": "effort",
        "mandate": "mandate_json",
        "lockedIds": "locked_ids_json",
        "excludedIds": "excluded_ids_json",
        "seed": "seed",
        "pipelineHash": "pipeline_hash",
        "assumptionSetId": "assumption_set_id",
    }
    for field, column in columns.items():
        if stored[column] != offered[field]:
            raise RunIdentityChangedError(record.run_id, field, stored[column], offered[field])


def _row(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    cursor = connection.execute("SELECT * FROM run WHERE run_id = ?", (run_id,))
    row: sqlite3.Row | None = cursor.fetchone()
    if row is None:
        raise RunNotFoundError(run_id)
    return row


def _stored_run(connection: sqlite3.Connection, row: sqlite3.Row) -> StoredRun:
    base_year = connection.execute(
        "SELECT base_year FROM pipeline_snapshot WHERE pipeline_hash = ?", (row["pipeline_hash"],)
    ).fetchone()
    warnings = tuple(
        FeasibilityWarning.model_validate(item) for item in json.loads(row["warnings_json"])
    )
    return StoredRun(
        record=RunRecord.model_validate_json(row["result_json"]),
        result_json=row["result_json"],
        run_reference=row["run_reference"],
        created_by=row["created_by"],
        finished_at=None if row["finished_at"] is None else from_db_time(row["finished_at"]),
        population_size=row["population_size"],
        generations_planned=row["generations_planned"],
        generations_used=row["generations_used"],
        eligible_ids=tuple(json.loads(row["eligible_ids_json"])),
        warnings_raised=warnings,
        base_year=int(base_year["base_year"]),
        assumption_snapshot_hash=row["assumption_snapshot_hash"],
        deterministic_reduction=bool(row["deterministic_reduction"]),
        error_code=row["error_code"],
        error_message=row["error_message"],
        schema_version=row["schema_version"],
    )


def load_run(connection: sqlite3.Connection, *, run_id: str) -> StoredRun:
    """The stored run, with the audit fields the wire contract does not carry."""
    return _stored_run(connection, _row(connection, run_id))


def load_run_by_ref(connection: sqlite3.Connection, *, run_ref: str) -> StoredRun:
    """The same, addressed by the label the export carries (§7.6)."""
    row = connection.execute("SELECT * FROM run WHERE run_ref = ?", (run_ref,)).fetchone()
    if row is None:
        raise RunNotFoundError(run_ref)
    return _stored_run(connection, row)


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
    """Newest first, paginated on the reference rather than on a clock."""
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
