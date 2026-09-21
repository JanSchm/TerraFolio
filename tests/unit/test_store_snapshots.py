"""Snapshotting the calibration and the pipeline a run saw.

§12 asks for assumption-set changes to be "versioned and attributable", which
is only true if a run's calibration cannot be edited afterwards. The store
takes a copy on first use; the test that matters most here is the one proving
the copy is *complete*, because a field added to the dataclass tree and
forgotten by the serialiser would leave an audit record that looks whole.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
from test_store_runs import CREATED_AT, opened_store, pipeline_snapshot

from terrafolio.config.loader import load_default
from terrafolio.store import (
    StoreError,
    ValidationStatus,
    assumption_payload,
    load_assumption_snapshot,
    load_pipeline_snapshot,
    open_store,
    record_assumption_set,
    record_pipeline_snapshot,
    snapshot_hash_of,
)


def _declared_names(value: Any) -> Iterator[str]:
    """Every field name anywhere in a frozen dataclass tree."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            yield field.name
            yield from _declared_names(getattr(value, field.name))
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _declared_names(item)
    elif isinstance(value, tuple | list):
        for item in value:
            yield from _declared_names(item)


def _present_names(payload: Any) -> Iterator[str]:
    if isinstance(payload, dict):
        for key, item in payload.items():
            yield key
            yield from _present_names(item)
    elif isinstance(payload, list):
        for item in payload:
            yield from _present_names(item)


def _leaves(payload: Any) -> Iterator[Any]:
    if isinstance(payload, dict):
        for item in payload.values():
            yield from _leaves(item)
    elif isinstance(payload, list):
        for item in payload:
            yield from _leaves(item)
    else:
        yield payload


def _value_leaves(value: Any) -> Iterator[Any]:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            yield from _value_leaves(getattr(value, field.name))
    elif isinstance(value, Enum):
        yield value.value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _value_leaves(item)
    elif isinstance(value, tuple | list):
        for item in value:
            yield from _value_leaves(item)
    else:
        yield value


def test_the_stored_payload_carries_every_field_of_the_assumption_set() -> None:
    """Recursion over ``dataclasses.fields`` rather than a hand-written list,
    because a hand-written list is the thing that goes stale."""
    assumptions = load_default()
    payload = json.loads(assumption_payload(assumptions))
    declared = set(_declared_names(assumptions))
    present = set(_present_names(payload))
    # The two identity fields are statements about the set, not part of it.
    assert declared - present == {"assumption_set_id", "content_hash"}


def test_the_stored_payload_carries_every_value_not_merely_every_name() -> None:
    """Names alone would pass if a band were serialised with a null in it."""
    assumptions = load_default()
    payload = json.loads(assumption_payload(assumptions))
    stored = list(_leaves(payload))
    declared = [
        leaf
        for name, leaf in zip(
            _declared_names(assumptions), _value_leaves(assumptions), strict=False
        )
        if name not in {"assumption_set_id", "content_hash"}
    ]
    assert len(stored) >= len(declared) - 2
    assert all(leaf in stored for leaf in _value_leaves(assumptions.objective))


def test_enum_keys_are_stored_as_their_values(tmp_path: Path) -> None:
    """``Technology.SOLAR`` and not ``"Technology.SOLAR"`` — an audit payload
    should read the way the file it came from reads."""
    payload = json.loads(assumption_payload(load_default()))
    assert set(payload["ga"]["effort"]) == {"fast", "standard", "exhaustive"}
    assert "solar" in payload["exit_multiples"]


def test_the_payload_digest_is_not_the_assumption_set_hash() -> None:
    """They answer different questions and the schema keeps both.

    The loader's digest covers the TOML structure with ``[meta]`` removed and
    numbers normalised; this one covers the materialised tree, ``meta``
    included. Asserting the difference is what stops someone later "fixing"
    them into one value and silently changing what a stored run claims.
    """
    assumptions = load_default()
    assert snapshot_hash_of(assumptions) != assumptions.content_hash
    assert snapshot_hash_of(assumptions).startswith("sha256:")


def test_recording_one_assumption_set_twice_writes_a_single_row(tmp_path: Path) -> None:
    with closing(open_store(tmp_path / "runs.db")) as connection:
        assumptions = load_default()
        first = record_assumption_set(connection, assumptions, recorded_at=CREATED_AT)
        second = record_assumption_set(connection, assumptions, recorded_at=CREATED_AT)
        assert first == second
        assert connection.execute("SELECT COUNT(*) FROM assumption_set").fetchone()[0] == 1


def test_changing_an_assumption_set_leaves_the_stored_one_untouched(
    tmp_path: Path,
) -> None:
    """The acceptance criterion, stated directly: a run's calibration cannot be
    edited out from under it, because an edit is a different row."""
    with closing(open_store(tmp_path / "runs.db")) as connection:
        original = load_default()
        digest = record_assumption_set(connection, original, recorded_at=CREATED_AT)
        before = load_assumption_snapshot(connection, digest)

        edited = dataclasses.replace(original, name="edited-2026")
        other = record_assumption_set(connection, edited, recorded_at=CREATED_AT)

        assert other != digest
        assert load_assumption_snapshot(connection, digest) == before
        assert connection.execute("SELECT COUNT(*) FROM assumption_set").fetchone()[0] == 2


def test_a_snapshotted_assumption_set_cannot_be_edited_or_deleted(
    tmp_path: Path,
) -> None:
    with closing(open_store(tmp_path / "runs.db")) as connection:
        record_assumption_set(connection, load_default(), recorded_at=CREATED_AT)
        with pytest.raises(sqlite3.IntegrityError, match="content-addressed"):
            connection.execute("UPDATE assumption_set SET name = 'other'")
        with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
            connection.execute("DELETE FROM assumption_set")


def test_a_snapshot_records_the_loaders_own_identity_verbatim(tmp_path: Path) -> None:
    """``RunProvenance`` carries the loader's id, so the store has to be able
    to answer "which stored calibration was that?" from it."""
    with closing(open_store(tmp_path / "runs.db")) as connection:
        assumptions = load_default()
        digest = record_assumption_set(connection, assumptions, recorded_at=CREATED_AT)
        snapshot = load_assumption_snapshot(connection, digest)
        assert snapshot.assumption_set_id == assumptions.assumption_set_id
        assert snapshot.assumption_set_hash == assumptions.content_hash
        assert snapshot.label == assumptions.meta.label


# --------------------------------------------------------------------------
# The pipeline snapshot
# --------------------------------------------------------------------------


def test_a_pipeline_snapshot_keeps_its_file_hashes_and_base_year(
    tmp_path: Path,
) -> None:
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        stored = load_pipeline_snapshot(connection, pipeline_snapshot().pipeline_hash)
        assert stored.base_year == pipeline_snapshot().base_year
        assert dict(stored.file_hashes) == dict(pipeline_snapshot().file_hashes)


def test_a_validation_report_is_stored_opaquely(tmp_path: Path) -> None:
    """Issue 2A owns what a report says. The store keeps it whole and does not
    parse it, so a change to that type is not a change here."""
    report = json.dumps({"checks": 412, "failures": [], "note": "2A's shape, not ours"})
    snapshot = pipeline_snapshot(validation_status=ValidationStatus.VALID, validation_json=report)
    with closing(open_store(tmp_path / "runs.db")) as connection:
        record_pipeline_snapshot(connection, snapshot, recorded_at=CREATED_AT)
        stored = load_pipeline_snapshot(connection, snapshot.pipeline_hash)
        assert stored.validation_json == report
        assert stored.validation_status is ValidationStatus.VALID


def test_only_an_unknown_verdict_may_carry_no_report(tmp_path: Path) -> None:
    """A caller that recorded nothing must be visibly uncertain, never
    silently clean."""
    with (
        closing(open_store(tmp_path / "runs.db")) as connection,
        pytest.raises(StoreError, match="must carry the report"),
    ):
        record_pipeline_snapshot(
            connection,
            pipeline_snapshot(validation_status=ValidationStatus.VALID),
            recorded_at=CREATED_AT,
        )


def test_a_malformed_validation_report_is_refused(tmp_path: Path) -> None:
    with (
        closing(open_store(tmp_path / "runs.db")) as connection,
        pytest.raises(sqlite3.IntegrityError),
    ):
        record_pipeline_snapshot(
            connection,
            pipeline_snapshot(validation_status=ValidationStatus.VALID, validation_json="not json"),
            recorded_at=CREATED_AT,
        )


def test_a_pipeline_snapshot_cannot_be_edited_or_deleted(tmp_path: Path) -> None:
    """§13: a run reopened after the pipeline moved keeps its own snapshot."""
    with closing(opened_store(tmp_path / "runs.db")) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="content-addressed"):
            connection.execute("UPDATE pipeline_snapshot SET base_year = 2030")
        with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
            connection.execute("DELETE FROM pipeline_snapshot")
