"""The ported PRNG reproduces the reference's stream exactly.

Not "closely": exactly. Every project's capacity factor, opex, risk score, PPA
terms and entry yield come out of this generator, so one differing bit in the
stream is a different pipeline, and the golden corpus stops being an oracle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest

from terrafolio.model.prng import Xorshift32, draws

FIXTURES: Final = Path(__file__).resolve().parents[1] / "golden" / "fixtures"


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    """``js_prng.json`` — the reference's own record of its generator."""
    data: dict[str, Any] = json.loads((FIXTURES / "js_prng.json").read_text(encoding="utf-8"))
    return data


def test_the_algorithm_is_the_one_the_fixture_describes(spec: dict[str, Any]) -> None:
    """A guard on the fixture, not on the port.

    If the recorded algorithm ever changes, these tests should fail loudly
    rather than keep passing against a stream nobody meant.
    """
    assert spec["algorithm"]["name"] == "xorshift32"
    assert spec["algorithm"]["output"] == "x / 4294967296"


def test_every_recorded_stream_is_reproduced_exactly(spec: dict[str, Any]) -> None:
    """All three streams, bit for bit — including the 1,000 draws at seed 42."""
    for stream in spec["streams"]:
        mine = draws(stream["seed"], stream["count"])
        assert len(mine) == stream["count"]
        for index, (got, want) in enumerate(zip(mine, stream["draws"], strict=True)):
            assert got == want, f"seed {stream['seed']} draw {index}: {got!r} != {want!r}"


def test_seed_zero_is_coerced_to_one(spec: dict[str, Any]) -> None:
    """``s >>> 0 || 1``: zero is not a valid xorshift state, so it folds to one.

    The fixture records seeds 0 and 1 as two streams that are deliberately
    identical; a port that skips the coercion sticks at zero forever and returns
    the same draw every time.
    """
    by_seed = {stream["seed"]: stream for stream in spec["streams"]}
    assert draws(0, by_seed[0]["count"]) == draws(1, by_seed[1]["count"])
    assert len(set(draws(0, 8))) > 1


def test_draws_land_in_the_unit_interval() -> None:
    """``[0, 1)`` — the upper bound is open, which the inclusion draw depends on."""
    for value in draws(42, 1000):
        assert 0.0 <= value < 1.0


def test_each_instance_carries_its_own_state() -> None:
    """Two streams on one seed run in lockstep; one stream does not repeat itself.

    The generator hands a fresh instance to each project, so a shared or
    accidentally-global state would silently correlate every project's draws.
    """
    first, second = Xorshift32(13), Xorshift32(13)
    assert [first() for _ in range(8)] == [second() for _ in range(8)]
    stream = Xorshift32(13)
    assert stream() != stream()


def test_a_negative_count_is_refused() -> None:
    with pytest.raises(ValueError, match="count"):
        draws(42, -1)
