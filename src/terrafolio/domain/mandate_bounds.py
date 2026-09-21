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
from typing import Any, Final

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
    def multiple_of(self) -> dict[str, Any]:
        """JSON-schema metadata advertising the step to the UI.

        The step is published, never validated. §5's steps describe slider
        affordances, not properties of a mandate: the API is also a programmatic
        surface, float modulo is unreliable (``1.15 % 0.05`` is not zero), and
        re-validating a stored ``RunRecord`` against a step tightened in a later
        release would break §11's promise that a run id reopens the exact result.
        Reproducibility comes from hashing the mandate as submitted, not from
        quantising it.
        """
        # Typed loosely because pydantic wants dict[str, JsonValue] and this
        # module may not import pydantic — it is a stdlib-only leaf so that
        # config, and through it the numeric core, can depend on domain.
        return {"multipleOf": self.step}


# §5.1 Objective.
#
# Units are those `docs/api.md` §6.1 pins for the wire: shares and rates are
# fractions of one, never percentage points. Spec §5 states the same controls in
# points because that is what the sliders show; converting here rather than at
# the HTTP boundary would add a third unit boundary to a system that allows two.
AVAILABLE_CAPITAL_M: Final = Bound(200.0, 4000.0, 50.0, 1200.0, "EURm")
CAPACITY_TARGET_MW: Final = Bound(200.0, 4000.0, 50.0, 1500.0, "MW")
SOLAR_SHARE: Final = Bound(0.0, 1.0, 0.05, 0.45, "fraction")
TARGET_IRR: Final = Bound(0.06, 0.18, 0.005, 0.11, "fraction")
HOLD_YEARS: Final = Bound(5.0, 30.0, 1.0, 10.0, "years")

# §5.2 Hard constraints.
MIN_LEVERAGE: Final = Bound(0.0, 0.85, 0.01, 0.60, "fraction")
MIN_DSCR: Final = Bound(1.00, 2.00, 0.05, 1.25, "multiple")
MAX_MERCHANT_SHARE: Final = Bound(0.0, 1.0, 0.05, 0.35, "fraction")
MAX_COUNTRY_SHARE: Final = Bound(0.10, 1.0, 0.05, 0.35, "fraction")
MAX_PROJECT_SHARE: Final = Bound(0.05, 1.0, 0.05, 0.15, "fraction")
COD_FROM: Final = Bound(2027.0, 2033.0, 1.0, 2027.0, "year")
COD_TO: Final = Bound(2027.0, 2033.0, 1.0, 2032.0, "year")

ALL_BOUNDS: Final[dict[str, Bound]] = {
    "available_capital_m": AVAILABLE_CAPITAL_M,
    "capacity_target_mw": CAPACITY_TARGET_MW,
    "solar_share": SOLAR_SHARE,
    "target_irr": TARGET_IRR,
    "hold_years": HOLD_YEARS,
    "min_leverage": MIN_LEVERAGE,
    "min_dscr": MIN_DSCR,
    "max_merchant_share": MAX_MERCHANT_SHARE,
    "max_country_share": MAX_COUNTRY_SHARE,
    "max_project_share": MAX_PROJECT_SHARE,
    "cod_from": COD_FROM,
    "cod_to": COD_TO,
}
"""Every bound, keyed by the ``Mandate`` field it constrains.

The test suite asserts this covers the model exactly, so a field added without a
bound — or a bound left behind by a renamed field — fails rather than drifting.
"""
