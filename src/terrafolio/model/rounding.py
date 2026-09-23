"""Half-up rounding, because neither Python nor the reference's language gives it.

The JavaScript reference rounds in four places that reach the emitted files or
the oracle: the capture price to a whole €/MWh, the development risk score to
one decimal, the contracted share to two, and min DSCR to two. Reproducing its
numbers means reproducing its tie rule.

``Math.round(x)`` in JavaScript is ``floor(x + 0.5)`` — halves go **up**, toward
positive infinity. Python's built-in :func:`round` is banker's rounding, which
sends halves to the nearest *even* value: ``round(0.5) == 0`` and
``round(2.5) == 2``. On a capture price of ``58 x 0.68 = 39.44`` the two agree;
on anything landing exactly on a half they do not, and the disagreement then
propagates through the PPA price, every year's revenue, the entry capex and the
debt quantum.

``docs/decisions.md`` already records that half-up is implemented explicitly on
both sides of the wire rather than assumed. This is the server half of that.
"""

from __future__ import annotations

import math

__all__ = ["js_round", "js_round_to"]

# The tie rule itself, and the base a decimal place is a place in.
_HALF, _DECIMAL_BASE = 0.5, 10  # structural: the rounding rule, not calibration


def js_round(value: float) -> float:
    """Round half up, reproducing JavaScript's ``Math.round``.

    ``floor(value + 0.5)``, so ``0.5`` goes to ``1`` and ``-0.5`` goes to ``0``
    — halves move toward positive infinity, not away from zero. Every value this
    is applied to in the model is positive, so the negative branch is a property
    of the rule rather than a case the pipeline exercises.
    """
    return math.floor(value + _HALF)


def js_round_to(value: float, places: int) -> float:
    """Round half up to ``places`` decimal places.

    The reference spells this ``Math.round(x * 100) / 100``; shifting by a power
    of ten and back is what it does, including the representation error that
    comes with it, so this shifts the same way rather than reaching for
    :mod:`decimal` and landing on a different number. ``2.675`` rounds to
    ``2.68`` and ``1.005`` to ``1.0`` — not because the tie rule went one way or
    the other, but because ``2.675 * 100`` is exactly ``267.5`` in binary64
    while ``1.005 * 100`` is ``100.49999999999999``. Verified against ``node``
    on both, and on ``0.145``, which shifts to just under the half.
    """
    if places < 0:
        raise ValueError(f"places must not be negative, got {places}")
    scale: int = _DECIMAL_BASE**places
    return js_round(value * scale) / scale
