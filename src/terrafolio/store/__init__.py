"""SQLite store: assumption_set, pipeline_snapshot, run, run_event (issue 2B).

The one import surface for ``runner``, ``api`` and ``export``. Every function
takes a connection as its first argument and the caller owns its lifetime —
there is no module-level singleton, because ``sqlite3.Connection`` is
single-threaded by default and a threaded server plus a separate search worker
means every caller needs its own.

The shape of a run's life::

    connection = open_store(path)
    snapshot_hash = record_assumption_set(connection, assumptions, recorded_at=now)
    record_pipeline_snapshot(connection, snapshot, recorded_at=now)
    # `RunSubmission.assumption_snapshot_hash` is that returned hash: two
    # calibrations differing only in `[meta]` share an id and a hash, so the
    # snapshot cannot be inferred from provenance.
    stored = open_run(connection, submission)      # 202 answers from here
    start_run(connection, run_id=stored.record.run_id)
    append_events(connection, run_id=..., events=[...])   # from the worker
    stored = finish_run(connection, record=result, finished_at=now)

and from then on the run is immutable: §11 makes it the audit trail an
investment committee has already seen, and the triggers in ``schema.sql``
refuse to let it change.
"""

from terrafolio.store.db import (
    SCHEMA_VERSION,
    backup_into,
    connect,
    from_db_time,
    initialise,
    open_store,
    to_db_time,
    writing,
)
from terrafolio.store.errors import (
    DuplicateGenerationError,
    DuplicateRunError,
    RunAlreadyFinishedError,
    RunIdentityChangedError,
    RunNotFinishedError,
    RunNotFoundError,
    SchemaUnsupportedError,
    SnapshotConflictError,
    StoreError,
    UnknownSnapshotError,
)
from terrafolio.store.events import append_events, latest_generation, read_events
from terrafolio.store.ids import RUN_REF_SERIES, format_run_ref, new_ulid, parse_run_ref
from terrafolio.store.records import (
    AssumptionSnapshot,
    PipelineSnapshot,
    RunEvent,
    RunFailure,
    RunSubmission,
    RunSummary,
    StoredRun,
    ValidationStatus,
)
from terrafolio.store.runs import (
    finish_run,
    in_flight_runs,
    list_runs,
    load_result_json,
    load_run,
    load_run_by_ref,
    open_run,
    start_run,
)
from terrafolio.store.snapshots import (
    assumption_payload,
    load_assumption_snapshot,
    load_pipeline_snapshot,
    record_assumption_set,
    record_pipeline_snapshot,
    snapshot_hash_of,
)

__all__ = [
    "RUN_REF_SERIES",
    "SCHEMA_VERSION",
    "AssumptionSnapshot",
    "DuplicateGenerationError",
    "DuplicateRunError",
    "PipelineSnapshot",
    "RunAlreadyFinishedError",
    "RunEvent",
    "RunFailure",
    "RunIdentityChangedError",
    "RunNotFinishedError",
    "RunNotFoundError",
    "RunSubmission",
    "RunSummary",
    "SchemaUnsupportedError",
    "SnapshotConflictError",
    "StoreError",
    "StoredRun",
    "UnknownSnapshotError",
    "ValidationStatus",
    "append_events",
    "assumption_payload",
    "backup_into",
    "connect",
    "finish_run",
    "format_run_ref",
    "from_db_time",
    "in_flight_runs",
    "initialise",
    "latest_generation",
    "list_runs",
    "load_assumption_snapshot",
    "load_pipeline_snapshot",
    "load_result_json",
    "load_run",
    "load_run_by_ref",
    "new_ulid",
    "open_run",
    "open_store",
    "parse_run_ref",
    "read_events",
    "record_assumption_set",
    "record_pipeline_snapshot",
    "snapshot_hash_of",
    "start_run",
    "to_db_time",
    "writing",
]
