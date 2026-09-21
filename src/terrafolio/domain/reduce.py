"""The one place a :class:`Mandate` becomes plain scalars.

Named and written now, although its only caller arrives with issue 2A, because
the alternative is three issues each writing their own conversion — which is
exactly how a fraction gets multiplied by a hundred twice.
"""

from __future__ import annotations

from terrafolio.domain.conventions import EUR_PER_EUR_MILLION
from terrafolio.domain.mandate import Mandate
from terrafolio.domain.scalars import MandateScalars

__all__ = ["mandate_to_scalars"]


def mandate_to_scalars(mandate: Mandate) -> MandateScalars:
    """Reduce a validated mandate to what the numeric core consumes.

    Money crosses from €m to euros here, and only here on this side of the
    system: the loader does the same for project files, and the API converts
    back on the way out (epic §5). Every other field passes through unchanged.
    """
    return MandateScalars(
        available_capital_eur=mandate.available_capital_m * EUR_PER_EUR_MILLION,
        capacity_target_mw=mandate.capacity_target_mw,
        solar_share=mandate.solar_share,
        target_irr=mandate.target_irr,
        hold_years=mandate.hold_years,
        countries=mandate.countries,
        stages=mandate.stages,
        min_leverage=mandate.min_leverage,
        min_dscr=mandate.min_dscr,
        max_merchant_share=mandate.max_merchant_share,
        max_country_share=mandate.max_country_share,
        max_project_share=mandate.max_project_share,
        cod_from=mandate.cod_from,
        cod_to=mandate.cod_to,
        risk_appetite=mandate.risk_appetite,
        grid_secured_only=mandate.grid_secured_only,
        eur_revenue_only=mandate.eur_revenue_only,
        om_contracted_only=mandate.om_contracted_only,
    )
