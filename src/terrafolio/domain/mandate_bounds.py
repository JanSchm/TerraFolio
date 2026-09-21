"""The §5.1-5.3 mandate ranges, in one place.

**This is the only module exempt from the numeric-literal scan** in
``tests/unit/test_config_no_literals.py``, and the exemption list has exactly
one entry, asserted by that test. The numbers here are not calibration — they do
not move a result, they bound what a user may ask for, and they are quoted
verbatim from the specification's control tables. Everything that *does* move a
result lives in the assumption set.

Keeping them here rather than inline in ``Field(ge=..., le=...)`` means the
mandate model itself stays literal-free, and 1B can generate the ranges table in
its documentation from this module rather than transcribing it.

Steps are **not enforced**; see :class:`Bound.step`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = ["Bound"]


@dataclass(frozen=True, slots=True)
class Bound:
    """An inclusive range for one mandate control, with its UI step and default."""

    lo: float
    hi: float
    step: float
    default: float
    unit: str

    @property
    def multiple_of(self) -> dict[str, float]:
        """JSON-schema metadata advertising the step to the UI.

        The step is published, never validated. §5's steps describe slider
        affordances, not properties of a mandate: the API is also a programmatic
        surface, float modulo is unreliable (``1.15 % 0.05`` is not zero), and
        re-validating a stored ``RunRecord`` against a step tightened in a later
        release would break §11's promise that a run id reopens the exact result.
        Reproducibility comes from hashing the mandate as submitted, not from
        quantising it.
        """
        return {"multipleOf": self.step}


# §5.1 Objective.
AVAILABLE_EQUITY_M: Final = Bound(200.0, 4000.0, 50.0, 1200.0, "EURm")
CAPACITY_TARGET_MW: Final = Bound(200.0, 4000.0, 50.0, 1500.0, "MW")
SOLAR_SHARE_PCT: Final = Bound(0.0, 100.0, 5.0, 45.0, "percent")
TARGET_EQUITY_IRR_PCT: Final = Bound(6.0, 18.0, 0.5, 11.0, "percent")
HOLD_YEARS: Final = Bound(5.0, 30.0, 1.0, 10.0, "years")

# §5.2 Hard constraints.
MIN_LEVERAGE_PCT: Final = Bound(0.0, 85.0, 1.0, 60.0, "percent")
MIN_DSCR: Final = Bound(1.00, 2.00, 0.05, 1.25, "multiple")
MAX_MERCHANT_PCT: Final = Bound(0.0, 100.0, 5.0, 35.0, "percent")
MAX_COUNTRY_PCT: Final = Bound(10.0, 100.0, 5.0, 35.0, "percent")
MAX_PROJECT_PCT: Final = Bound(5.0, 100.0, 5.0, 15.0, "percent")
COD_FROM: Final = Bound(2027.0, 2033.0, 1.0, 2027.0, "year")
COD_TO: Final = Bound(2027.0, 2033.0, 1.0, 2032.0, "year")

ALL_BOUNDS: Final[dict[str, Bound]] = {
    "available_equity_m": AVAILABLE_EQUITY_M,
    "capacity_target_mw": CAPACITY_TARGET_MW,
    "solar_share_pct": SOLAR_SHARE_PCT,
    "target_equity_irr_pct": TARGET_EQUITY_IRR_PCT,
    "hold_years": HOLD_YEARS,
    "min_leverage_pct": MIN_LEVERAGE_PCT,
    "min_dscr": MIN_DSCR,
    "max_merchant_pct": MAX_MERCHANT_PCT,
    "max_country_pct": MAX_COUNTRY_PCT,
    "max_project_pct": MAX_PROJECT_PCT,
    "cod_from": COD_FROM,
    "cod_to": COD_TO,
}
"""Every bound, keyed by the ``Mandate`` field it constrains.

The test suite asserts this covers the model exactly, so a field added without a
bound — or a bound left behind by a renamed field — fails rather than drifting.
"""
