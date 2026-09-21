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

__all__ = [
    "DuplicateGenerationError",
    "DuplicateRunError",
    "RunAlreadyFinishedError",
    "RunIdentityChangedError",
    "RunNotFoundError",
    "SchemaUnsupportedError",
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
