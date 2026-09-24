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

    @property
    def pipeline_hash(self) -> str:
        return self.result.pipeline_hash

    def returns(self, hold_years: int) -> ProjectReturns:
        """Per-project returns at one hold period, memoised under that period.

        ``ProjectReturns`` carries ``hold_years`` itself, which is what makes
        this key checkable rather than merely conventional.
        """
        cached = self.returns_cache.get(hold_years)
        if cached is not None:
            self.returns_cache.move_to_end(hold_years)
            return cached
        computed = project_returns(self.candidates.arrays, self.assumptions, hold_years)
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
        cached = self.rendered_cache.get(hold_years)
        if cached is not None:
            self.rendered_cache.move_to_end(hold_years)
            return cached
        built = self._render(hold_years)
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
            validation_json=_validation_json(self.result, self.loaded_at),
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
        """
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


def _validation_json(result: LoadResult, loaded_at: dt.datetime) -> str:
    """The report exactly as ``GET /pipeline/status`` serves it.

    Stamped with the load's **own** time rather than the moment it is recorded.
    ``record_pipeline_snapshot`` is content-addressed and compares the stored
    report against the one offered, so a fresh timestamp here would make the
    second record of an unchanged pipeline look like a conflicting one.
    """
    return pipeline_status(result, loaded_at=loaded_at).model_dump_json(by_alias=True)


def _trim[K, V](cache: OrderedDict[K, V]) -> None:
    while len(cache) > CACHE_ENTRIES:
        cache.popitem(last=False)


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)
