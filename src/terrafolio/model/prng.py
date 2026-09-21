"""The reference's xorshift32 generator, ported bit-for-bit.

The seed pipeline exists to be *reproducible*, and reproducibility here means
two different things that need two different streams:

**Parity.** ``tests/golden/fixtures/`` is 48 project files produced by the
JavaScript reference. Reproducing them field for field means reproducing its
PRNG exactly — the algorithm, the seeding, and the order in which draws are
consumed. That is what this module is for.

**Stability under insertion.** The reference seeds each project from its
*index* (``i * 97 + 13``), which means inserting one candidate at the head of
the pipeline reprices every project behind it, and every stored run stops
explaining its own numbers. The shipped generator therefore keys its stream on
``(project_id, assumption_set_id)`` instead — see :mod:`terrafolio.model.jitter`.

Both feed the same draw plan, so the two are the same generator with different
entropy rather than two implementations that might drift.

The port's one trap, recorded in ``js_prng.json``'s own ``algorithm.note``: in
JavaScript the state is a *signed* int32 between steps and the unsigned
coercion happens once, at the end, just before the division. Working in
unsigned 32-bit throughout is equivalent — exclusive-or and the shifts act on
the bit pattern, and signedness only changes how it is read — but a port that
uses Python's arbitrary-precision ``<<`` without masking, or ``>>`` where
JavaScript writes ``>>>`` on a negative value, produces a different stream that
still looks random.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final

__all__ = ["Xorshift32", "draws"]

_SHIFTS: Final = (13, 17, 5)  # structural: xorshift32's defining shift triple
_UINT32_MASK: Final = 0xFFFFFFFF  # structural: 32-bit wraparound, not a calibration value

_UINT32_SCALE: Final = _UINT32_MASK + 1
"""``2**32``, derived rather than written, so the mask and the divisor cannot disagree."""


class Xorshift32:
    """A seeded xorshift32 stream yielding floats in ``[0, 1)``.

    Mutable by nature: each call advances the state. Hand a fresh instance to
    each project rather than sharing one, which is what the reference does and
    what makes a project's draws depend on its own seed alone.
    """

    __slots__ = ("_state",)

    def __init__(self, seed: int) -> None:
        """Seed the stream, reproducing JavaScript's ``s >>> 0 || 1``.

        The coercion matters: seed 0 is *not* a valid xorshift state — it maps
        to itself forever — so the reference folds it to 1, and seeds 0 and 1
        yield the same stream. ``js_prng.json`` pins that with two streams that
        are byte-identical on purpose.
        """
        self._state = (seed & _UINT32_MASK) or 1

    def __call__(self) -> float:
        """Advance the state and return the next draw in ``[0, 1)``."""
        left, right, last = _SHIFTS
        state = self._state
        state ^= (state << left) & _UINT32_MASK
        state ^= state >> right
        state ^= (state << last) & _UINT32_MASK
        state &= _UINT32_MASK
        self._state = state
        return state / _UINT32_SCALE

    def __iter__(self) -> Iterator[float]:
        while True:
            yield self()


def draws(seed: int, count: int) -> tuple[float, ...]:
    """The first ``count`` draws of the stream seeded with ``seed``.

    A convenience for tests and for the fixture comparison; the generator itself
    consumes draws one at a time, because how many it takes depends on the
    project (a greenfield asset draws for its grid connection, a construction
    asset does not).
    """
    if count < 0:
        raise ValueError(f"count must not be negative, got {count}")
    rnd = Xorshift32(seed)
    return tuple(rnd() for _ in range(count))
