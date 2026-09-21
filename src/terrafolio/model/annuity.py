"""Level-annuity debt sizing and its amortisation schedule.

Senior debt is a level annuity: the same total service every year, split between
interest on the opening balance and whatever principal the payment leaves over.
"Sculpted" in spec §9.3 means the debt is **sized** so a target cover ratio is
met, not that the service is shaped — a level payment against a varying EBITDA
profile gives a varying DSCR by definition, which is the whole point of epic
§6.3 and what keeps the mandate's DSCR floor a live control.

Two factors, reciprocal and both wanted:

``annuity_pv_factor``
    ``(1 - (1+r)^-n) / r`` — the present value of one unit paid annually for
    ``n`` years. This is what *sizes* the debt: a project that can service
    ``x`` per year at the target cover can borrow ``x x factor``.

``annuity_payment_factor``
    ``r / (1 - (1+r)^-n)`` — the annual payment per unit borrowed. This is what
    *services* it.

They are exact reciprocals in real arithmetic and near-reciprocals in floating
point, so both are computed from the definition rather than one from the other:
the JavaScript reference writes the payment factor, and ``debt x payment_factor``
is not always the last bit of ``debt ÷ pv_factor``.

**Two schedules, deliberately.** :func:`schedule` is the recurrence the reference
runs and the one the seed pipeline is emitted from; :func:`closed_form_schedule`
is the vectorised form, with no loop. They agree to about 6e-14 — far inside the
€0.01m tie-out tolerance — but *not* bit for bit, and the committed fixtures were
produced by the recurrence. Measured over all 48 golden files and both series,
1,440 cells each: the recurrence reproduces 1,440 of 1,440 exactly, the closed
form 680. Emitting from the closed form would therefore fail a field-for-field
parity check on numbers that are equally correct, so the recurrence emits and the
closed form is held to it as a cross-check.
"""

from __future__ import annotations

from typing import Final, NamedTuple

import numpy as np
import numpy.typing as npt

__all__ = [
    "Schedule",
    "annuity_payment_factor",
    "annuity_pv_factor",
    "closed_form_schedule",
    "schedule",
]

_F64: Final = np.float64


class Schedule(NamedTuple):
    """One debt's amortisation, indexed by period from the first payment.

    Every series is in the same money unit as the ``debt`` it was built from,
    and every one is a positive magnitude: ``opening`` and ``closing`` are
    balances, ``interest`` and ``principal`` are flows.
    """

    opening: npt.NDArray[np.float64]
    interest: npt.NDArray[np.float64]
    principal: npt.NDArray[np.float64]
    closing: npt.NDArray[np.float64]


def _discount(rate: float, periods: int) -> float:
    """``(1 + r)^-n``, the factor both annuity factors are built on."""
    return float((1 + rate) ** -periods)


def annuity_pv_factor(rate: float, periods: int) -> float:
    """Present value of one unit paid annually for ``periods`` years.

    ``annuity_pv_factor(0.055, 18) == 11.246074465287…`` — the figure issue #8
    pins. Sizing debt off a cover ratio multiplies by this factor; note that
    issue #8's prose divides by it, which is a slip, since dividing by 11.246
    rather than multiplying puts the template project's senior debt at €0.70m
    against its actual €74.39m. See ``docs/decisions.md``.

    At a zero rate the limit is ``periods``, computed as such rather than
    dividing by zero.
    """
    if periods < 0:
        raise ValueError(f"periods must not be negative, got {periods}")
    if rate == 0:
        return float(periods)
    return (1 - _discount(rate, periods)) / rate


def annuity_payment_factor(rate: float, periods: int) -> float:
    """Annual payment per unit borrowed — the reciprocal of the PV factor.

    Written from the definition, exactly as the JavaScript reference writes it,
    rather than as ``1 / annuity_pv_factor(...)``: the service schedule is
    reproduced bit for bit against the golden fixtures, and one reciprocal
    round-trip is enough to lose the last bit of every year of it.
    """
    if periods <= 0:
        raise ValueError(f"periods must be positive, got {periods}")
    if rate == 0:
        return 1 / periods
    return rate / (1 - _discount(rate, periods))


def schedule(debt: float, rate: float, periods: int) -> Schedule:
    """Amortise ``debt`` by the recurrence, as the reference does.

    Interest accrues on the **opening** balance and the balance is reduced after
    that year's interest is taken, so the first period carries a full year's
    interest on the whole facility. Both the payment and the balance are floored
    at zero, matching the reference's ``Math.max(0, …)``; on a well-formed
    annuity neither floor ever binds, and they exist so that a pathological rate
    cannot produce a negative flow.

    This is the schedule the seed pipeline is emitted from.
    """
    payment = debt * annuity_payment_factor(rate, periods)
    opening = np.zeros(periods, dtype=_F64)
    interest = np.zeros(periods, dtype=_F64)
    principal = np.zeros(periods, dtype=_F64)
    closing = np.zeros(periods, dtype=_F64)

    outstanding = debt
    for period in range(periods):
        accrued = outstanding * rate
        repaid = max(0.0, payment - accrued)
        opening[period] = outstanding
        interest[period] = accrued
        principal[period] = repaid
        outstanding = max(0.0, outstanding - repaid)
        closing[period] = outstanding
    return Schedule(opening, interest, principal, closing)


def closed_form_schedule(debt: float, rate: float, periods: int) -> Schedule:
    """The same amortisation with no loop, as issue #8 specifies.

    ``principal_k = payment x (1+r)^(k-n-1)`` and
    ``opening_k = debt x (1 - (1+r)^-(n-k+1)) / (1 - (1+r)^-n)``, for
    ``k = 1…n``. Both follow from the annuity identity, and summing the
    principals telescopes back to ``debt`` exactly.

    Held to :func:`schedule` by test rather than used in its place — see the
    module docstring for why.
    """
    payment = debt * annuity_payment_factor(rate, periods)
    period = np.arange(periods, dtype=_F64)
    remaining = periods - period

    if rate == 0:
        principal = np.full(periods, debt / periods, dtype=_F64)
        opening = debt * remaining / periods
        interest = np.zeros(periods, dtype=_F64)
        return Schedule(opening, interest, principal, opening - principal)

    growth = 1 + rate
    principal = payment * growth ** (period - periods)
    span = 1 - growth**-periods
    opening = debt * (1 - growth**-remaining) / span
    closing = debt * (1 - growth ** -(remaining - 1)) / span
    return Schedule(opening, opening * rate, principal, closing)
