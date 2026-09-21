"""The snapshot hash, and the load-order invariance a stored run depends on."""

from __future__ import annotations

import json

from terrafolio.pipeline.snapshot import file_content_hash, snapshot_hash

RAW_A = b'{"id": "P01", "capacityMw": 180}'
RAW_B = b'{"id": "P02", "capacityMw": 90}'


def _hashes() -> dict[str, str]:
    return {"P01": file_content_hash(RAW_A), "P02": file_content_hash(RAW_B)}


def test_file_hash_is_prefixed_as_the_wire_writes_it() -> None:
    digest = file_content_hash(RAW_A)
    assert digest.startswith("sha256:")
    assert len(digest.removeprefix("sha256:")) == 64


def test_file_hash_is_over_bytes_not_over_meaning() -> None:
    """Reformatting a file without changing a number still changes the pipeline.

    A run that claimed otherwise could not be audited against what was on disk.
    """
    reformatted = json.dumps(json.loads(RAW_A), indent=4).encode()
    assert json.loads(reformatted) == json.loads(RAW_A)
    assert file_content_hash(reformatted) != file_content_hash(RAW_A)


def test_snapshot_hash_ignores_load_order() -> None:
    forward = _hashes()
    reversed_order = dict(reversed(list(forward.items())))
    assert list(reversed_order) != list(forward)
    assert snapshot_hash(reversed_order) == snapshot_hash(forward)


def test_editing_any_file_moves_the_snapshot() -> None:
    before = snapshot_hash(_hashes())
    after = _hashes() | {"P02": file_content_hash(RAW_B + b" ")}
    assert snapshot_hash(after) != before


def test_adding_a_file_moves_the_snapshot() -> None:
    before = snapshot_hash(_hashes())
    after = _hashes() | {"P03": file_content_hash(b"{}")}
    assert snapshot_hash(after) != before


def test_removing_a_file_moves_the_snapshot() -> None:
    """A rejected file leaves the snapshot: the run never saw it."""
    before = snapshot_hash(_hashes())
    after = {"P01": file_content_hash(RAW_A)}
    assert snapshot_hash(after) != before


def test_two_ids_swapping_contents_is_not_the_same_snapshot() -> None:
    """The pairs are hashed, not the multiset of digests."""
    straight = {"P01": file_content_hash(RAW_A), "P02": file_content_hash(RAW_B)}
    swapped = {"P01": file_content_hash(RAW_B), "P02": file_content_hash(RAW_A)}
    assert snapshot_hash(straight) != snapshot_hash(swapped)


def test_snapshot_hash_is_stable_across_calls() -> None:
    assert snapshot_hash(_hashes()) == snapshot_hash(_hashes())
