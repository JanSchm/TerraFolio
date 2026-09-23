"""ULIDs for run identifiers, on the standard library alone.

A run id is the address in §11's "a run ID is shareable and reopens the exact
result", so it has to be unguessable enough not to be enumerated and sortable
enough that a list of runs comes back in the order they happened. A ULID is
both: 48 bits of millisecond timestamp followed by 80 bits of randomness,
rendered in Crockford's base32, which sorts lexicographically by time.

No dependency is added for this. The encoding is thirty lines, a new package
would have to be argued on the epic first, and every run's id would then depend
on a library version that nothing records.

Ids minted inside the same millisecond by one process increase rather than
scatter, so ``sorted(ids)`` is submission order even under a burst. Across
processes the random component carries it, and ``run.run_id`` is ``UNIQUE``:
a collision is a failed insert, never a silently overwritten run.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

__all__ = [
    "RUN_REF_SERIES",
    "ULID_LENGTH",
    "format_run_ref",
    "is_ulid",
    "new_ulid",
    "parse_run_ref",
    "ulid_timestamp",
]

# Crockford's base32: no I, L, O or U, so a run id read aloud from a committee
# pack cannot be mistyped into a different one.
_ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE: Final = {character: index for index, character in enumerate(_ALPHABET)}

ULID_BITS: Final = 128
TIMESTAMP_BITS: Final = 48
RANDOM_BITS: Final = ULID_BITS - TIMESTAMP_BITS
ULID_LENGTH: Final = 26
"""Characters in a ULID: 128 bits rendered five at a time, rounded up."""

_RANDOM_MASK: Final = (1 << RANDOM_BITS) - 1
_MILLISECONDS_PER_SECOND: Final = 1000


@dataclass(slots=True)
class _LastMinted:
    """The previous id's parts, so the next one inside the same millisecond sorts after it."""

    timestamp_ms: int = -1
    randomness: int = -1


_lock: Final = threading.Lock()
_last: Final = _LastMinted()


def _encode(value: int) -> str:
    """Render a 128-bit integer as 26 base32 characters, most significant first."""
    base = len(_ALPHABET)
    characters = [_ALPHABET[0]] * ULID_LENGTH
    for index in reversed(range(ULID_LENGTH)):
        value, remainder = divmod(value, base)
        characters[index] = _ALPHABET[remainder]
    return "".join(characters)


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * _MILLISECONDS_PER_SECOND)


def new_ulid(*, now_ms: int | None = None) -> str:
    """Mint a run id.

    ``now_ms`` exists for tests that need two ids inside one known millisecond;
    in production the clock supplies it.
    """
    timestamp = _now_ms() if now_ms is None else now_ms
    with _lock:
        if timestamp == _last.timestamp_ms:
            # Same millisecond: step the randomness so the pair still sorts in
            # the order they were minted. An overflow rolls into the next
            # millisecond rather than wrapping back to a smaller id.
            randomness = _last.randomness + 1
            if randomness > _RANDOM_MASK:
                timestamp += 1
                randomness = secrets.randbits(RANDOM_BITS)
        else:
            randomness = secrets.randbits(RANDOM_BITS)
        _last.timestamp_ms, _last.randomness = timestamp, randomness
    return _encode((timestamp << RANDOM_BITS) | randomness)


def is_ulid(text: str) -> bool:
    """Whether ``text`` is a well-formed ULID this module could have minted."""
    return len(text) == ULID_LENGTH and all(character in _DECODE for character in text)


def ulid_timestamp(text: str) -> datetime:
    """The instant encoded in a ULID, to the millisecond.

    A run's ``created_at`` is what the record reports; this exists so an auditor
    holding nothing but an id can still say when it was issued.
    """
    if not is_ulid(text):
        raise ValueError(f"not a ULID: {text!r}")
    value = 0
    for character in text:
        value = value * len(_ALPHABET) + _DECODE[character]
    return datetime.fromtimestamp((value >> RANDOM_BITS) / _MILLISECONDS_PER_SECOND, tz=UTC)


# --------------------------------------------------------------------------
# The run reference
# --------------------------------------------------------------------------

RUN_REF_SERIES: Final = "A"
"""The series letter in ``docs/api.md`` §8's ``"A-4"``.

The only evidence for the letter is that one example, so it is a named constant
and the primary key of ``run_sequence`` rather than a hardcoded prefix. It is
fixed at ``A`` for v1. It exists so that a store rebuilt from a backup, or one
that ever needs a reference scoped per fund, can start a distinct series
instead of minting labels that collide with references a committee has already
been shown.
"""

_SEPARATOR: Final = "-"


def format_run_ref(reference: int, *, series: str = RUN_REF_SERIES) -> str:
    """``4`` -> ``"A-4"`` — the label §7.6 says every run carries into its export."""
    if reference < 1:
        raise ValueError(f"a run reference counts from one; got {reference}")
    return f"{series}{_SEPARATOR}{reference}"


def parse_run_ref(label: str) -> tuple[str, int]:
    """``"A-4"`` -> ``("A", 4)``. The inverse of :func:`format_run_ref`."""
    series, separator, digits = label.partition(_SEPARATOR)
    if not separator or not series or not digits.isdigit():
        raise ValueError(f"not a run reference: {label!r}")
    return series, int(digits)
