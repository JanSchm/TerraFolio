"""The live pipeline: what is loaded now, what it hashes to, and what it serves.

Users add a project by dropping a file in and remove one by deleting it, while
the server runs (epic §2). So the pipeline is state, not a constant, and this is
the one object that owns it: everything else asks it for the current arrays, the
current hash, or a rendered response.

**The cache key is the ETag.** ``docs/api.md`` §2 requires
``sha256(pipelineHash ‖ assumptionSetId ‖ engineVersion ‖ holdYears)`` and is
explicit that `pipelineHash` alone will not do — it would make an assumption-set
change look like a pipeline change and, worse, leave a client holding IRRs
computed at a different hold period while the hash said nothing had moved. The
same four components key the response cache, which means nothing
mandate-dependent can be cached without ``holdYears``: the failure mode epic §5
names, closed by construction rather than by remembering.

``GET /pipeline`` is budgeted at under 200 ms warm at 300 projects and is hit on
every load of the mandate screen, so a warm hit returns bytes that were already
serialised rather than rebuilding three hundred pydantic models to throw them
away again.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass
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

__all__ = ["PipelineSource", "RenderedPipeline", "pipeline_etag"]

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
    project_count: int


class PipelineSource:
    """The loaded pipeline, reloadable, with its derived columns and cache.

    Not thread-safe by construction, and it does not need to be: a reload
    replaces whole objects rather than mutating them, so a request that is
    mid-flight when one happens keeps serving consistently from the load it
    started with.
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
        self._loaded_at = _now()
        self._result = load_pipeline(directory, assumptions)
        self._candidates = _candidates(self._result, assumptions)
        self._returns: OrderedDict[int, ProjectReturns] = OrderedDict()
        self._rendered: OrderedDict[int, RenderedPipeline] = OrderedDict()

    # -- what is loaded now --------------------------------------------------

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def result(self) -> LoadResult:
        return self._result

    @property
    def candidates(self) -> Candidates:
        return self._candidates

    @property
    def assumptions(self) -> AssumptionSet:
        return self._assumptions

    @property
    def pipeline_hash(self) -> str:
        return self._result.pipeline_hash

    @property
    def loaded_at(self) -> dt.datetime:
        return self._loaded_at

    def reload(self) -> LoadResult:
        """Re-read the directory and recompute everything derived from it.

        The caches are dropped wholesale rather than invalidated selectively.
        They are keyed by a hash that has just moved, so nothing in them could
        be hit again — and a selective invalidation is how a stale IRR survives
        a reload.
        """
        self._loaded_at = _now()
        self._result = load_pipeline(self._directory, self._assumptions)
        self._candidates = _candidates(self._result, self._assumptions)
        self._returns.clear()
        self._rendered.clear()
        return self._result

    # -- the mandate-dependent half -----------------------------------------

    def returns(self, hold_years: int) -> ProjectReturns:
        """Per-project returns at one hold period, memoised under that period.

        ``ProjectReturns`` carries ``hold_years`` itself, which is what makes
        this key checkable rather than merely conventional.
        """
        cached = self._returns.get(hold_years)
        if cached is not None:
            self._returns.move_to_end(hold_years)
            return cached
        computed = project_returns(self._candidates.arrays, self._assumptions, hold_years)
        self._returns[hold_years] = computed
        _trim(self._returns)
        return computed

    def projects(self, hold_years: int) -> tuple[ProjectScalars, ...]:
        """Every candidate's scalar record at one hold period, ordered by id."""
        return project_scalars(self._candidates, self.returns(hold_years))

    # -- the rendered response ----------------------------------------------

    def etag(self, hold_years: int) -> str:
        return pipeline_etag(
            pipeline_hash=self.pipeline_hash,
            assumption_set_id=self._assumptions.assumption_set_id,
            engine_version=self._engine_version,
            hold_years=hold_years,
        )

    def rendered(self, hold_years: int) -> RenderedPipeline:
        """§2's body and ETag for one hold period, built at most once."""
        cached = self._rendered.get(hold_years)
        if cached is not None:
            self._rendered.move_to_end(hold_years)
            return cached
        built = self._render(hold_years)
        self._rendered[hold_years] = built
        _trim(self._rendered)
        return built

    def _render(self, hold_years: int) -> RenderedPipeline:
        payload = PipelineResponse(
            pipeline_hash=self.pipeline_hash,
            assumption_set_id=self._assumptions.assumption_set_id,
            engine_version=self._engine_version,
            base_year=self._candidates.arrays.base_year,
            hold_years=hold_years,
            project_count=self._candidates.arrays.count,
            projects=self.projects(hold_years),
        )
        return RenderedPipeline(
            etag=self.etag(hold_years),
            body=payload.model_dump_json(by_alias=True).encode("utf-8"),
            project_count=self._candidates.arrays.count,
        )

    # -- the store ------------------------------------------------------------

    def snapshot(self) -> PipelineSnapshot:
        """The current load as 2B stores it, so a run can reference it.

        ``validation_status`` distinguishes a clean load from one that carried
        plausibility warnings, and ``invalid`` from a load where some file failed
        a tie-out — warnings never block (A-7), but the distinction is part of
        what a committee is entitled to see about the data a run used.
        """
        result = self._result
        return PipelineSnapshot(
            pipeline_hash=result.pipeline_hash,
            base_year=result.arrays.base_year,
            project_count=result.arrays.count,
            source_label=str(self._directory),
            loaded_at=self._loaded_at,
            file_hashes=dict(result.file_hashes),
            validation_status=_status(result),
            validation_json=_validation_json(result, self._loaded_at),
        )

    def record(self, connection: sqlite3.Connection) -> str:
        """Record the current snapshot, returning its hash. Idempotent."""
        return record_pipeline_snapshot(connection, self.snapshot(), recorded_at=_now())


def _candidates(result: LoadResult, assumptions: AssumptionSet) -> Candidates:
    return Candidates(
        arrays=result.arrays,
        files=result.files,
        derived=DerivedColumns(result.arrays, assumptions),
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
