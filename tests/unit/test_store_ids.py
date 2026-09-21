"""Run identifiers and the export's run reference.

Two different jobs. A ULID addresses the run — §11 makes it shareable, so it
has to be unguessable enough not to be enumerated. The reference is the short
label §7.6 says every run carries into its export, and it is the ordering key,
because a clock can skew between hosts and a reference cannot.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from terrafolio.store.ids import (
    RANDOM_BITS,
    TIMESTAMP_BITS,
    ULID_BITS,
    ULID_LENGTH,
    format_run_ref,
    is_ulid,
    new_ulid,
    parse_run_ref,
    ulid_timestamp,
)

AMBIGUOUS = frozenset("ILOU")


def test_a_run_id_is_twenty_six_crockford_characters() -> None:
    identifier = new_ulid()
    assert len(identifier) == ULID_LENGTH
    assert is_ulid(identifier)


def test_a_run_id_uses_no_letters_that_can_be_misread() -> None:
    """Crockford's alphabet drops I, L, O and U, so an id read aloud from a
    committee pack cannot be retyped into a different run."""
    assert AMBIGUOUS.isdisjoint(set("".join(new_ulid() for _ in range(50))))


def test_the_timestamp_and_the_randomness_fill_the_identifier() -> None:
    assert TIMESTAMP_BITS + RANDOM_BITS == ULID_BITS


def test_a_run_id_encodes_the_moment_it_was_minted() -> None:
    moment = datetime(2026, 9, 21, 9, 22, 11, tzinfo=UTC)
    milliseconds = int(moment.timestamp() * 1000)
    assert ulid_timestamp(new_ulid(now_ms=milliseconds)) == moment


def test_two_ids_minted_in_one_millisecond_differ_and_still_sort_in_order() -> None:
    """Within a burst the random component is stepped rather than redrawn, so
    a list of ids is still submission order."""
    minted = [new_ulid(now_ms=1_800_000_000_000) for _ in range(5)]
    assert len(set(minted)) == len(minted)
    assert minted == sorted(minted)


def test_ids_sort_by_time_across_milliseconds() -> None:
    earlier = new_ulid(now_ms=1_800_000_000_000)
    later = new_ulid(now_ms=1_800_000_000_001)
    assert earlier < later


def test_something_that_is_not_a_ulid_is_rejected() -> None:
    assert not is_ulid("A-4")
    assert not is_ulid("I" * ULID_LENGTH)
    with pytest.raises(ValueError, match="not a ULID"):
        ulid_timestamp("A-4")


def test_a_reference_renders_as_the_documented_label() -> None:
    """``api.md`` §8 publishes exactly one example of the format."""
    assert format_run_ref(4) == "A-4"


def test_parsing_a_label_inverts_formatting_it() -> None:
    assert parse_run_ref(format_run_ref(17)) == ("A", 17)


def test_a_reference_counts_from_one() -> None:
    with pytest.raises(ValueError, match="counts from one"):
        format_run_ref(0)


@pytest.mark.parametrize("label", ["A4", "-4", "A-", "A-x", ""])
def test_something_that_is_not_a_label_is_rejected(label: str) -> None:
    with pytest.raises(ValueError, match="not a run reference"):
        parse_run_ref(label)
