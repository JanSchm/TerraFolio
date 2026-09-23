"""What the endpoints share: the pipeline, the store, the runner, the runs in flight.

One object rather than module-level state, because ``create_app`` is a factory
and a test builds several apps in one process. Everything a handler needs is
reachable from here, and nothing here knows what HTTP is.

**Connections are never shared.** ``sqlite3.Connection`` is single-threaded by
default, the server is threaded, the completion callbacks arrive on an executor
thread and the worker is a different process entirely — so every caller opens its
own, and WAL plus ``BEGIN IMMEDIATE`` does the rest. There is deliberately no
module-level singleton to reach for.
"""

from __future__ import annotations

import datetime as dt
import secrets
import sqlite3
import traceback
from contextlib import closing
from typing import Final

import terrafolio
from terrafolio.api.pipeline_source import PipelineSource
from terrafolio.api.records import (
    PendingRun,
    build_provenance,
    convergence_from_log,
    failed_record,
    succeeded_record,
)
from terrafolio.api.settings import Settings
from terrafolio.api.wire import SEED_BITS, OptimisationRequest
from terrafolio.config.assumptions import AssumptionSet
from terrafolio.config.loader import load_default
from terrafolio.optimiser.feasibility import FeasibilityPreview
from terrafolio.optimiser.ga import resolve_seed
from terrafolio.runner.modes import RunnerMode
from terrafolio.runner.pool import Callbacks, Runner, build_runner
from terrafolio.runner.threads import observed_threads
from terrafolio.runner.worker import RunPayload, WorkerOutcome
from terrafolio.store.db import open_store
from terrafolio.store.errors import RunAlreadyFinishedError, StoreError
from terrafolio.store.events import read_events
from terrafolio.store.records import RunFailure, RunSubmission
from terrafolio.store.runs import finish_run, open_run
from terrafolio.store.snapshots import record_assumption_set

__all__ = ["Service", "build_service"]

SUBMITTED_BY: Final = "terrafolio-api"
"""``run.created_by``. Authentication is not in this backlog (epic §12 Q7), so
every run is attributed to the service rather than to a person — which is an
honest placeholder for an audit field, not a stand-in for a user model."""

ENGINE_ERROR: Final = "ENGINE_ERROR"
TRACEBACK_LIMIT: Final = 4000
"""How much of a traceback ``run.error_message`` keeps.

There is no traceback column in 2B's schema, so a failure's diagnosis has to
live in the message. Truncated from the front — the innermost frames are the
ones that say what broke.
"""


class Service:
    """The application's state, built once per app and shut down with it."""

    def __init__(
        self,
        settings: Settings,
        assumptions: AssumptionSet,
        source: PipelineSource,
        runner: Runner,
    ) -> None:
        self.settings = settings
        self.assumptions = assumptions
        self.source = source
        self.runner = runner
        self._pending: dict[str, PendingRun] = {}
        with closing(self.connect()) as connection:
            self.assumption_snapshot = record_assumption_set(
                connection, assumptions, recorded_at=_now()
            )
            self.assumptions_recorded_at = _now()
            self.source.record(connection)

    def connect(self) -> sqlite3.Connection:
        """A fresh store connection, owned and closed by the caller."""
        return open_store(self.settings.database_path)

    def reload_pipeline(self) -> None:
        """Re-read the directory and record the snapshot it now hashes to.

        Prior runs are untouched: each references its own snapshot by hash, and
        the snapshot table is content-addressed and never deleted (§13).
        """
        self.source.reload()
        with closing(self.connect()) as connection:
            self.source.record(connection)

    def warm(self) -> None:
        if self.settings.warm_workers:
            self.runner.warm(
                self.source.directory, self.source.pipeline_hash, self.settings.assumption_set
            )

    def shutdown(self) -> None:
        self.runner.shutdown()

    # -- starting a run ------------------------------------------------------

    def predicted_blas_threads(self) -> int:
        """What the run will execute under, decided before it starts.

        ``open_run`` freezes a run's inputs at submission and ``blasThreads`` is
        one of them, so this is a claim the worker then has to honour — and
        :func:`~terrafolio.api.records.succeeded_record` refuses the record if it
        did not. A pool worker pins before numpy loads and so always reports one;
        anything in this process reports whatever this process actually has.
        """
        if self.runner.mode is RunnerMode.PROCESS:
            return 1
        return observed_threads()

    def submit(self, request: OptimisationRequest, preview: FeasibilityPreview) -> PendingRun:
        """Record a run and start it. The caller has already refused 409 and 422.

        The seed is resolved **here**, not in the worker, so that it is on the
        run row before the search begins: a run whose seed was only knowable
        after it finished could not be re-submitted from its own record, and epic
        §5 makes a run with no recorded seed not a run.
        """
        mandate, effort = request.mandate, request.effort
        loaded = self.source.result
        arrays = loaded.arrays
        eligible_ids = tuple(
            arrays.ids[index]
            for index, flag in enumerate(preview.screens.eligible.tolist())
            if flag
        )
        params = self.assumptions.ga.effort[effort]
        resolved = resolve_seed(request.seed if request.seed is not None else _draw_seed())
        provenance = build_provenance(
            seed=resolved,
            loaded=loaded,
            assumptions=self.assumptions,
            blas_threads=self.predicted_blas_threads(),
        )
        created_at = _now()
        with closing(self.connect()) as connection:
            self.source.record(connection)
            stored = open_run(
                connection,
                RunSubmission(
                    created_at=created_at,
                    created_by=SUBMITTED_BY,
                    mandate=mandate,
                    effort=effort,
                    provenance=provenance,
                    assumption_snapshot_hash=self.assumption_snapshot,
                    eligible_ids=eligible_ids,
                    population_size=params.population,
                    generations_planned=params.generations,
                    deterministic_reduction=provenance.blas_threads == 1,
                    locked_ids=request.locked_ids,
                    excluded_ids=request.excluded_ids,
                ),
            )

        pending = PendingRun(
            run_id=stored.record.run_id,
            run_ref=stored.record.run_ref,
            created_at=created_at,
            mandate=mandate,
            effort=effort,
            locked_ids=stored.record.locked_ids,
            excluded_ids=stored.record.excluded_ids,
            provenance=provenance,
            candidates=self.source.candidates,
            returns=self.source.returns(mandate.hold_years),
            total_generations=params.generations,
        )
        self._pending[pending.run_id] = pending
        self.runner.submit(
            RunPayload(
                run_id=pending.run_id,
                database_path=self.settings.database_path,
                pipeline_dir=self.source.directory,
                pipeline_hash=loaded.pipeline_hash,
                assumption_set=self.settings.assumption_set,
                mandate=mandate,
                effort=effort,
                seed=resolved,
                locked_ids=pending.locked_ids,
                excluded_ids=pending.excluded_ids,
            ),
            Callbacks(
                succeeded=lambda outcome: self._succeeded(pending, outcome),
                failed=lambda error: self._failed(pending, error),
            ),
        )
        return pending

    # -- finishing one -------------------------------------------------------

    def _succeeded(self, pending: PendingRun, outcome: WorkerOutcome) -> None:
        """Store the result. Any failure here still has to end the run."""
        try:
            record = succeeded_record(pending, outcome)
        except Exception as error:  # a record we cannot build is a failed run
            self._failed(pending, error)
            return
        try:
            with closing(self.connect()) as connection:
                finish_run(connection, record=record, finished_at=_now())
        except RunAlreadyFinishedError:  # pragma: no cover - idempotent retry
            pass
        finally:
            self._pending.pop(pending.run_id, None)

    def _failed(self, pending: PendingRun, error: BaseException) -> None:
        """Record the failure, with whatever curve reached the log before it.

        The run row persists: a failed run is still audit trail (§12). Its
        convergence is read back from ``run_event`` rather than remembered,
        because ``finish_run`` reconciles the two and the in-memory list did not
        survive whatever went wrong.
        """
        try:
            with closing(self.connect()) as connection:
                events = read_events(connection, run_id=pending.run_id)
                finish_run(
                    connection,
                    record=failed_record(
                        pending,
                        duration_ms=_elapsed_ms(pending.created_at),
                        convergence=convergence_from_log(events),
                    ),
                    finished_at=_now(),
                    failure=RunFailure(code=ENGINE_ERROR, message=_diagnosis(error)),
                )
        except (StoreError, ValueError):  # pragma: no cover - the run is already terminal
            pass
        finally:
            self._pending.pop(pending.run_id, None)

    def pending(self, run_id: str) -> PendingRun | None:
        return self._pending.get(run_id)


def _draw_seed() -> int:
    """A seed the store can hold.

    ``resolve_seed(None)`` draws from ``numpy.random.SeedSequence``, whose entropy
    is **128 bits** — more than a SQLite ``INTEGER`` can represent, so an unseeded
    run could be searched and then not stored. Drawing here instead keeps
    ``resolve_seed`` the one sanctioned way across while giving it a seed that
    fits, and the run is no less reproducible for it: the seed is explicit from
    the moment it is drawn, and it is on the 202 before the search starts.

    ``secrets`` rather than any seeded generator, for the same reason
    ``resolve_seed`` uses the operating system: a seed that came out of a seeded
    stream would make two "unseeded" runs identical.
    """
    return secrets.randbits(SEED_BITS)


def _diagnosis(error: BaseException) -> str:
    """The traceback, folded into the one text column a failure has."""
    text = "".join(traceback.format_exception(error)).strip()
    if len(text) <= TRACEBACK_LIMIT:
        return text
    return text[-TRACEBACK_LIMIT:]


def _elapsed_ms(since: dt.datetime) -> int:
    return max(round((_now() - since).total_seconds() * 1000), 0)


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def build_service(settings: Settings) -> Service:
    """Load everything the service needs, in dependency order."""
    assumptions = (
        load_default(settings.assumption_set) if settings.assumption_set else load_default()
    )

    source = PipelineSource(
        settings.pipeline_dir, assumptions, engine_version=terrafolio.__version__
    )
    runner = build_runner(settings.runner_mode, max_workers=settings.max_workers)
    return Service(settings, assumptions, source, runner)
