"""The live pipeline: what is loaded now, what it hashes to, and what it serves.

Users add a project by dropping a file in and remove one by deleting it, while
the server runs (epic §2). So the pipeline is state, not a constant, and this is
the one object that owns it: everything else asks it for the current arrays, the
current hash, or a rendered response.

**A reload swaps one object.** Everything derived from a load — the arrays, the
validated files, the mandate-independent columns, and both caches — lives on a
:class:`Snapshot`, and :meth:`PipelineSource.reload` replaces that snapshot in a
single assignment. An earlier version assigned four fields in sequence, which
let a reader interleaved between two of them pair the *new* pipeline hash with
the *old* projects: it would then serve stale economics under an ETag that said
nothing had moved, and a client could start a run against data it never saw.
FastAPI runs sync handlers in a threadpool, so that interleaving was reachable.
Readers now take the snapshot **once** and read everything from it.

**The cache key is the ETag.** ``docs/api.md`` §2 requires
``sha256(pipelineHash ‖ assumptionSetId ‖ engineVersion ‖ holdYears)`` and is
explicit that ``pipelineHash`` alone will not do — it would make an
assumption-set change look like a pipeline change and, worse, leave a client
holding IRRs computed at a different hold period while the hash said nothing had
moved. Because each snapshot owns its own caches, nothing mandate-dependent can
outlive the load it was computed from: the failure mode epic §5 names, closed by
construction rather than by remembering to clear.

``GET /pipeline`` is budgeted at under 200 ms warm at 300 projects and is hit on
every load of the mandate screen, so a warm hit returns bytes that were already
serialised rather than rebuilding three hundred pydantic models to throw them
away again.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from terrafolio.api.scalars import Candidates, DerivedColumns, project_scalars
from terrafolio.api.wire import PipelineResponse, pipeline_status
from terrafolio.config.assumptions import AssumptionSet
from terrafolio.config.hashing import content_hash
from terrafolio.domain.results import ProjectScalars
from terrafolio.economics.returns import ProjectReturns, project_returns
from terrafolio.pipeline.loader import LoadResult, load_pipeline
from terrafolio.store.records import PipelineSnapshot, ValidationStatus
from terrafolio.store.snapshots import record_pipeline_snapshot

__all__ = ["PipelineSource", "RenderedPipeline", "Snapshot", "pipeline_etag"]

CACHE_ENTRIES: Final = 8
"""How many hold periods keep a rendered response.

A slider with 26 positions could in principle fill a cache with 26 copies of a
400 KB payload, so it is bounded; in practice a session moves between two or
three. Least-recently-used, because the hold period a user is working at is the
one they will ask for again.
"""


def pipeline_etag(
    *, pipeline_hash: str, assumption_set_id: str, engine_version: str, hold_years: int
) -> str:
    """§2's ETag over all four components, quoted as a strong validator.

    Joined with a separator that cannot occur inside any component, so two
    different tuples cannot concatenate to the same string — ``("ab", "c")`` and
    ``("a", "bc")`` would otherwise hash identically.
    """
    parts = "␟".join([pipeline_hash, assumption_set_id, engine_version, str(hold_years)])
    return f'"{content_hash(parts)}"'


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderedPipeline:
    """One ``GET /pipeline`` response, serialised once and reused."""

    etag: str
    body: bytes


@dataclass(frozen=True, slots=True, kw_only=True)
class Snapshot:
    """One load of the pipeline directory, and everything derived from it.

    Immutable in the fields that describe the load, so a reader holding one is
    reading a single consistent moment however many attributes it touches. The
    two caches are mutable — that is what they are for — but they belong to this
    load alone, so a stale entry cannot survive into the next one.
    """

    result: LoadResult
    candidates: Candidates
    loaded_at: dt.datetime
    assumptions: AssumptionSet
    engine_version: str

    returns_cache: OrderedDict[int, ProjectReturns] = field(default_factory=OrderedDict)
    rendered_cache: OrderedDict[int, RenderedPipeline] = field(default_factory=OrderedDict)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    """Guards the two caches.

    FastAPI runs sync handlers in a threadpool, so two requests for different
    hold periods touch these concurrently. ``OrderedDict`` is not the problem —
    the *sequence* is: one thread reads an entry, another evicts it while the
    first is between the ``get`` and the ``move_to_end``, and the first then
    raises ``KeyError`` on a key it had just seen. Cheap to hold, because
    everything inside is a dictionary operation; the expensive work is done
    outside it.
    """

    @property
    def pipeline_hash(self) -> str:
        return self.result.pipeline_hash

    def returns(self, hold_years: int) -> ProjectReturns:
        """Per-project returns at one hold period, memoised under that period.

        ``ProjectReturns`` carries ``hold_years`` itself, which is what makes
        this key checkable rather than merely conventional.
        """
        with self.lock:
            cached = _touch(self.returns_cache, hold_years)
        if cached is not None:
            return cached
        # Computed outside the lock: two threads asking for the same new hold
        # period both do the work, and the second's answer replaces the first's.
        # They are equal — this is a pure function of the snapshot — so the only
        # cost is a duplicated computation, which is much cheaper than holding a
        # lock across it.
        computed = project_returns(self.candidates.arrays, self.assumptions, hold_years)
        with self.lock:
            self.returns_cache[hold_years] = computed
            _trim(self.returns_cache)
        return computed

    def projects(self, hold_years: int) -> tuple[ProjectScalars, ...]:
        """Every candidate's scalar record at one hold period, ordered by id."""
        return project_scalars(self.candidates, self.returns(hold_years))

    def etag(self, hold_years: int) -> str:
        return pipeline_etag(
            pipeline_hash=self.pipeline_hash,
            assumption_set_id=self.assumptions.assumption_set_id,
            engine_version=self.engine_version,
            hold_years=hold_years,
        )

    def rendered(self, hold_years: int) -> RenderedPipeline:
        """§2's body and ETag for one hold period, built at most once."""
        with self.lock:
            cached = _touch(self.rendered_cache, hold_years)
        if cached is not None:
            return cached
        built = self._render(hold_years)
        with self.lock:
            self.rendered_cache[hold_years] = built
            _trim(self.rendered_cache)
        return built

    def _render(self, hold_years: int) -> RenderedPipeline:
        payload = PipelineResponse(
            pipeline_hash=self.pipeline_hash,
            assumption_set_id=self.assumptions.assumption_set_id,
            engine_version=self.engine_version,
            base_year=self.candidates.arrays.base_year,
            hold_years=hold_years,
            project_count=self.candidates.arrays.count,
            projects=self.projects(hold_years),
        )
        return RenderedPipeline(
            etag=self.etag(hold_years),
            body=payload.model_dump_json(by_alias=True).encode("utf-8"),
        )

    def as_record(self, directory: Path) -> PipelineSnapshot:
        """This load as 2B stores it, so a run can reference it.

        ``validation_status`` distinguishes a clean load from one that carried
        plausibility warnings, and ``invalid`` from a load where some file failed
        a tie-out — warnings never block (A-7), but the distinction is part of
        what a committee is entitled to see about the data a run used.
        """
        return PipelineSnapshot(
            pipeline_hash=self.pipeline_hash,
            base_year=self.result.arrays.base_year,
            project_count=self.result.arrays.count,
            source_label=str(directory),
            loaded_at=self.loaded_at,
            file_hashes=dict(self.result.file_hashes),
            validation_status=_status(self.result),
            validation_json=_validation_json(self.result),
        )


class PipelineSource:
    """The loaded pipeline, reloadable, as a succession of immutable snapshots.

    Thread-safe for the pattern the API uses: a reader calls :meth:`current`
    once and works from what it returns, and a reload rebinds one attribute.
    CPython makes that rebinding atomic, so a reader either sees the whole old
    load or the whole new one and never a mixture of the two.
    """

    def __init__(
        self,
        directory: Path,
        assumptions: AssumptionSet,
        *,
        engine_version: str,
    ) -> None:
        self._directory = directory
        self._assumptions = assumptions
        self._engine_version = engine_version
        self._reloading = threading.Lock()
        self._snapshot = self._load()

    def _load(self) -> Snapshot:
        result = load_pipeline(self._directory, self._assumptions)
        return Snapshot(
            result=result,
            candidates=Candidates(
                arrays=result.arrays,
                files=result.files,
                derived=DerivedColumns(result.arrays, self._assumptions),
            ),
            loaded_at=_now(),
            assumptions=self._assumptions,
            engine_version=self._engine_version,
        )

    def current(self) -> Snapshot:
        """The load in force right now.

        **Call this once per request and read everything from the result.**
        Touching the convenience properties below several times in one operation
        reintroduces exactly the race this class exists to remove.
        """
        return self._snapshot

    def reload(self) -> LoadResult:
        """Re-read the directory and swap in everything derived from it.

        One assignment, so there is no window in which the new hash is visible
        alongside the old projects. The previous snapshot's caches go with it,
        which is why nothing has to be invalidated selectively — selective
        invalidation is how a stale IRR survives a reload.

        Serialised, because two overlapping reloads across a directory that is
        still being edited can finish out of order: the slower, *older* load
        would assign last and leave the server serving data that has already
        been superseded, with no event to correct it. Readers are never blocked
        — they take the current snapshot without the lock.
        """
        with self._reloading:
            self._snapshot = self._load()
            return self._snapshot.result

    # -- convenience, each a single read of the current snapshot --------------

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def assumptions(self) -> AssumptionSet:
        return self._assumptions

    @property
    def result(self) -> LoadResult:
        return self._snapshot.result

    @property
    def candidates(self) -> Candidates:
        return self._snapshot.candidates

    @property
    def pipeline_hash(self) -> str:
        return self._snapshot.pipeline_hash

    @property
    def loaded_at(self) -> dt.datetime:
        return self._snapshot.loaded_at

    def record(self, connection: sqlite3.Connection, snapshot: Snapshot | None = None) -> str:
        """Record a snapshot, returning its hash. Idempotent.

        Takes the snapshot explicitly so a caller that has already committed to
        one records *that* one, rather than whatever happens to be current by
        the time it gets here.
        """
        chosen = snapshot if snapshot is not None else self._snapshot
        return record_pipeline_snapshot(
            connection, chosen.as_record(self._directory), recorded_at=_now()
        )


def _status(result: LoadResult) -> ValidationStatus:
    if result.rejected:
        return ValidationStatus.INVALID
    return ValidationStatus.WARNINGS if result.warnings else ValidationStatus.VALID


LOAD_EVENT_FIELDS: Final[set[str]] = {"loaded_at", "duration_ms"}
"""What describes the *act* of loading rather than what was loaded.

Both are excluded from the stored validation report. ``pipeline_snapshot`` is
content-addressed, and ``record_pipeline_snapshot`` compares the stored report
against the one offered — deliberately ignoring ``loaded_at`` and
``source_label`` on the row, because "the same pipeline read twice, or read from
a copy of the directory, is the same snapshot". Embedding the timestamp and the
elapsed milliseconds *inside* the report smuggled them straight back past that
check: a server restarted against an existing database offered a report that
differed in two fields, and every subsequent run answered 409 PIPELINE_MOVED for
a pipeline that had not moved.
"""


def _validation_json(result: LoadResult) -> str:
    """The report as stored: everything the load *found*, and nothing about when.

    ``GET /pipeline/status`` still serves ``loadedAt`` and ``durationMs`` — they
    are exactly what a reader of a live server wants. They simply cannot be part
    of an identity.
    """
    return pipeline_status(result, loaded_at=_EPOCH).model_dump_json(
        by_alias=True, exclude=LOAD_EVENT_FIELDS
    )


_EPOCH: Final = dt.datetime(dt.MINYEAR, 1, 1, tzinfo=dt.UTC)
"""A placeholder for the excluded timestamp; it never reaches the output."""


def _touch[K, V](cache: OrderedDict[K, V], key: K) -> V | None:
    """Read an entry and mark it most-recently-used, or report a miss.

    ``get`` then ``move_to_end`` as one step: split apart, another thread can
    evict the key in between and the ``move_to_end`` raises ``KeyError`` on an
    entry this thread had just been handed.
    """
    cached = cache.get(key)
    if cached is None:
        return None
    cache.move_to_end(key)
    return cached


def _trim[K, V](cache: OrderedDict[K, V]) -> None:
    while len(cache) > CACHE_ENTRIES:
        cache.popitem(last=False)


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)
