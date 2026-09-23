"""Snapshotting the two things that determine every number a run produced.

An assumption set is stored **on first use**, so a run's calibration can never
be edited out from under it — which is what makes §12's "assumption-set changes
are versioned and attributable" true rather than aspirational.

The table is keyed on the digest of the payload stored here, not on
``assumption_set_id``. The loader's id deliberately excludes ``[meta]`` so that
fixing a typo in a label does not invalidate every run compared against that
set; the consequence is that one id can correspond to two different files. Both
are kept, each run points at the exact one it used, and the two digests are
recorded under names that cannot be mistaken for each other:

* ``snapshot_hash`` — the digest of ``payload_json``. These bytes.
* ``assumption_set_hash`` / ``assumption_set_id`` — the loader's, over the TOML
  structure with ``[meta]`` removed and numbers normalised.

They differ **by construction**, not by accident: the dataclass tree flattens
the TOML's nesting, and the payload keeps ``meta``. A test asserts the
difference, so that nobody later "fixes" it into a single value.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any, Final

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.config.hashing import canonical_json, content_hash
from terrafolio.store.db import from_db_time, to_db_time
from terrafolio.store.errors import SnapshotConflictError, StoreError
from terrafolio.store.records import AssumptionSnapshot, PipelineSnapshot, ValidationStatus

__all__ = [
    "assumption_payload",
    "load_assumption_snapshot",
    "load_pipeline_snapshot",
    "record_assumption_set",
    "record_pipeline_snapshot",
    "snapshot_hash_of",
]

_IDENTITY_FIELDS: Final = frozenset({"assumption_set_id", "content_hash"})
"""Left out of the payload: they are statements *about* the set rather than
part of it, and putting a digest inside the thing it digests is a fixed point
nobody wants to maintain."""


def _plain(value: Any) -> Any:
    """One frozen dataclass tree as JSON-native structures.

    ``dataclasses.asdict`` cannot do this: it deep-copies anything that is not
    a dataclass or a builtin container, and ``AssumptionSet``'s mappings are
    ``MappingProxyType``, which is unpicklable — it raises rather than
    returning a wrong answer, which is the one mercy in it.

    Hand-rolled recursion is also what turns an enum *key* into its value, so
    the payload reads ``"solar"`` rather than ``"Technology.SOLAR"``.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _plain(getattr(value, field.name)) for field in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {_plain(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def assumption_payload(assumptions: AssumptionSet) -> str:
    """The canonical JSON the ``assumption_set`` table stores.

    Through ``config.hashing.canonical_json``, the project's one canonicaliser,
    so this payload and the loader's digest are ordered by the same rule.

    Deliberately *not* through ``numbers_as_floats``. That helper exists so a
    hand edit of ``18`` to ``18.0`` in TOML does not invent a new calibration;
    by the time an ``AssumptionSet`` exists, int-ness is a typed fact —
    ``quantisation_dp``, ``tournament_size``, ``generations`` — and rendering
    ``generations: 110.0`` into an audit record would be both wrong-looking and
    lossy on the way back.
    """
    payload = {
        field.name: _plain(getattr(assumptions, field.name))
        for field in dataclasses.fields(assumptions)
        if field.name not in _IDENTITY_FIELDS
    }
    return canonical_json(payload)


def snapshot_hash_of(assumptions: AssumptionSet) -> str:
    """The identity of the stored bytes for this set."""
    return content_hash(assumption_payload(assumptions))


def record_assumption_set(
    connection: sqlite3.Connection, assumptions: AssumptionSet, *, recorded_at: datetime
) -> str:
    """Store the snapshot if it is new; return its ``snapshot_hash`` either way.

    ``ON CONFLICT DO NOTHING``, never ``INSERT OR REPLACE`` — which deletes the
    conflicting row, and so would trip the immutability trigger, or on a
    connection without ``recursive_triggers`` silently evade it.
    """
    payload = assumption_payload(assumptions)
    digest = content_hash(payload)
    connection.execute(
        """
        INSERT INTO assumption_set (
            snapshot_hash, assumption_set_id, assumption_set_hash, name,
            label, version, created_by, supersedes, payload_json, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (snapshot_hash) DO NOTHING
        """,
        (
            digest,
            assumptions.assumption_set_id,
            assumptions.content_hash,
            assumptions.name,
            assumptions.meta.label,
            assumptions.meta.version,
            assumptions.meta.created_by,
            assumptions.meta.supersedes,
            payload,
            to_db_time(recorded_at),
        ),
    )
    return digest


def load_assumption_snapshot(
    connection: sqlite3.Connection, snapshot_hash: str
) -> AssumptionSnapshot:
    row = connection.execute(
        "SELECT * FROM assumption_set WHERE snapshot_hash = ?", (snapshot_hash,)
    ).fetchone()
    if row is None:
        raise StoreError(f"no assumption-set snapshot {snapshot_hash!r}")
    return AssumptionSnapshot(
        snapshot_hash=row["snapshot_hash"],
        assumption_set_id=row["assumption_set_id"],
        assumption_set_hash=row["assumption_set_hash"],
        name=row["name"],
        label=row["label"],
        version=row["version"],
        created_by=row["created_by"],
        supersedes=row["supersedes"],
        payload_json=row["payload_json"],
        recorded_at=from_db_time(row["recorded_at"]),
    )


def record_pipeline_snapshot(
    connection: sqlite3.Connection, snapshot: PipelineSnapshot, *, recorded_at: datetime
) -> str:
    """Store the pipeline snapshot if it is new; return its hash either way.

    Unlike :func:`record_assumption_set`, the key here is **not** a digest of
    what the row holds. ``pipeline_hash`` digests the project files; the base
    year, the project count and the validation verdict are not in it. So
    "already present" does not mean "already present with these values", and
    ``ON CONFLICT DO NOTHING`` alone would accept a second, different snapshot,
    return its hash as though it had been stored, and keep serving the first —
    a run would then take its year labels from a base year nobody recorded for
    it, on an export that carries no other year information.

    A conflict whose stored row **agrees** is the ordinary case: every reload of
    an unchanged pipeline hits it. A conflict that disagrees raises.
    """
    if (
        snapshot.validation_json is None
        and snapshot.validation_status is not ValidationStatus.UNKNOWN
    ):
        raise StoreError(
            f"a {snapshot.validation_status.value!r} snapshot must carry the report that says so"
        )
    file_hashes = canonical_json(dict(snapshot.file_hashes))
    cursor = connection.execute(
        """
        INSERT INTO pipeline_snapshot (
            pipeline_hash, base_year, project_count, source_label, loaded_at,
            file_hashes_json, validation_status, validation_json, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (pipeline_hash) DO NOTHING
        """,
        (
            snapshot.pipeline_hash,
            snapshot.base_year,
            snapshot.project_count,
            snapshot.source_label,
            to_db_time(snapshot.loaded_at),
            file_hashes,
            snapshot.validation_status.value,
            snapshot.validation_json,
            to_db_time(recorded_at),
        ),
    )
    if cursor.rowcount == 0:
        _assert_snapshot_agrees(connection, snapshot, file_hashes)
    return snapshot.pipeline_hash


def _assert_snapshot_agrees(
    connection: sqlite3.Connection, offered: PipelineSnapshot, file_hashes: str
) -> None:
    """What is already stored under this hash must be what is being offered.

    ``source_label`` and ``loaded_at`` are excluded: the same pipeline read
    twice, or read from a copy of the directory, is the same snapshot. The rest
    describes what a run would cite, so a difference there is two snapshots
    claiming one identity.
    """
    stored = load_pipeline_snapshot(connection, offered.pipeline_hash)
    differences = [
        f"{name}: stored {was!r}, offered {now!r}"
        for name, was, now in (
            ("baseYear", stored.base_year, offered.base_year),
            ("projectCount", stored.project_count, offered.project_count),
            ("fileHashes", canonical_json(dict(stored.file_hashes)), file_hashes),
            ("validationStatus", stored.validation_status.value, offered.validation_status.value),
            ("validationReport", stored.validation_json, offered.validation_json),
        )
        if was != now
    ]
    if differences:
        raise SnapshotConflictError(offered.pipeline_hash, differences)


def load_pipeline_snapshot(connection: sqlite3.Connection, pipeline_hash: str) -> PipelineSnapshot:
    row = connection.execute(
        "SELECT * FROM pipeline_snapshot WHERE pipeline_hash = ?", (pipeline_hash,)
    ).fetchone()
    if row is None:
        raise StoreError(f"no pipeline snapshot {pipeline_hash!r}")
    hashes: dict[str, str] = json.loads(row["file_hashes_json"])
    return PipelineSnapshot(
        pipeline_hash=row["pipeline_hash"],
        base_year=row["base_year"],
        project_count=row["project_count"],
        source_label=row["source_label"],
        loaded_at=from_db_time(row["loaded_at"]),
        file_hashes=hashes,
        validation_status=ValidationStatus(row["validation_status"]),
        validation_json=row["validation_json"],
    )
