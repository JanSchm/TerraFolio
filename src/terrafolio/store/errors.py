"""What the store refuses, named so a caller can act on it.

One class per condition an API layer answers differently. ``api.md`` §11 maps
three of these onto status codes — ``RunNotFoundError`` is 404 ``RUN_NOT_FOUND``,
and everything else that escapes is 500 ``STORE_ERROR`` — so a single
``StoreError`` with a message would push that decision into string matching.

Follows ``config.loader.AssumptionError``'s precedent: the error says which
run, which generation or which hash, because the reader is an engineer holding
an audit question, not a stack trace.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = [
    "DuplicateGenerationError",
    "DuplicateRunError",
    "RunAlreadyFinishedError",
    "RunIdentityChangedError",
    "RunNotFinishedError",
    "RunNotFoundError",
    "SchemaUnsupportedError",
    "SnapshotConflictError",
    "StoreError",
    "UnknownSnapshotError",
]


class StoreError(RuntimeError):
    """Anything the run store refuses to do."""


class SchemaUnsupportedError(StoreError):
    """The database file, or the SQLite build, is not one this release can use."""


class RunNotFoundError(StoreError):
    """No run with that id or reference. ``api.md`` §11's 404."""

    def __init__(self, identifier: str) -> None:
        super().__init__(f"no run {identifier!r}")
        self.identifier = identifier


class DuplicateRunError(StoreError):
    """A run with that id was already opened."""

    def __init__(self, run_id: str) -> None:
        super().__init__(f"run {run_id!r} already exists")
        self.run_id = run_id


class RunNotFinishedError(StoreError):
    """A record was offered as a run's result without being one.

    Separate from :class:`RunAlreadyFinishedError`, which is the opposite
    problem: this is a caller handing over a record that is still ``running``,
    or one that reports no duration, and calling it an outcome.
    """

    def __init__(self, run_id: str, reason: str) -> None:
        super().__init__(f"run {run_id!r} is not finished: {reason}")
        self.run_id = run_id
        self.reason = reason


class RunAlreadyFinishedError(StoreError):
    """Something tried to write a result over a run that already has one.

    Two workers believe they own the run, or one is replaying stale state.
    Neither is a condition to swallow: a stored run is what an investment
    committee has already seen (§11).
    """

    def __init__(self, run_id: str, status: str) -> None:
        super().__init__(f"run {run_id!r} is already {status}")
        self.run_id = run_id
        self.status = status


class RunIdentityChangedError(StoreError):
    """A result was offered for a run whose recorded inputs it does not match."""

    def __init__(self, run_id: str, field: str, stored: object, offered: object) -> None:
        super().__init__(
            f"run {run_id!r} was opened with {field}={stored!r}, but the result carries {offered!r}"
        )
        self.run_id = run_id
        self.field = field


class SnapshotConflictError(StoreError):
    """Two different snapshots claim one hash.

    ``pipeline_hash`` digests the project files, not the base year or the
    validation verdict recorded beside them — so "already stored" and "already
    stored with these values" are different questions, and only the second one
    makes a stored run's year labels trustworthy.
    """

    def __init__(self, pipeline_hash: str, differences: Sequence[str]) -> None:
        super().__init__(
            f"pipeline snapshot {pipeline_hash!r} is already stored with different "
            f"values ({'; '.join(differences)}); it cannot be re-recorded"
        )
        self.pipeline_hash = pipeline_hash
        self.differences = tuple(differences)


class UnknownSnapshotError(StoreError):
    """A run cited a pipeline or assumption-set snapshot that was never recorded."""

    def __init__(self, kind: str, digest: str) -> None:
        super().__init__(
            f"no {kind} snapshot {digest!r}; record it before opening a run against it"
        )
        self.kind = kind
        self.digest = digest


class DuplicateGenerationError(StoreError):
    """A generation was appended twice to one run's log.

    The worker restarted mid-run or double-emitted. Ignoring it would let the
    persisted curve disagree with ``RunRecord.convergence``, which is what §6's
    chart and every cross-release regression test read.
    """

    def __init__(self, run_id: str, generation: int) -> None:
        super().__init__(f"run {run_id!r} already has generation {generation}")
        self.run_id = run_id
        self.generation = generation
