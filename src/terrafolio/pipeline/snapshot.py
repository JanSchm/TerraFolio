"""The pipeline snapshot hash: what pins a stored run to the files it actually saw.

``snapshot_hash`` is ``sha256`` over the **sorted** ``(id, file content hash)`` pairs,
so any edit to any loaded file moves it, and the order the directory happened to be
read in does not. ``docs/api.md`` §3 carries it as ``pipelineHash`` and rejects a run
request whose hash no longer matches with ``409 PIPELINE_MOVED``.

Hashing goes through :mod:`terrafolio.config.hashing` rather than calling ``hashlib``
here: that module exists so the assumption-set digest and this one cannot drift apart
on key order or float formatting.

Only files that **loaded** are in the snapshot. A file rejected for a tie-out failure
is reported by name but contributes nothing, because the run never saw it — including
it would make two pipelines with different usable contents hash the same.
"""

from __future__ import annotations

from collections.abc import Mapping

from terrafolio.config.hashing import canonical_json, content_hash

__all__ = ["file_content_hash", "snapshot_hash"]


def file_content_hash(raw: bytes) -> str:
    """The content hash of one project file, over its **bytes as committed**.

    Deliberately not over the parsed model: an analyst reformatting a file without
    changing a number is still a change to the pipeline the run saw, and a run that
    claims otherwise cannot be audited.
    """
    return content_hash(raw)


def snapshot_hash(file_hashes: Mapping[str, str]) -> str:
    """Hash the whole loaded pipeline from its ``id`` to content-hash mapping.

    The pairs are sorted by ``id`` — canonical order, the same ordering the arrays
    use — so shuffling the load order leaves the hash identical.
    """
    pairs = [[project_id, file_hashes[project_id]] for project_id in sorted(file_hashes)]
    return content_hash(canonical_json(pairs))
