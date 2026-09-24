"""What actually runs a search, wherever it runs.

**The first import is deliberate and load-bearing.** ``threads`` pins every
numeric thread pool to one before anything else in this module is imported, and
under ``spawn`` a child process imports this module before it can call anything
in it. Every threading library reads its environment variable once, when its
shared object loads — which happens on the first ``import numpy`` in a process —
so the pin has to be in place before that, and moving this import down the block,
or letting anything above it reach numpy, silently un-pins BLAS. That would make
reduction order vary between runs and break §12's bit-exact guarantee while every
test still passed.

Every other import in this file therefore sits **below** that call, with the
lint rule that would reorder them suppressed line by line: the order is the
mechanism, not a style preference.

**This module does not finish the run.** It appends each generation to
``run_event`` as the search produces it — that is what makes the stream live —
and returns the euro-denominated result. Turning that into the €m ``RunRecord``
needs the project names, coordinates and provenance blocks that ``ProjectArrays``
does not carry, and the join that fills them in lives in ``api/scalars.py``,
which ``runner`` may not import (the dependency direction runs
``api -> runner``). So the caller finishes the run. That split is also the right
one on its own terms: the worker owns the compute, the server owns the store.
"""

from __future__ import annotations

from terrafolio.runner.threads import pin_threads

pin_threads()

# Everything below loads *after* the pin above, which is why these sit here
# rather than at the top of the file. `E402` is the rule that would otherwise
# reorder them, and reordering them is precisely the bug.
import dataclasses  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Final  # noqa: E402

import numpy as np  # noqa: E402

from terrafolio.config.assumptions import AssumptionSet  # noqa: E402
from terrafolio.config.hashing import canonical_json  # noqa: E402
from terrafolio.config.loader import DEFAULT_ASSUMPTION_SET, load_default  # noqa: E402
from terrafolio.domain.conventions import EUR_PER_EUR_MILLION  # noqa: E402
from terrafolio.domain.enums import Effort  # noqa: E402
from terrafolio.domain.mandate import Mandate  # noqa: E402
from terrafolio.domain.reduce import mandate_to_scalars  # noqa: E402
from terrafolio.economics.returns import contracted_revenue_share, project_returns  # noqa: E402
from terrafolio.optimiser.feasibility import preview_feasibility  # noqa: E402
from terrafolio.optimiser.features import build_features  # noqa: E402
from terrafolio.optimiser.ga import GenerationEvent, SearchControls, evolve  # noqa: E402
from terrafolio.optimiser.result import RunResult, SelectionOutcome, build_result  # noqa: E402
from terrafolio.pipeline.loader import LoadResult, load_pipeline  # noqa: E402
from terrafolio.runner.threads import observed_threads, threads_are_pinned  # noqa: E402
from terrafolio.store.db import open_store  # noqa: E402
from terrafolio.store.events import append_events  # noqa: E402
from terrafolio.store.records import RunEvent  # noqa: E402
from terrafolio.store.runs import start_run  # noqa: E402

__all__ = ["RunPayload", "WorkerOutcome", "execute", "warm"]

MILLISECONDS_PER_SECOND: Final = 1000


class PipelineMovedError(RuntimeError):
    """The directory no longer hashes to what the run was accepted against.

    Raised by the worker rather than only checked by the server, because the
    worker reads the files itself: a run whose provenance claims one snapshot
    and whose numbers came from another is not auditable, and a hash check here
    is the only thing that can prove it did not happen.
    """


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RunPayload:
    """Everything a search needs, and nothing that cannot be pickled.

    Paths and identifiers rather than loaded objects: the arrays for a 300-file
    pipeline are megabytes, and sending them down a pipe on every run would cost
    more than the search. The worker loads once and caches by hash instead.
    """

    run_id: str
    database_path: Path
    pipeline_dir: Path
    pipeline_hash: str
    assumption_set: str | None
    mandate: Mandate
    effort: Effort
    seed: int
    """Already resolved. The server draws it so it can record it before the run
    starts — a run with no recorded seed is not a valid run (epic §5)."""
    locked_ids: tuple[str, ...]
    excluded_ids: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class WorkerOutcome:
    """What the search produced, in **euros**, for the caller to record.

    The masks are over the whole pipeline in canonical order, so the caller can
    rebuild the wire holdings against its own copy of the same snapshot without
    the worker having to ship the join.
    """

    result: RunResult
    convergence: tuple[GenerationEvent, ...]
    eligible: tuple[bool, ...]
    selected: tuple[bool, ...]
    locked: tuple[bool, ...]
    population_size: int
    generations_used: int
    duration_ms: int
    blas_threads: int
    threads_pinned: bool


_PIPELINES: dict[tuple[str, str], LoadResult] = {}
"""The **one** loaded pipeline this process is holding, keyed by directory and hash.

A pool worker handles many runs. Re-reading, validating and tying out 300 files
takes about a quarter of a second and retains roughly 15 MB, which would be pure
repetition across runs against data that has not moved.

Bounded to a single entry on purpose. Users add and remove files while the
server runs (epic §2), so the hash moves as a matter of course, and a cache that
kept every hash it had ever seen would retain 15 MB per reload per worker for
the life of the process — none of it ever reachable again, because only the
current hash is ever asked for.
"""

_ASSUMPTIONS: dict[str, AssumptionSet] = {}


def _assumptions_for(name: str | None) -> AssumptionSet:
    key = name or DEFAULT_ASSUMPTION_SET
    cached = _ASSUMPTIONS.get(key)
    if cached is None:
        cached = load_default(key)
        _ASSUMPTIONS[key] = cached
    return cached


def _pipeline_for(directory: Path, expected_hash: str, assumptions: AssumptionSet) -> LoadResult:
    """The pipeline this run was accepted against, loaded once per worker.

    The hash is verified rather than trusted. A worker that loaded a directory
    someone edited between acceptance and execution would produce a result whose
    provenance is a lie, and 409 at the front door cannot see that race.
    """
    key = (str(directory), expected_hash)
    cached = _PIPELINES.get(key)
    if cached is not None:
        return cached
    loaded = load_pipeline(directory, assumptions)
    if loaded.pipeline_hash != expected_hash:
        raise PipelineMovedError(
            f"{directory} now hashes to {loaded.pipeline_hash}, not the "
            f"{expected_hash} this run was accepted against"
        )
    _PIPELINES.clear()
    _PIPELINES[key] = loaded
    return loaded


def warm(directory: Path, expected_hash: str, assumption_set: str | None) -> str:
    """Load the pipeline into this process before a real run needs it.

    Called once per worker at startup so the first search does not pay for the
    load. Returns the hash it loaded, so a caller can tell the warm-up worked.
    """
    assumptions = _assumptions_for(assumption_set)
    return _pipeline_for(directory, expected_hash, assumptions).pipeline_hash


def execute(payload: RunPayload) -> WorkerOutcome:
    """Run one search, streaming each generation into ``run_event`` as it lands.

    Opens its **own** store connection: ``sqlite3.Connection`` is single-threaded
    by default and this may be a different process entirely, so there is no
    shared handle to inherit.
    """
    started = time.perf_counter()
    assumptions = _assumptions_for(payload.assumption_set)
    loaded = _pipeline_for(payload.pipeline_dir, payload.pipeline_hash, assumptions)
    arrays = loaded.arrays
    mandate = mandate_to_scalars(payload.mandate)

    # The same screening the server did to answer 202 rather than 422, recomputed
    # here because the worker must not depend on a mask travelling intact through
    # a pickle — and because it is a pure function of data whose hash has just
    # been verified, so it cannot disagree.
    preview = preview_feasibility(
        arrays,
        mandate,
        assumptions,
        locked_ids=payload.locked_ids,
        excluded_ids=payload.excluded_ids,
    )
    eligible = preview.screens.eligible
    rows = np.flatnonzero(eligible)

    returns = project_returns(arrays, assumptions, mandate.hold_years)
    features = build_features(
        arrays,
        equity_irr=np.nan_to_num(returns.equity_irr),
        irr_defined=returns.defined,
        merchant_share=1.0 - arrays.revenue.ppa_share,
    ).take(rows)

    # One definition of "locked", and the same one the screens used: an exclusion
    # is the more specific instruction, so a project that is both stays excluded.
    held = set(payload.locked_ids) - set(payload.excluded_ids)
    locked_all = np.array([project_id in held for project_id in arrays.ids], dtype=np.bool_)

    connection = open_store(payload.database_path)
    try:
        start_run(connection, run_id=payload.run_id)
        search = evolve(
            features,
            mandate,
            assumptions,
            SearchControls(effort=payload.effort, locked=locked_all[rows], seed=payload.seed),
        )
        events: list[GenerationEvent] = []
        while True:
            try:
                event = next(search)
            except StopIteration as finished:
                outcome = finished.value
                break
            events.append(event)
            # Appended one at a time rather than batched: §10.3 wants a
            # generation on the wire at least every 100 ms, and a batch that
            # waits for the next one is a batch that arrives late.
            append_events(connection, run_id=payload.run_id, events=[_stored(event)])
    finally:
        connection.close()

    selection = np.zeros(arrays.count, dtype=np.bool_)
    selection[rows] = outcome.selection
    result = build_result(
        arrays,
        features,
        mandate,
        assumptions,
        SelectionOutcome(
            eligible=eligible,
            winner=outcome.selection,
            locked=locked_all,
            returns=returns,
            contracted_share=contracted_revenue_share(arrays),
        ),
    )
    return WorkerOutcome(
        result=result,
        convergence=outcome.convergence,
        eligible=tuple(bool(flag) for flag in eligible.tolist()),
        selected=tuple(bool(flag) for flag in selection.tolist()),
        locked=tuple(bool(flag) for flag in locked_all.tolist()),
        population_size=outcome.population_size,
        generations_used=len(events),
        duration_ms=round((time.perf_counter() - started) * MILLISECONDS_PER_SECOND),
        blas_threads=observed_threads(),
        threads_pinned=threads_are_pinned(),
    )


def _stored(event: GenerationEvent) -> RunEvent:
    """One generation as the event log holds it.

    ``summary_json`` is ``docs/api.md`` §7's ``best`` object, serialised here and
    opaque to the store, so the stream can hand a subscriber bytes it already has
    rather than rebuilding the frame from columns.
    """
    return RunEvent(
        generation=event.generation,
        best_fitness=event.best_fitness,
        mean_fitness=event.mean_fitness,
        summary_json=canonical_json(
            {
                "projectCount": event.best_project_count,
                "capacityMw": event.best_capacity_mw,
                "equity_m": _millions(event.best_equity),
                "blendedIrr": event.best_blended_irr,
            }
        ),
    )


def _millions(euros: float) -> float:
    return euros / EUR_PER_EUR_MILLION
