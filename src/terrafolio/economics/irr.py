"""Equity IRR by vectorised bisection, with undefined meaning undefined.

**The null rule is part of the answer, not a failure mode.** A cash-flow series with no
sign change in the bracket has no internal rate of return, and §13 requires that to
surface as an em dash — never as ``0.0``, never as a sentinel, and never excluded
quietly from a weighted average. This module returns ``NaN`` with a companion mask; the
``NaN`` becomes ``None`` where arrays meet models, JSON ``null`` on the wire, and ``—``
on the page. Anything that "improves" the solver into always returning a number has
broken that chain.

**Horner, not powers.** ``v = v / (1 + r) + cf[:, t]`` walking ``t`` backwards is about
three times faster than ``cf / (1 + r) ** arange(n)`` and allocates nothing per year.
It puts the first element at exponent zero rather than one, which is a *different NPV*
from the reference's — by a constant positive factor — and therefore the **same root**
and the same sign changes. The bracket and the null rule are what a port has to
reproduce, not the NPV's scale.

**The bracket is wider than the reference's.** Issue #6 specifies ``[-0.9999, 10.0]``
over 100 iterations where the JavaScript reference uses ``[-0.5, 1.2]`` over 70. Wider
is the better answer: a 150% IRR is a number, not "undefined". It costs nothing against
the oracle — every one of the 192 IRR figures in ``derived_expectations.json`` lies
inside ``[-0.148, 0.165]``, so both brackets find the same root — and 100 halvings of
an interval of 11 leaves 1e-29, far below the resolution of a double, so the result is
the exactly-representable root either way.
"""

from __future__ import annotations

from typing import Final

import numpy as np
from numpy.typing import NDArray

__all__ = [
    "IRR_BISECTION_ITERATIONS",
    "IRR_BRACKET_HIGH",
    "IRR_BRACKET_LOW",
    "irr",
    "npv",
]

Vector = NDArray[np.float64]
Matrix = NDArray[np.float64]
BoolVector = NDArray[np.bool_]

IRR_BRACKET_LOW: Final = -0.9999  # structural: bisection bracket, not a calibration
"""Just above total loss. At exactly ``-1`` the discount factor is a division by zero,
so the bracket stops short of it rather than at a rate anyone chose."""

IRR_BRACKET_HIGH: Final = 10.0  # structural: bisection bracket, not a calibration
"""1000% a year. Any equity IRR a renewables portfolio can plausibly show is far
inside this; the point of the bound is to be past the plausible, not near it."""

IRR_BISECTION_ITERATIONS: Final = 100  # structural: iteration count, not a calibration
"""Enough halvings to exhaust a double across the bracket above — 11 / 2**100 is
1e-29 — so the answer is the root, not an approximation to it."""


def npv(rate: Vector, cashflows: Matrix) -> Vector:
    """Net present value of each row at its own rate, in Horner form.

    ``rate`` is ``(n,)`` and ``cashflows`` is ``(n, t)``; the first column is
    undiscounted. Walking backwards means one multiply and one add per year, with no
    intermediate power series allocated.
    """
    discount = 1.0 + rate
    value = np.zeros_like(rate)
    for period in range(cashflows.shape[-1] - 1, -1, -1):
        value = value / discount + cashflows[:, period]
    return value


def irr(cashflows: Matrix) -> tuple[Vector, BoolVector]:
    """Internal rate of return per row, and the mask of rows where one exists.

    Returns ``(rates, defined)``. ``rates`` is ``NaN`` wherever ``defined`` is false —
    the two are redundant on purpose, so a caller that forgets the mask still gets a
    ``NaN`` that propagates loudly rather than a zero that does not.

    A row is undefined when the NPV does not change sign across the bracket — the
    reference's rule, and the honest one: an all-negative series never breaks even, so
    there is no rate at which it does.

    An **all-zero** row is undefined too, and that is a deliberate departure. Its NPV
    is zero at every rate, so the sign test passes vacuously and the reference returns
    the bottom of its bracket — a number that means nothing. A project with no cash
    flows has no rate of return, and #6 requires it to come back ``NaN`` alongside the
    all-negative case.
    """
    rows = cashflows.shape[0]
    low = np.full(rows, IRR_BRACKET_LOW, dtype=np.float64)
    high = np.full(rows, IRR_BRACKET_HIGH, dtype=np.float64)

    npv_low = npv(low, cashflows)
    npv_high = npv(high, cashflows)
    has_cash: BoolVector = np.any(cashflows != 0.0, axis=-1)
    defined: BoolVector = has_cash & (npv_low * npv_high <= 0.0)

    for _ in range(IRR_BISECTION_ITERATIONS):
        # Exact halving: ldexp multiplies by a power of two, so the midpoint carries
        # no rounding of its own. `(low + high) / 2` would need a literal 2, which
        # the numeric core does not allow, and would not be more accurate.
        middle = np.ldexp(low + high, -1)
        npv_middle = npv(middle, cashflows)
        root_is_below = npv_low * npv_middle <= 0.0
        high = np.where(root_is_below, middle, high)
        low = np.where(root_is_below, low, middle)
        npv_low = np.where(root_is_below, npv_low, npv_middle)

    rates = np.ldexp(low + high, -1)
    return np.where(defined, rates, np.nan), defined
