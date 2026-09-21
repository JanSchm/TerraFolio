"""The nineteen mandates ``objective_cases.json`` references but does not carry.

Each case names a ``mandateId`` and embeds its own screened pool, but not the mandate
itself; the parameters live in ``tools/extract_reference.mjs`` (``mandateVariants``,
and the two boundary variants built just below it). They are transcribed here rather
than parsed out of the JavaScript, so a change on either side shows up as a failing
test rather than as a silently different mandate.

**Units change on the way in.** The reference mixes whole percent with fractions in
one object and divides at each comparison — ``hurdle: 11`` beside ``solarShare: 0.45``
— which is how a factor-of-100 bug gets written, and decision A-17 records that one
did. :class:`MandateScalars` is fractions and euros throughout, so the conversion
happens once, here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from terrafolio.domain.conventions import EUR_PER_EUR_MILLION
from terrafolio.domain.enums import RiskAppetite, Stage
from terrafolio.domain.scalars import MandateScalars

__all__ = ["BASE_MANDATE", "REFERENCE_MANDATES", "as_scalars"]

FIXTURES = Path(__file__).resolve().parent / "fixtures"

BASE_MANDATE: dict[str, Any] = json.loads(
    (FIXTURES / "ga_trace_fast_seed42.json").read_text(encoding="utf-8")
)["mandate"]
"""``M0-default`` in full — the reference's own default, which the GA trace records."""

BOUNDARY_EQUITY_M = 278.6990299116662
"""The equity of the handcrafted boundary chromosome, in €m, from ``OBJ-204``."""

_STAGES = {
    "Greenfield": Stage.GREENFIELD,
    "Ready-to-build": Stage.READY_TO_BUILD,
    "Construction": Stage.CONSTRUCTION,
}
_RISK = {"Low": RiskAppetite.LOW, "Balanced": RiskAppetite.BALANCED, "High": RiskAppetite.HIGH}


def as_scalars(**overrides: Any) -> MandateScalars:
    """The base mandate with ``overrides`` applied, in the core's units."""
    mandate = {**BASE_MANDATE, **overrides}
    return MandateScalars(
        available_capital_eur=mandate["capital"] * EUR_PER_EUR_MILLION,
        capacity_target_mw=float(mandate["target"]),
        solar_share=mandate["solarShare"],
        target_irr=mandate["hurdle"] / 100,
        hold_years=mandate["hold"],
        countries=tuple(mandate["countries"]),
        stages=tuple(_STAGES[stage] for stage in mandate["stages"]),
        min_leverage=mandate["minLev"] / 100,
        min_dscr=mandate["minDscr"],
        max_merchant_share=mandate["maxMerchant"] / 100,
        max_country_share=mandate["maxCountry"] / 100,
        max_project_share=mandate["maxProject"] / 100,
        cod_from=mandate["codFrom"],
        cod_to=mandate["codTo"],
        risk_appetite=_RISK[mandate["risk"]],
        grid_secured_only=mandate["gridOnly"],
        eur_revenue_only=mandate["hedged"],
        om_contracted_only=mandate["omOnly"],
    )


REFERENCE_MANDATES: dict[str, MandateScalars] = {
    "M0-default": as_scalars(),
    "M1-leverage-floor": as_scalars(minLev=85),
    "M2-merchant-cap": as_scalars(maxMerchant=8),
    "M3-risk-low": as_scalars(risk="Low"),
    "M4-country-cap": as_scalars(maxCountry=8),
    "M5-project-cap": as_scalars(maxProject=3),
    "M6-tight-capital": as_scalars(capital=250),
    "M7-screened-pool": as_scalars(
        countries=["ES", "PT", "IT", "GR"],
        stages=["Ready-to-build", "Construction"],
        codFrom=2027,
        codTo=2029,
        gridOnly=True,
    ),
    "M8-short-hold": as_scalars(hold=5),
    "M9-rail-capacity-high": as_scalars(target=200),
    "M10-rail-tech-mix": as_scalars(solarShare=0),
    "M11-rail-returns-low": as_scalars(hurdle=40),
    "M12-rail-returns-high": as_scalars(hurdle=0),
    # The exclusion list is a run control, not a mandate field, so it changes the pool
    # the case embeds rather than anything the objective reads.
    "M13-excluded": as_scalars(),
    "M14-hedged-only": as_scalars(hedged=True),
    "M15-tight-dscr": as_scalars(minDscr=1.6),
    "M16-om-and-grid": as_scalars(omOnly=True, gridOnly=True),
    "M13-on-the-cap": as_scalars(capital=BOUNDARY_EQUITY_M),
    "M14-one-ulp-under": as_scalars(capital=float(np.nextafter(BOUNDARY_EQUITY_M, -np.inf))),
}
