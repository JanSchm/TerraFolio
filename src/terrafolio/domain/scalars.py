"""The mandate as plain scalars, for the numeric core.

The core — ``economics``, ``model``, ``optimiser`` — takes numpy arrays, an
:class:`~terrafolio.config.assumptions.AssumptionSet` and plain scalars. It never
sees a pydantic model, which is what keeps it testable at 1e-12 and cheap to
start in a worker process.

This module is the plain-scalar half of that contract: standard library only, so
the core can import it. :func:`terrafolio.domain.reduce.mandate_to_scalars` is
the other half, and it is the **only** place a :class:`Mandate` becomes one of
these.
"""

from __future__ import annotations

from dataclasses import dataclass

from terrafolio.domain.enums import RiskAppetite, Stage

__all__ = ["MandateScalars"]


@dataclass(frozen=True, slots=True, kw_only=True)
class MandateScalars:
    """A mandate in the core's units: **euros**, fractions and years.

    The only unit that changes from :class:`~terrafolio.domain.mandate.Mandate`
    is money — ``availableCapital_m`` in €m becomes ``available_capital_eur``.
    Shares and rates are already fractions on the wire, so they pass through
    untouched; adding a percentage conversion here would be a third unit
    boundary in a system that permits two.
    """

    available_capital_eur: float
    capacity_target_mw: float
    solar_share: float
    target_irr: float
    hold_years: int

    countries: tuple[str, ...]
    stages: tuple[Stage, ...]
    min_leverage: float
    min_dscr: float
    max_merchant_share: float
    max_country_share: float
    max_project_share: float
    cod_from: int
    cod_to: int

    risk_appetite: RiskAppetite
    grid_secured_only: bool
    eur_revenue_only: bool
    om_contracted_only: bool
