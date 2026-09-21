"""Content hashing, in one place so two hashes cannot drift apart.

Issue 2A hashes a pipeline snapshot, this module hashes an assumption set, and
issue 2B stores both. Two canonicalisers would eventually disagree about
key order or float formatting, and the symptom would be a run that cannot find
its own inputs — so there is one.

Standard library only: ``config`` is imported by the numeric core.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

__all__ = [
    "ASSUMPTION_SET_ID_LENGTH",
    "canonical_json",
    "content_hash",
    "numbers_as_floats",
    "sha256_hex",
]

ASSUMPTION_SET_ID_LENGTH: Final = 16  # structural: identifier length, not a rate
"""Characters of the digest an assumption-set id keeps.

Structural: it is an identifier length, not a tolerance. Sixteen hex characters
is 64 bits, which is far past collision risk for a handful of hand-written
calibration files and short enough to read in a run record.
"""


def canonical_json(value: Any) -> str:
    """Serialise to the one byte-stable JSON form used for every hash.

    Sorted keys so insertion order cannot matter, tight separators so
    whitespace cannot, ``ensure_ascii=False`` so a non-ASCII label has one
    encoding rather than two, and ``allow_nan=False`` because ``NaN`` is not
    JSON and a hash input that is not valid JSON is a bug waiting for a reader.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def numbers_as_floats(value: Any) -> Any:
    """Recursively render every number as a float, leaving booleans alone.

    Without this, editing ``debt_tenor_years = 18`` to ``18.0`` in a TOML file
    would change the assumption-set id although nothing about the calibration
    moved — and ``json.dumps`` writes ``18`` and ``18.0`` differently.

    ``bool`` is checked before ``int`` deliberately: ``isinstance(True, int)``
    is true, and a flag rendered as ``1.0`` would both read wrongly and collide
    with a genuine number.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, Mapping):
        return {str(key): numbers_as_floats(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [numbers_as_floats(item) for item in value]
    return value


def sha256_hex(data: bytes | str) -> str:
    """The SHA-256 of ``data`` as lowercase hex, encoding text as UTF-8."""
    payload = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(payload).hexdigest()


def content_hash(data: bytes | str) -> str:
    """A prefixed digest, as ``docs/api.md`` writes pipeline and file hashes."""
    return f"sha256:{sha256_hex(data)}"
