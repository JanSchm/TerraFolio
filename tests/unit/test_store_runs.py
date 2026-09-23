"""The run lifecycle: opened at submission, finished once, immutable after.

§11 is the requirement these tests exist for — "a run ID is shareable and
reopens the exact result, including the mandate that produced it. This is the
audit trail the investment committee needs." An audit trail that can be
rewritten is not one, so most of what follows is about what the store refuses.

The builders here are shared with the other ``test_store_*`` modules and with
``test_export_csv``, by bare module name: ``tests/`` is not a package and
pytest puts each test directory on ``sys.path``, which is the same route
``test_domain_results`` takes to ``test_domain_mandate``.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest

# Imported by bare module name -- see the module docstring.
from test_domain_mandate import VALID as VALID_MANDATE
from test_domain_results import AGGREGATES, PROVENANCE, holding

from terrafolio.config.loader import load_default
from terrafolio.domain.conventions import YEARS
from terrafolio.domain.enums import Effort, RunStatus, WarningCode
from terrafolio.domain.mandate import Mandate
from terrafolio.domain.results import (
    ConvergencePoint,
    FeasibilityWarning,
    Holding,
    PortfolioAggregates,
    RunProvenance,
    RunRecord,
)
from terrafolio.store import (
    DuplicateRunError,
    PipelineSnapshot,
    RunAlreadyFinishedError,
    RunFailure,
    RunIdentityChangedError,
    RunNotFinishedError,
    RunNotFoundError,
    RunSubmission,
    StoredRun,
    UnknownSnapshotError,
    ValidationStatus,
    finish_run,
    in_flight_runs,
    list_runs,
    load_result_json,
    load_run,
    load_run_by_ref,
    open_run,
    open_store,
    record_assumption_set,
    record_pipeline_snapshot,
    snapshot_hash_of,
    start_run,
)

HOLD_YEARS: Final = int(VALID_MANDATE["holdYears"])
BASE_YEAR: Final = 2027
CREATED_AT: Final = datetime(2026, 9, 21, 9, 22, 11, tzinfo=UTC)
RUN_ID: Final = "01JB2QTESTRUNTESTRUNTESTRU"
ELIGIBLE: Final = ("P001", "P002", "P003")


def provenance(**overrides: Any) -> RunProvenance:
    """Provenance naming the shipped calibration, so the snapshot resolves."""
    assumptions = load_default()
    # Overrides merge last, so a test can vary the two calibration fields too.
    payload = (
        dict(PROVENANCE)
        | {
            "assumptionSetId": assumptions.assumption_set_id,
            "assumptionSetHash": assumptions.content_hash,
        }
        | overrides
    )
    return RunProvenance.model_validate(payload)


def submission(**overrides: Any) -> RunSubmission:
    fields: dict[str, Any] = {
        "created_at": CREATED_AT,
        "created_by": "jan.schmitz",
        "mandate": Mandate.model_validate(VALID_MANDATE),
        "effort": Effort.STANDARD,
        "provenance": provenance(),
        "assumption_snapshot_hash": snapshot_hash_of(load_default()),
        "eligible_ids": ELIGIBLE,
        "population_size": 90,
        "generations_planned": 60,
        "deterministic_reduction": True,
        "run_id": RUN_ID,
    }
    return RunSubmission(**(fields | overrides))


def pipeline_snapshot(**overrides: Any) -> PipelineSnapshot:
    fields: dict[str, Any] = {
        "pipeline_hash": PROVENANCE["pipelineHash"],
        "base_year": BASE_YEAR,
        "project_count": len(ELIGIBLE),
        "source_label": "pipeline/",
        "loaded_at": CREATED_AT,
        "file_hashes": dict(PROVENANCE["fileHashes"]),
        "validation_status": ValidationStatus.UNKNOWN,
        "validation_json": None,
    }
    return PipelineSnapshot(**(fields | overrides))


def opened_store(path: Path) -> sqlite3.Connection:
    """A database with both snapshots already recorded. The caller closes it."""
    connection = open_store(path)
    record_assumption_set(connection, load_default(), recorded_at=CREATED_AT)
    record_pipeline_snapshot(connection, pipeline_snapshot(), recorded_at=CREATED_AT)
    return connection


def cashflow_30y() -> tuple[float, ...]:
    """A series with a construction outflow, so the sign change is real."""
    return tuple(-28.93 if year == 0 else round(12.3456 + year, 4) for year in range(YEARS))


def completed(record: RunRecord, **overrides: Any) -> RunRecord:
    """The queued record, finished — the shape a worker hands back."""
    holdings = (
        Holding.model_validate(holding("P001")),
        Holding.model_validate(holding("P002", selected=False)),
        # An undefined IRR, which §13 requires to stay undefined all the way out.
        Holding.model_validate(holding("P003", equityIrr=None, minDscr=None, moic=None)),
    )
    fields: dict[str, Any] = {
        "status": RunStatus.SUCCEEDED,
        "duration_ms": 2483,
        "aggregates": PortfolioAggregates.model_validate(AGGREGATES),
        "holdings": holdings,
        "selected_ids": ("P001", "P003"),
        "cashflow_30y_m": cashflow_30y(),
        "cashflow_hold_m": tuple(float(year) for year in range(HOLD_YEARS)),
        "convergence": (ConvergencePoint(generation=1, best_fitness=-12.4, mean_fitness=-31.2),),
    }
    return record.model_copy(update=fields | overrides)


def finished_run(connection: sqlite3.Connection, **overrides: Any) -> StoredRun:
    """Open a run and finish it, the ordinary path."""
    stored = open_run(connection, submission())
    return finish_run(
        connection, record=completed(stored.record, **overrides), finished_at=CREATED_AT
    )


# --------------------------------------------------------------------------
# Opening a run
# --------------------------------------------------------------------------


def test_a_queued_run_is_readable_the_moment_it_is_opened(tmp_path: Path) -> None:
    """``POST /optimisations`` answers 202 before any result exists.

    A run in flight still answers ``GET /optimisations/{id}`` with a valid body
    (``api.md`` §8), so the record has to be real from the start rather than a
    placeholder that becomes real later.
    """
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        assert stored.record.status is RunStatus.QUEUED
        assert stored.record.aggregates is None
        assert stored.record.run_ref == "A-1"
        assert stored.record.provenance.seed == PROVENANCE["seed"]
        assert load_run(connection, run_id=RUN_ID).record == stored.record


def test_the_reference_increments_and_the_label_follows_it(tmp_path: Path) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        refs = [open_run(connection, submission(run_id=None)).record.run_ref for _ in range(3)]
        assert refs == ["A-1", "A-2", "A-3"]


def test_a_run_cannot_cite_a_pipeline_snapshot_that_was_never_recorded(
    tmp_path: Path,
) -> None:
    """The snapshot is the run's input. A run that names one nobody kept is not
    reproducible, which is the whole of §12."""
    with closing(open_store(tmp_path / "runs.db")) as connection:
        record_assumption_set(connection, load_default(), recorded_at=CREATED_AT)
        with pytest.raises(UnknownSnapshotError):
            open_run(connection, submission())


def test_a_run_cannot_cite_an_assumption_set_that_was_never_snapshotted(
    tmp_path: Path,
) -> None:
    with closing(open_store(tmp_path / "runs.db")) as connection:
        record_pipeline_snapshot(connection, pipeline_snapshot(), recorded_at=CREATED_AT)
        with pytest.raises(UnknownSnapshotError):
            open_run(connection, submission())


# --------------------------------------------------------------------------
# The round trip
# --------------------------------------------------------------------------


def test_a_stored_run_reloads_byte_for_byte(tmp_path: Path) -> None:
    """The headline requirement, and the reason ``result_json`` is the truth.

    Re-serialising on read would make §11's "the same id always reopens the
    same result" depend on no dependency ever changing how a float or a
    timestamp renders.
    """
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = finished_run(connection)
        payload = stored.record.model_dump_json()
        reloaded = load_run(connection, run_id=RUN_ID)
        assert reloaded.result_json == payload
        assert load_result_json(connection, run_id=RUN_ID) == payload
        assert reloaded.record == stored.record


def test_a_reloaded_run_keeps_its_tuples_frozen_mappings_and_aware_times(
    tmp_path: Path,
) -> None:
    """A stored run is compared against other stored runs, so shape matters as
    much as value."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        finished_run(connection)
        record = load_run(connection, run_id=RUN_ID).record
        assert isinstance(record.holdings, tuple)
        assert isinstance(record.selected_ids, tuple)
        assert record.created_at.tzinfo is not None
        assert record.created_at == CREATED_AT
        with pytest.raises(TypeError):
            record.provenance.file_hashes["P001"] = "sha256:tampered"  # type: ignore[index]


def test_an_undefined_irr_survives_the_store_as_null(tmp_path: Path) -> None:
    """§13: never coerce to zero. The chain is NaN -> null -> em dash, and a
    store that defaulted a nullable column is one of the places it can break."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        finished_run(connection)
        record = load_run(connection, run_id=RUN_ID).record
        undefined = next(item for item in record.holdings if item.id == "P003")
        assert undefined.equity_irr is None
        assert undefined.min_dscr is None
        assert '"equityIrr":null' in load_result_json(connection, run_id=RUN_ID)


def test_the_denormalised_columns_agree_with_the_stored_json(tmp_path: Path) -> None:
    """Every indexed column is an assertion about the served bytes.

    ``schema.sql`` states most of these as CHECK constraints; this proves the
    values actually written satisfy them rather than that the constraint exists.
    """
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = finished_run(connection)
        row = connection.execute("SELECT * FROM run WHERE run_id = ?", (RUN_ID,)).fetchone()
        served = json.loads(row["result_json"])
        assert row["run_id"] == served["runId"]
        assert row["run_ref"] == served["runRef"]
        assert row["status"] == served["status"]
        assert row["effort"] == served["effort"]
        assert row["seed"] == served["provenance"]["seed"]
        assert row["pipeline_hash"] == served["provenance"]["pipelineHash"]
        assert json.loads(row["selected_ids_json"]) == list(served["selectedIds"])
        assert json.loads(row["cashflow_30y_json"]) == served["cashflow30Y_m"]
        assert json.loads(row["cashflow_hold_json"]) == served["cashflowHold_m"]
        assert row["generations_used"] == len(stored.record.convergence)


def test_the_two_cash_flow_series_are_stored_apart(tmp_path: Path) -> None:
    """A-6 calls conflating them the single most likely silent bug in the
    feature. They are different lengths and only one carries a terminal value,
    so the store gives them different columns as well as different names."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        finished_run(connection)
        row = connection.execute("SELECT * FROM run WHERE run_id = ?", (RUN_ID,)).fetchone()
        thirty = json.loads(row["cashflow_30y_json"])
        hold = json.loads(row["cashflow_hold_json"])
        assert len(thirty) == YEARS
        assert len(hold) == HOLD_YEARS
        assert thirty[:HOLD_YEARS] != hold


def test_a_run_is_addressable_by_its_export_label(tmp_path: Path) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        finished_run(connection)
        assert load_run_by_ref(connection, run_ref="A-1").record.run_id == RUN_ID


def test_an_unknown_run_is_not_found(tmp_path: Path) -> None:
    with (
        closing(opened_store(tmp_path / "runs.db")) as connection,
        pytest.raises(RunNotFoundError),
    ):
        load_run(connection, run_id="01NOSUCHRUNNOSUCHRUNNOSUCH")


# --------------------------------------------------------------------------
# What the store refuses
# --------------------------------------------------------------------------


def test_a_run_advances_from_queued_through_running_to_succeeded(tmp_path: Path) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        assert start_run(connection, run_id=RUN_ID).record.status is RunStatus.RUNNING
        assert start_run(connection, run_id=RUN_ID).record.status is RunStatus.RUNNING
        done = finish_run(connection, record=completed(stored.record), finished_at=CREATED_AT)
        assert done.record.status is RunStatus.SUCCEEDED
        assert done.finished_at == CREATED_AT


def test_a_finished_run_cannot_be_rewritten(tmp_path: Path) -> None:
    """The trigger, not the caller. A guarantee that depends on every future
    caller remembering it is not a guarantee.

    The update here touches a column no status rule covers, so the refusal can
    only have come from the immutability trigger.
    """
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        finished_run(connection)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE run SET duration_ms = 1 WHERE run_id = ?", (RUN_ID,))
        assert load_run(connection, run_id=RUN_ID).record.duration_ms == 2483


def test_a_finished_run_cannot_be_moved_back_to_an_earlier_status(tmp_path: Path) -> None:
    """Refused whichever guard gets there first: SQLite does not define the
    order two BEFORE UPDATE triggers fire in, and both of these say no."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        finished_run(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE run SET status = 'running' WHERE run_id = ?", (RUN_ID,))
        assert load_run(connection, run_id=RUN_ID).record.status is RunStatus.SUCCEEDED


def test_a_run_is_never_deleted(tmp_path: Path) -> None:
    """§12 keeps every run indefinitely."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        finished_run(connection)
        with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
            connection.execute("DELETE FROM run WHERE run_id = ?", (RUN_ID,))


def test_an_insert_or_replace_cannot_go_around_the_delete_trigger(tmp_path: Path) -> None:
    """``INSERT OR REPLACE`` deletes the conflicting row, and without
    ``recursive_triggers`` that delete does not fire the trigger — so the
    immutability guarantee would evaporate on one statement."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        finished_run(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT OR REPLACE INTO run (run_id, run_reference, run_ref, status, "
                "created_at, created_by, mandate_json, effort, locked_ids_json, "
                "excluded_ids_json, eligible_ids_json, seed, pipeline_hash, "
                "assumption_snapshot_hash, assumption_set_id, engine_version, "
                "numpy_version, blas_threads, deterministic_reduction, python_version, "
                "platform, population_size, generations_planned, selected_ids_json, "
                "cashflow_30y_json, cashflow_hold_json, warnings_json, schema_version, "
                "result_json) SELECT run_id, run_reference, run_ref, 'queued', created_at, "
                "created_by, mandate_json, effort, locked_ids_json, excluded_ids_json, "
                "eligible_ids_json, seed, pipeline_hash, assumption_snapshot_hash, "
                "assumption_set_id, engine_version, numpy_version, blas_threads, "
                "deterministic_reduction, python_version, platform, population_size, "
                "generations_planned, selected_ids_json, cashflow_30y_json, "
                "cashflow_hold_json, warnings_json, schema_version, result_json "
                "FROM run WHERE run_id = ?",
                (RUN_ID,),
            )


def test_a_second_finish_with_different_bytes_raises(tmp_path: Path) -> None:
    """Two workers believe they own the run, or one is replaying stale state.
    Neither is a condition to swallow."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = finished_run(connection)
        divergent = stored.record.model_copy(update={"duration_ms": 9999})
        with pytest.raises(RunAlreadyFinishedError):
            finish_run(connection, record=divergent, finished_at=CREATED_AT)
        assert load_run(connection, run_id=RUN_ID).record.duration_ms == 2483


def test_a_second_finish_with_identical_bytes_is_a_no_op(tmp_path: Path) -> None:
    """A redelivered acknowledgement after a crash between commit and reply.
    The worker would be writing exactly what is already there."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = finished_run(connection)
        again = finish_run(connection, record=stored.record, finished_at=CREATED_AT)
        assert again.result_json == stored.result_json


def test_a_retry_that_changes_the_failure_reason_is_not_a_redelivery(
    tmp_path: Path,
) -> None:
    """The reason and the warnings are stored beside `result_json` and are not
    in it, so one record and two different reasons are two different outcomes —
    which is the case a bytes-only comparison would wave through."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        record = stored.record.model_copy(update={"status": RunStatus.FAILED, "duration_ms": 12})
        first = RunFailure(code="ENGINE_ERROR", message="the search did not converge")
        finish_run(connection, record=record, finished_at=CREATED_AT, failure=first)

        # Same record, same reason: a redelivery.
        again = finish_run(connection, record=record, finished_at=CREATED_AT, failure=first)
        assert again.error_message == first.message

        with pytest.raises(RunAlreadyFinishedError):
            finish_run(
                connection,
                record=record,
                finished_at=CREATED_AT,
                failure=RunFailure(code="STORE_ERROR", message="something else entirely"),
            )
        assert load_run(connection, run_id=RUN_ID).error_code == "ENGINE_ERROR"


def test_a_retry_that_changes_the_warnings_is_not_a_redelivery(tmp_path: Path) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        record = completed(stored.record)
        raised = (
            FeasibilityWarning.model_validate(
                {
                    "code": "CAPACITY_BELOW_TARGET",
                    "severity": "alert",
                    "message": "Eligible pipeline is 900 MW - below the 1,500 MW target.",
                }
            ),
        )
        finish_run(connection, record=record, finished_at=CREATED_AT, warnings_raised=raised)
        with pytest.raises(RunAlreadyFinishedError):
            finish_run(connection, record=record, finished_at=CREATED_AT)
        assert load_run(connection, run_id=RUN_ID).warnings_raised == raised


def test_a_result_for_a_different_mandate_is_refused(tmp_path: Path) -> None:
    """The inputs are fixed at submission. A result that quietly re-points the
    run at another mandate would make the stored pair internally consistent and
    historically false."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        other = Mandate.model_validate(VALID_MANDATE | {"holdYears": HOLD_YEARS + 1})
        record = completed(stored.record).model_copy(update={"mandate": other})
        with pytest.raises(RunIdentityChangedError, match="mandate"):
            finish_run(connection, record=record, finished_at=CREATED_AT)


def test_a_result_that_changes_any_part_of_the_provenance_is_refused(
    tmp_path: Path,
) -> None:
    """Reproducibility rests on ten fields, not on the three the columns index.

    A worker returning a different `fileHashes`, `numpyVersion` or
    `blasThreads` would otherwise be stored happily, and the run would claim to
    be reproducible from inputs it never saw.
    """
    changed = {
        "fileHashes": {"P001": "sha256:different"},
        "numpyVersion": "2.4.5",
        "blasThreads": 1,
        "pythonVersion": "3.13.0",
        "platform": "linux-x86_64",
        "engineVersion": "1.0.1",
        "assumptionSetHash": "sha256:different",
    }
    for field, value in changed.items():
        with closing(opened_store(tmp_path / f"{field}.db")) as connection:
            stored = open_run(connection, submission())
            record = completed(stored.record).model_copy(
                update={"provenance": provenance(**{field: value})}
            )
            with pytest.raises(RunIdentityChangedError, match="provenance"):
                finish_run(connection, record=record, finished_at=CREATED_AT)


def test_a_run_is_attached_to_the_snapshot_it_names_not_the_newest_match(
    tmp_path: Path,
) -> None:
    """Two calibrations that differ only in `[meta]` share an id *and* a hash by
    design, so the snapshot cannot be inferred from provenance — it is passed in.

    Here the older variant is recorded first, a newer one second, and the run
    names the older. Re-recording does not move `recorded_at`, so any
    most-recent-match rule would attach the run to the wrong payload.
    """
    original = load_default()
    relabelled = dataclasses.replace(
        original, meta=dataclasses.replace(original.meta, label="a later label")
    )
    assert relabelled.assumption_set_id == original.assumption_set_id
    assert relabelled.content_hash == original.content_hash

    with closing(opened_store(tmp_path / "runs.db")) as connection:
        wanted = record_assumption_set(connection, original, recorded_at=CREATED_AT)
        newer = record_assumption_set(connection, relabelled, recorded_at=CREATED_AT)
        assert wanted != newer

        stored = open_run(connection, submission(assumption_snapshot_hash=wanted, run_id=None))
        assert stored.assumption_snapshot_hash == wanted


def test_a_run_cannot_name_a_snapshot_that_is_not_its_calibration(
    tmp_path: Path,
) -> None:
    """The foreign key proves the snapshot exists; this proves it is the right one.

    Here the snapshot is real and the run names it, but the provenance it
    serves claims a different calibration. Every column would still agree with
    itself, and the run would cite an audit payload it never used.
    """
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        real = snapshot_hash_of(load_default())
        with pytest.raises(UnknownSnapshotError, match="but the run claims"):
            open_run(
                connection,
                submission(
                    provenance=provenance(assumptionSetId="0000000000000000"),
                    assumption_snapshot_hash=real,
                ),
            )


def test_a_result_whose_holdings_are_not_the_eligible_set_is_refused(
    tmp_path: Path,
) -> None:
    """The candidate vector is indexed by position by the search, so a result
    carrying a different set is not a result for this run."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission(eligible_ids=("P001", "P002")))
        with pytest.raises(RunIdentityChangedError, match="eligibleIds"):
            finish_run(connection, record=completed(stored.record), finished_at=CREATED_AT)


def test_a_finished_run_must_report_its_duration(tmp_path: Path) -> None:
    """§12 audits how long a run took, and the schema pairs `finished_at` with
    `duration_ms`, so a result reporting neither is not an outcome."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        record = completed(stored.record, duration_ms=None)
        with pytest.raises(RunNotFinishedError, match="no duration"):
            finish_run(connection, record=record, finished_at=CREATED_AT)


def test_a_record_that_is_still_running_is_not_an_outcome(tmp_path: Path) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        record = stored.record.model_copy(update={"status": RunStatus.RUNNING})
        with pytest.raises(RunNotFinishedError, match="is running"):
            finish_run(connection, record=record, finished_at=CREATED_AT)


def test_opening_a_run_twice_under_one_id_is_refused(tmp_path: Path) -> None:
    """The id addresses the run (§11). Two runs under one id would make the
    second silently unreachable."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        open_run(connection, submission())
        with pytest.raises(DuplicateRunError):
            open_run(connection, submission())


def test_finishing_a_run_that_was_never_opened_is_not_found(tmp_path: Path) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        orphan = completed(stored.record).model_copy(
            update={"run_id": "01NOSUCHRUNNOSUCHRUNNOSUCH"}
        )
        with pytest.raises(RunNotFoundError):
            finish_run(connection, record=orphan, finished_at=CREATED_AT)


def test_a_failed_run_keeps_its_reason_and_has_no_aggregates(tmp_path: Path) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        record = stored.record.model_copy(update={"status": RunStatus.FAILED, "duration_ms": 12})
        done = finish_run(
            connection,
            record=record,
            finished_at=CREATED_AT,
            failure=RunFailure(code="ENGINE_ERROR", message="the search did not converge"),
        )
        assert done.record.aggregates is None
        assert done.error_code == "ENGINE_ERROR"


def test_a_start_after_finishing_is_refused(tmp_path: Path) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        finished_run(connection)
        with pytest.raises(RunAlreadyFinishedError):
            start_run(connection, run_id=RUN_ID)


# --------------------------------------------------------------------------
# What the record carries that the wire does not
# --------------------------------------------------------------------------


def test_a_stored_run_names_the_audit_fields_the_wire_contract_omits(
    tmp_path: Path,
) -> None:
    """§12 wants who ran it, against which candidates, at what effort the preset
    actually resolved to — none of which is part of the result a client reads."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = finished_run(connection)
        assert stored.created_by == "jan.schmitz"
        assert stored.eligible_ids == ELIGIBLE
        assert (stored.population_size, stored.generations_planned) == (90, 60)
        assert stored.deterministic_reduction is True
        assert stored.base_year == BASE_YEAR
        assert stored.run_reference == 1


def test_a_stored_run_names_the_exact_file_hashes_it_saw(tmp_path: Path) -> None:
    """The files are the input. A run must say which versions of them it read,
    and §13 expects that to survive the pipeline moving underneath it."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = finished_run(connection)
        assert dict(stored.record.provenance.file_hashes) == dict(PROVENANCE["fileHashes"])
        row = connection.execute(
            "SELECT file_hashes_json FROM pipeline_snapshot WHERE pipeline_hash = ?",
            (stored.record.provenance.pipeline_hash,),
        ).fetchone()
        assert json.loads(row["file_hashes_json"]) == dict(PROVENANCE["fileHashes"])


def test_warnings_are_stored_by_name_never_by_ordinal(tmp_path: Path) -> None:
    """C-9: runs are addressable indefinitely, so a code whose number shifted in
    a later release would rewrite the meaning of a stored run."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = open_run(connection, submission())
        raised = (
            FeasibilityWarning.model_validate(
                {
                    "code": "CAPACITY_BELOW_TARGET",
                    "severity": "alert",
                    "message": "Eligible pipeline is 900 MW - below the 1,500 MW target.",
                }
            ),
        )
        done = finish_run(
            connection,
            record=completed(stored.record),
            finished_at=CREATED_AT,
            warnings_raised=raised,
        )
        assert done.warnings_raised == raised
        row = connection.execute(
            "SELECT warnings_json FROM run WHERE run_id = ?", (RUN_ID,)
        ).fetchone()
        assert "CAPACITY_BELOW_TARGET" in row["warnings_json"]
        assert str(int(WarningCode.CAPACITY_BELOW_TARGET)) not in row["warnings_json"]


def test_runs_list_newest_first_and_in_flight_finds_the_unfinished(
    tmp_path: Path,
) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        first = open_run(connection, submission(run_id=None))
        finish_run(connection, record=completed(first.record), finished_at=CREATED_AT)
        second = open_run(connection, submission(run_id=None))
        assert [item.run_ref for item in list_runs(connection, limit=10)] == ["A-2", "A-1"]
        assert [item.run_ref for item in in_flight_runs(connection)] == ["A-2"]
        assert second.record.run_ref == "A-2"
