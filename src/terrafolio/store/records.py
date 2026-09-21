"""The store's own value objects.

``RunRecord`` is the served result and belongs to ``domain``. It does not carry
everything §12 wants recorded — who asked for the run, which candidates were
eligible, what the effort preset actually resolved to, which warnings the
result screen raised — because none of that is part of the wire contract. Those
live here, alongside the record, in :class:`StoredRun`.

Frozen dataclasses rather than pydantic models, matching
``config/assumptions.py``: these never cross an HTTP boundary, so they need
ordering and immutability, not validation and aliasing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from terrafolio.domain.enums import Effort, RunStatus
from terrafolio.domain.mandate import Mandate
from terrafolio.domain.results import FeasibilityWarning, RunProvenance, RunRecord

__all__ = [
    "AssumptionSnapshot",
    "PipelineSnapshot",
    "RunEvent",
    "RunFailure",
    "RunSubmission",
    "RunSummary",
    "StoredRun",
    "ValidationStatus",
]


class ValidationStatus(StrEnum):
    """How the loaded pipeline stood when its snapshot was taken.

    Four values, owned here rather than imported from issue 2A's loader. 2A
    owns *what* validation means and *how* it is reported; this table records
    only whether a run may cite the snapshot, which the store can answer
    without knowing the report's shape. The mapping from 2A's report onto one
    of these four belongs above both, in ``runner``.
    """

    VALID = "valid"
    WARNINGS = "warnings"
    INVALID = "invalid"
    UNKNOWN = "unknown"
    """No validation was recorded. Distinct from ``valid`` on purpose: a caller
    that recorded nothing must be visibly uncertain, never silently clean."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PipelineSnapshot:
    """The candidate set a run saw, addressed by its hash."""

    pipeline_hash: str
    base_year: int
    """A-13's one base year for the whole pipeline. The run result does not
    carry it, and the 30-year series is indexed from it — so this is where a
    reopened run recovers the year labels for its chart and its cash-flow CSV."""
    project_count: int
    source_label: str
    loaded_at: datetime
    file_hashes: Mapping[str, str]
    validation_status: ValidationStatus
    validation_json: str | None
    """Issue 2A's report, already serialised by the caller. Stored, checked for
    well-formedness, never parsed — so when 2A's report type changes, nothing
    here changes with it."""


@dataclass(frozen=True, slots=True, kw_only=True)
class AssumptionSnapshot:
    """One calibration, as it stood when a run first used it."""

    snapshot_hash: str
    """The digest of ``payload_json``. The identity of exactly these bytes."""
    assumption_set_id: str
    """The loader's 16-hex value id — what ``RunProvenance`` and the wire carry."""
    assumption_set_hash: str
    name: str
    label: str
    version: int
    created_by: str
    supersedes: str
    payload_json: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class RunEvent:
    """One generation of the search, as the stream reported it."""

    generation: int
    best_fitness: float
    mean_fitness: float
    summary_json: str
    """``api.md`` §7's ``best`` object, already serialised. Opaque here."""


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredRun:
    """A run as the store holds it: the served record, plus what §12 audits."""

    record: RunRecord
    result_json: str
    """The exact bytes ``GET /optimisations/{id}`` serves. Never re-serialised
    on read: a pydantic upgrade that changed float formatting would otherwise
    silently rewrite the bytes of a run from three years ago."""
    run_reference: int
    """The monotonic integer behind ``record.run_ref``. The ordering key for
    every list query — never ``created_at``, whose clock can skew between hosts."""
    created_by: str
    finished_at: datetime | None
    population_size: int
    generations_planned: int
    generations_used: int | None
    eligible_ids: tuple[str, ...]
    warnings_raised: tuple[FeasibilityWarning, ...]
    base_year: int
    assumption_snapshot_hash: str
    deterministic_reduction: bool
    error_code: str | None
    error_message: str | None
    schema_version: int


@dataclass(frozen=True, slots=True, kw_only=True)
class RunSummary:
    """Enough of a run to list it without reading a 400 KB result."""

    run_id: str
    run_reference: int
    run_ref: str
    status: RunStatus
    created_at: datetime
    created_by: str
    effort: Effort
    duration_ms: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class RunSubmission:
    """Everything a run is committed to at the moment it is accepted.

    A single object rather than a dozen keyword arguments, because these are
    not independent settings: they are one decision, taken in the request
    handler that answers 202, and a trigger refuses to let any of them move
    afterwards.

    ``provenance`` carries the **resolved** seed. ``api.md`` §6.2 makes drawing
    it the server's job when the client sends none, and epic §5 is blunt that a
    run with no recorded seed is not a valid run — so it is drawn here, not in
    the worker, and the queued record already names it.
    """

    created_at: datetime
    created_by: str
    mandate: Mandate
    effort: Effort
    provenance: RunProvenance
    eligible_ids: tuple[str, ...]
    """The candidate set the search will index by position. Recorded because
    min DSCR is a *derived* screen, so this is not recoverable from the mandate."""
    population_size: int
    generations_planned: int
    deterministic_reduction: bool
    locked_ids: tuple[str, ...] = ()
    excluded_ids: tuple[str, ...] = ()
    run_id: str | None = None
    """Supplied only by a test that needs a known id; otherwise one is minted."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RunFailure:
    """Why a run ended without a result.

    A pair rather than two optional strings, so "failed" and "carries a reason"
    cannot come apart — which is what the schema's
    ``status <> 'failed' OR error_code IS NOT NULL`` says in SQL.
    """

    code: str
    message: str
