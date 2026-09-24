"""Where a search runs, and who hears about it finishing.

Three modes, and **all three run the same engine through the same entry point**.
``inline`` is not a stand-in implementation or a fake: it calls
:func:`terrafolio.runner.worker.execute` on the calling thread, which is what
makes an API test deterministic and single-threaded without waiting on a pool.
What it cannot do is pin BLAS, because numpy is already imported by the time a
server is answering requests — so a run that needs §12's bit-exact guarantee
needs ``process``, and ``blasThreads`` on the run record says which it got.

The pool knows nothing about the store or the wire. It takes a payload and two
callables, and the API layer supplies callables that finish the run. That is not
ceremony: the €m ``RunRecord`` needs the join in ``api/scalars.py``, and the
dependency direction runs ``api -> runner``, so the runner could not build one
even if it wanted to.
"""

from __future__ import annotations

import multiprocessing
import os
from collections.abc import Callable
from concurrent.futures import (
    CancelledError,
    Executor,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
)
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from terrafolio.runner.modes import RunnerMode
from terrafolio.runner.threads import pin_threads
from terrafolio.runner.worker import RunPayload, WorkerOutcome, execute, warm

__all__ = ["Runner", "build_runner", "worker_count"]


def worker_count(requested: int | None) -> int:
    """``max(1, cpu_count() - 1)`` unless told otherwise.

    One core is left for the server itself. Oversubscribing matters more here
    than it usually does: each worker is pinned to a single BLAS thread on
    purpose, so the pool size is the only parallelism there is.
    """
    if requested is not None:
        return max(1, requested)
    return max(1, (os.cpu_count() or 1) - 1)


@dataclass(frozen=True, slots=True, kw_only=True)
class Callbacks:
    """What the caller wants done when a run ends, either way.

    Never pickled — they stay in the parent, and only :class:`RunPayload`
    crosses the process boundary.
    """

    succeeded: Callable[[WorkerOutcome], None]
    failed: Callable[[BaseException], None]


class Runner:
    """Submits searches and reports their outcome. Not itself a state machine.

    The run's states live in the ``run`` table, where a trigger enforces the
    transitions; duplicating them here would give two answers to "what is this
    run doing" and one of them would be the stale one.
    """

    def __init__(self, mode: RunnerMode, *, max_workers: int | None = None) -> None:
        self.mode = mode
        self.workers = worker_count(max_workers)
        self._executor: Executor | None = _executor(mode, self.workers)

    def submit(self, payload: RunPayload, callbacks: Callbacks) -> None:
        """Start a search. Returns as soon as it is accepted, except inline.

        Every failure path funnels through ``callbacks.failed``, including a
        worker that dies without raising: a run left ``running`` for ever is
        worse than a run recorded as failed, because only one of the two can be
        explained to a committee.
        """
        if self._executor is None:
            self._run_inline(payload, callbacks)
            return
        future = self._executor.submit(execute, payload)
        future.add_done_callback(lambda done: _report(done, callbacks))

    def _run_inline(self, payload: RunPayload, callbacks: Callbacks) -> None:
        try:
            outcome = execute(payload)
        except Exception as error:  # a failure is a recorded outcome, not a crash
            callbacks.failed(error)
            return
        callbacks.succeeded(outcome)

    def warm(self, pipeline_dir: Path, pipeline_hash: str, assumption_set: str | None) -> None:
        """Load the pipeline into every worker before the first real run.

        Best effort and deliberately silent on failure: a warm-up that cannot
        read the directory tells us nothing a real request will not tell us
        better, and refusing to start the server over it would turn a slow first
        run into no server at all. Each future's result is dropped for the same
        reason, but it is *retrieved*, so a failed warm-up does not surface
        later as an unraisable-exception warning from the garbage collector.

        One task per worker is a best effort at covering them all, not a
        guarantee: nothing in ``ProcessPoolExecutor`` promises that N tasks land
        on N distinct workers. A worker this misses simply pays for its own
        first load.
        """
        if self._executor is None:
            return
        for _ in range(self.workers):
            future = self._executor.submit(warm, pipeline_dir, pipeline_hash, assumption_set)
            future.add_done_callback(lambda done: done.cancelled() or done.exception())

    def shutdown(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def __enter__(self) -> Runner:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.shutdown()


def _report(future: Future[WorkerOutcome], callbacks: Callbacks) -> None:
    """Hand one finished future to the caller, whichever way it ended.

    **Cancellation is checked before anything touches ``exception()``.** On a
    cancelled future that method *raises* ``CancelledError`` rather than
    returning it, so the obvious spelling raises inside a done-callback — where
    ``concurrent.futures`` swallows it — and neither callback fires. Since
    :meth:`Runner.shutdown` cancels outstanding futures, that left every queued
    run stuck in ``queued`` at every shutdown, with a stream that never ended
    and a status the store's forward-only transitions can never correct.
    """
    if future.cancelled():
        callbacks.failed(CancelledError("the run was cancelled before it started"))
        return
    error = future.exception()
    if error is not None:
        callbacks.failed(error)
        return
    callbacks.succeeded(future.result())


def _executor(mode: RunnerMode, workers: int) -> Executor | None:
    """``None`` for inline, which has nothing to submit to."""
    match mode:
        case RunnerMode.PROCESS:
            return ProcessPoolExecutor(
                max_workers=workers,
                # Spawn, never fork: a forked child inherits numpy already
                # imported, and so inherits whatever thread pool the parent
                # built before `pin_threads` could apply. It also inherits the
                # parent's sqlite handles, which is its own hazard.
                mp_context=multiprocessing.get_context("spawn"),
                initializer=pin_threads,
            )
        case RunnerMode.THREAD:
            return ThreadPoolExecutor(max_workers=workers, thread_name_prefix="terrafolio-run")
        case RunnerMode.INLINE:
            return None


def build_runner(mode: RunnerMode, *, max_workers: int | None = None) -> Runner:
    return Runner(mode, max_workers=max_workers)
