-- The run store. Four tables of record, one sequence, and a great many refusals.
--
-- Spec §11 makes a run immutable and addressable -- "a run ID is shareable and
-- reopens the exact result, including the mandate that produced it" -- and §12
-- keeps every run indefinitely. Both are enforced here rather than in the
-- caller: a trigger survives a bug in the API layer, a convention does not.
--
-- `result_json` is the truth. Every other column on `run` is an index over
-- those bytes, and the json_extract CHECKs below make a divergent write
-- impossible rather than merely untested.
--
-- What this does NOT defend against: an operator with the sqlite3 CLI. DROP
-- TABLE and PRAGMA writable_schema go around every trigger here. These guard
-- the application against its own bugs, which is the threat that actually
-- materialises.
--
-- Applied by store.db.initialise() with executescript(), outside any
-- transaction. Idempotent.

PRAGMA journal_mode = WAL;

-- --------------------------------------------------------------------------
-- The assumption set, snapshotted on first use
-- --------------------------------------------------------------------------
-- §12 wants assumption-set changes "versioned and attributable", which only
-- holds if a run's calibration cannot be edited out from under it.
--
-- Keyed on the digest of the payload stored here, NOT on assumption_set_id:
-- the loader's id deliberately excludes [meta], so fixing a typo in a label
-- yields the same id and different bytes. Keying on the id would silently keep
-- the first [meta] and misattribute every later run.
CREATE TABLE IF NOT EXISTS assumption_set (
    snapshot_hash       TEXT    NOT NULL PRIMARY KEY,
    assumption_set_id   TEXT    NOT NULL,
    assumption_set_hash TEXT    NOT NULL,
    name                TEXT    NOT NULL,
    label               TEXT    NOT NULL,
    version             INTEGER NOT NULL,
    created_by          TEXT    NOT NULL,
    supersedes          TEXT    NOT NULL,
    payload_json        TEXT    NOT NULL,
    recorded_at         TEXT    NOT NULL,
    CHECK (snapshot_hash LIKE 'sha256:%'),
    CHECK (assumption_set_hash LIKE 'sha256:%'),
    CHECK (length(assumption_set_id) = 16),
    CHECK (json_valid(payload_json))
) STRICT;

CREATE INDEX IF NOT EXISTS assumption_set_by_id ON assumption_set (assumption_set_id, version);

-- --------------------------------------------------------------------------
-- The pipeline snapshot the run saw
-- --------------------------------------------------------------------------
-- §13: "Stored runs keep their snapshot." A project deleted from the pipeline
-- after a run still has its content hash named here.
CREATE TABLE IF NOT EXISTS pipeline_snapshot (
    pipeline_hash     TEXT    NOT NULL PRIMARY KEY,
    -- A-13: one base year across the whole pipeline, enforced at load. The run
    -- result does not carry it and the 30-year series is indexed from it, so
    -- this is where a reopened run recovers its year labels.
    base_year         INTEGER NOT NULL,
    project_count     INTEGER NOT NULL CHECK (project_count >= 0),
    source_label      TEXT    NOT NULL,
    loaded_at         TEXT    NOT NULL,
    -- {id: "sha256:..."} -- the shape RunProvenance.file_hashes uses.
    file_hashes_json  TEXT    NOT NULL,
    -- A closed vocabulary owned by this table, not by issue 2A's report type.
    validation_status TEXT    NOT NULL
        CHECK (validation_status IN ('valid', 'warnings', 'invalid', 'unknown')),
    -- 2A's report, serialised by the caller and opaque here.
    validation_json   TEXT,
    recorded_at       TEXT    NOT NULL,
    CHECK (pipeline_hash LIKE 'sha256:%'),
    CHECK (json_valid(file_hashes_json)),
    CHECK (validation_json IS NULL OR json_valid(validation_json)),
    -- 'unknown' is the only status allowed to carry no report: a caller written
    -- before 2A landed must be visibly uncertain, never silently clean.
    CHECK (validation_json IS NOT NULL OR validation_status = 'unknown')
) STRICT;

-- --------------------------------------------------------------------------
-- The reference sequence
-- --------------------------------------------------------------------------
-- §7.6's "incrementing reference used in the export", minted BEFORE the run
-- row exists -- because POST /optimisations answers 202 with a runId and the
-- queued RunRecord already carries its runRef. AUTOINCREMENT can only report a
-- value after the insert, which would force a nullable result_json.
--
-- `series` is the scoping key. It is 'A' for now, which is the letter in
-- api.md §8's "A-4"; if a reference ever becomes per-fund, that is a new row
-- here rather than a migration.
CREATE TABLE IF NOT EXISTS run_sequence (
    series         TEXT    NOT NULL PRIMARY KEY,
    last_reference INTEGER NOT NULL CHECK (last_reference >= 0)
) STRICT;

INSERT OR IGNORE INTO run_sequence (series, last_reference) VALUES ('A', 0);

-- --------------------------------------------------------------------------
-- The run
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run (
    run_id                   TEXT    NOT NULL PRIMARY KEY,
    run_reference            INTEGER NOT NULL UNIQUE CHECK (run_reference > 0),
    run_ref                  TEXT    NOT NULL UNIQUE,
    status                   TEXT    NOT NULL
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    created_at               TEXT    NOT NULL,
    created_by               TEXT    NOT NULL,
    finished_at              TEXT,
    duration_ms              INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),

    -- What was asked for.
    mandate_json             TEXT    NOT NULL,
    effort                   TEXT    NOT NULL
        CHECK (effort IN ('fast', 'standard', 'exhaustive')),
    locked_ids_json          TEXT    NOT NULL,
    excluded_ids_json        TEXT    NOT NULL,
    -- Min DSCR is a derived screen, so the candidate vector the search indexed
    -- by position is not recoverable from the mandate alone. It is recorded.
    eligible_ids_json        TEXT    NOT NULL,

    -- What determined every number. §12's reproducibility, and a run missing
    -- its seed is not a valid run (epic §5) -- hence NOT NULL, not a default.
    seed                     INTEGER NOT NULL,
    pipeline_hash            TEXT    NOT NULL
        REFERENCES pipeline_snapshot (pipeline_hash) ON DELETE RESTRICT,
    assumption_snapshot_hash TEXT    NOT NULL
        REFERENCES assumption_set (snapshot_hash) ON DELETE RESTRICT,
    assumption_set_id        TEXT    NOT NULL,
    engine_version           TEXT    NOT NULL,
    numpy_version            TEXT    NOT NULL,
    blas_threads             INTEGER NOT NULL,
    deterministic_reduction  INTEGER NOT NULL CHECK (deterministic_reduction IN (0, 1)),
    python_version           TEXT    NOT NULL,
    platform                 TEXT    NOT NULL,
    -- Effort presets are configuration and can move, so what was actually run
    -- is recorded rather than re-derived from `effort` at read time.
    population_size          INTEGER NOT NULL CHECK (population_size > 0),
    generations_planned      INTEGER NOT NULL CHECK (generations_planned > 0),
    generations_used         INTEGER CHECK (generations_used IS NULL OR generations_used >= 0),

    -- What came out.
    selected_ids_json        TEXT    NOT NULL,
    -- The two series are named apart in the schema for the same reason A-6
    -- names them apart on the wire: one carries a terminal value and the other
    -- must not, and a single column called `cashflow` is how that gets lost.
    cashflow_30y_json        TEXT    NOT NULL,
    cashflow_hold_json       TEXT    NOT NULL,
    warnings_json            TEXT    NOT NULL,
    error_code               TEXT,
    error_message            TEXT,
    schema_version           INTEGER NOT NULL,
    result_json              TEXT    NOT NULL,

    CHECK (json_valid(result_json)),
    CHECK (json_valid(mandate_json)),
    CHECK (json_valid(locked_ids_json)),
    CHECK (json_valid(excluded_ids_json)),
    CHECK (json_valid(eligible_ids_json)),
    CHECK (json_valid(selected_ids_json)),
    CHECK (json_valid(warnings_json)),
    CHECK (json_valid(cashflow_30y_json)),
    CHECK (json_valid(cashflow_hold_json)),

    -- The indexed columns are made to agree with the served bytes here, where
    -- disagreement is impossible, rather than in a test that might not be run.
    CHECK (json_extract(result_json, '$.runId') = run_id),
    CHECK (json_extract(result_json, '$.runRef') = run_ref),
    CHECK (json_extract(result_json, '$.status') = status),
    CHECK (json_extract(result_json, '$.effort') = effort),
    CHECK (json_extract(result_json, '$.provenance.seed') = seed),
    CHECK (json_extract(result_json, '$.provenance.pipelineHash') = pipeline_hash),
    CHECK (json_extract(result_json, '$.provenance.assumptionSetId') = assumption_set_id),

    -- RunRecord's own succeeded-iff-aggregates rule, restated where a raw
    -- INSERT that never went through pydantic would still have to meet it.
    CHECK ((status = 'succeeded') = (json_extract(result_json, '$.aggregates') IS NOT NULL)),
    CHECK ((status IN ('queued', 'running')) = (finished_at IS NULL)),
    CHECK ((finished_at IS NULL) = (duration_ms IS NULL)),
    CHECK (status <> 'failed' OR error_code IS NOT NULL),
    CHECK (error_code IS NULL OR status IN ('failed', 'cancelled'))
) STRICT;

-- Ordering is by run_reference, which is monotone by construction. created_at
-- is a filter column and never the sort key: clock skew between worker hosts
-- must not be able to reorder history.
CREATE INDEX IF NOT EXISTS run_by_created_at ON run (created_at);
-- Partial: "what was still in flight" after a restart is a tiny set against a
-- table that grows forever, and a plain index on five status values is useless.
CREATE INDEX IF NOT EXISTS run_in_flight
    ON run (run_reference) WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS run_by_pipeline ON run (pipeline_hash, run_reference);
CREATE INDEX IF NOT EXISTS run_by_assumption ON run (assumption_set_id, run_reference);

-- --------------------------------------------------------------------------
-- The generation log
-- --------------------------------------------------------------------------
-- One row per generation, appended by the worker and tailed for SSE. The
-- composite key is what makes a late subscriber, a replay and two concurrent
-- subscribers all the same read (api.md §7). WITHOUT ROWID because
-- (run_id, gen) *is* the access path and the rows are small.
CREATE TABLE IF NOT EXISTS run_event (
    run_id       TEXT    NOT NULL REFERENCES run (run_id) ON DELETE RESTRICT,
    gen          INTEGER NOT NULL CHECK (gen > 0),
    -- Quantised to 6 dp by the engine before any comparison (epic §5), stored
    -- as given, so a replayed curve is the curve the run actually followed.
    best_fitness REAL    NOT NULL,
    mean_fitness REAL    NOT NULL,
    summary_json TEXT    NOT NULL,
    PRIMARY KEY (run_id, gen),
    CHECK (json_valid(summary_json)),
    -- allow_inf_nan=False, restated at the storage layer. NaN needs no check:
    -- SQLite stores it as NULL, which NOT NULL already refuses.
    CHECK (abs(best_fitness) < 9e999 AND abs(mean_fitness) < 9e999)
) STRICT, WITHOUT ROWID;

-- --------------------------------------------------------------------------
-- Append-only, enforced
-- --------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS run_is_never_deleted
BEFORE DELETE ON run
BEGIN SELECT RAISE(ABORT, 'store: a run is never deleted'); END;

CREATE TRIGGER IF NOT EXISTS run_is_immutable_once_finished
BEFORE UPDATE ON run WHEN old.status IN ('succeeded', 'failed', 'cancelled')
BEGIN SELECT RAISE(ABORT, 'store: a finished run is immutable'); END;

-- Only the outcome is filled in later. Without this, finishing a run could
-- quietly re-point it at a different mandate or a different pipeline.
CREATE TRIGGER IF NOT EXISTS run_inputs_are_fixed_at_submission
BEFORE UPDATE ON run
WHEN new.run_id IS NOT old.run_id
  OR new.run_reference IS NOT old.run_reference
  OR new.run_ref IS NOT old.run_ref
  OR new.created_at IS NOT old.created_at
  OR new.created_by IS NOT old.created_by
  OR new.seed IS NOT old.seed
  OR new.effort IS NOT old.effort
  OR new.population_size IS NOT old.population_size
  OR new.generations_planned IS NOT old.generations_planned
  OR new.mandate_json IS NOT old.mandate_json
  OR new.locked_ids_json IS NOT old.locked_ids_json
  OR new.excluded_ids_json IS NOT old.excluded_ids_json
  OR new.eligible_ids_json IS NOT old.eligible_ids_json
  OR new.pipeline_hash IS NOT old.pipeline_hash
  OR new.assumption_snapshot_hash IS NOT old.assumption_snapshot_hash
BEGIN SELECT RAISE(ABORT, 'store: a run''s inputs cannot be rewritten'); END;

CREATE TRIGGER IF NOT EXISTS run_status_only_advances
BEFORE UPDATE OF status ON run
WHEN NOT (
    (old.status = 'queued' AND new.status IN ('running', 'succeeded', 'failed', 'cancelled'))
    OR (old.status = 'running' AND new.status IN ('succeeded', 'failed', 'cancelled'))
)
BEGIN SELECT RAISE(ABORT, 'store: illegal run status transition'); END;

-- The log closes when the run does. Without this an event delivered after
-- `finish_run` commits would still insert -- the foreign key only asks whether
-- the run exists -- and a finished run's curve would grow after the result it
-- is supposed to match was served. Enforced here rather than in the caller
-- because the check and the insert have to be one statement to be race-free.
CREATE TRIGGER IF NOT EXISTS run_event_only_while_in_flight
BEFORE INSERT ON run_event
WHEN (SELECT status FROM run WHERE run_id = new.run_id) NOT IN ('queued', 'running')
BEGIN SELECT RAISE(ABORT, 'store: the generation log closes when the run finishes'); END;

CREATE TRIGGER IF NOT EXISTS run_event_is_append_only
BEFORE UPDATE ON run_event
BEGIN SELECT RAISE(ABORT, 'store: the generation log is append-only'); END;

CREATE TRIGGER IF NOT EXISTS run_event_is_never_deleted
BEFORE DELETE ON run_event
BEGIN SELECT RAISE(ABORT, 'store: the generation log is kept with its run'); END;

-- The whole point of snapshotting a calibration is that it cannot be edited
-- afterwards. A changed set is a new row, because the key is a content digest.
CREATE TRIGGER IF NOT EXISTS assumption_set_is_content_addressed
BEFORE UPDATE ON assumption_set
BEGIN SELECT RAISE(ABORT, 'store: an assumption-set snapshot is content-addressed'); END;

CREATE TRIGGER IF NOT EXISTS assumption_set_is_never_deleted
BEFORE DELETE ON assumption_set
BEGIN SELECT RAISE(ABORT, 'store: an assumption-set snapshot is never deleted'); END;

CREATE TRIGGER IF NOT EXISTS pipeline_snapshot_is_content_addressed
BEFORE UPDATE ON pipeline_snapshot
BEGIN SELECT RAISE(ABORT, 'store: a pipeline snapshot is content-addressed'); END;

CREATE TRIGGER IF NOT EXISTS pipeline_snapshot_is_never_deleted
BEFORE DELETE ON pipeline_snapshot
BEGIN SELECT RAISE(ABORT, 'store: a pipeline snapshot is never deleted'); END;
