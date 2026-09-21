"""Discounting, in the one convention this system uses.

Every present value here is an **ordinary annuity**: the first cash flow lands at the
end of period one and is discounted once. That is not a free choice — it is what
reproduces the figures the rest of the project is pinned to. A flat 1 GWh a year for
thirty years against €10m of capex and no opex gives an LCOE of
``726.489114900472`` €/MWh only under this convention; discounting from period zero
gives ``685.4``. Likewise ``annuity_pv_factor(0.055, 18) = 11.246074465287313``, which
is the factor the reference's own 18-year debt annuity uses.

numpy and the standard library only: this is the numeric core.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

__all__ = ["annuity_pv_factor", "discount_factors"]


def annuity_pv_factor(rate: float, periods: int) -> float:
    """Present value of 1 per period for ``periods`` periods, at ``rate``.

    ``(1 - (1 + r)^-n) / r``, with the removable singularity at ``r = 0`` handled as
    the limit — ``n`` — rather than by dividing by zero. A zero discount rate is a
    legitimate assumption set, not an error.
    """
    if periods < 0:
        raise ValueError(f"periods must not be negative, got {periods}")
    if rate == 0.0:
        return float(periods)
    return float((1.0 - (1.0 + rate) ** -periods) / rate)


def discount_factors(rate: float, periods: int) -> NDArray[np.float64]:
    """``(periods,)`` of ``(1 + r)^-(t + 1)`` — one factor per period, ordinary.

    The ``+ 1`` is the convention above, written once here so no caller has to
    remember it. Summing the result gives :func:`annuity_pv_factor`.
    """
    if periods < 0:
        raise ValueError(f"periods must not be negative, got {periods}")
    exponents = np.arange(periods, dtype=np.float64) + 1.0
    factors: NDArray[np.float64] = (1.0 + rate) ** -exponents
    return factors
