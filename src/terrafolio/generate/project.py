"""Draw one project's economics, in the reference's order.

Nine draws, seven to nine of them taken -- see :mod:`terrafolio.generate.draws`
for the plan and why the order is load-bearing. This module turns a
:class:`~terrafolio.generate.sites.Site` and a stream into everything a project
file declares: resource, cost, contract terms, risk, capital structure.

Every parameter comes from the assumption set. The formulas are the reference's,
with their bracketing preserved, because the same code has to reproduce the
48-project corpus exactly and produce a fresh 300-project pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

from terrafolio.config.assumptions import AssumptionSet, Range
from terrafolio.domain.conventions import EUR_PER_EUR_MILLION, KW_PER_MW
from terrafolio.domain.enums import Stage, Technology
from terrafolio.generate.draws import DrawSource
from terrafolio.generate.sites import Site, market_key
from terrafolio.model.project import (
    DebtTerms,
    ProjectInputs,
    entry_capex_per_kw,
    size_senior_debt,
    stabilised_ebitda,
)
from terrafolio.model.rounding import js_round, js_round_to

__all__ = ["DrawnProject", "draw_project"]

_SHARE_PLACES, _RISK_PLACES = 2, 1  # structural: quoted precision, not calibration


def _between(draw: float, span: Range) -> float:
    return span.low + draw * span.span


@dataclass(frozen=True, slots=True, kw_only=True)
class DrawnProject:
    """One project, drawn and priced, before its statements are built."""

    site: Site
    net_capacity_factor: float
    opex_per_kw_year: float
    development_risk_score: float
    grid_secured: bool
    om_contracted: bool
    ppa_share: float
    ppa_price: float
    ppa_tenor_years: int
    capture_price: float
    capture_factor_nominal: float
    baseload_price: float
    entry_yield: float
    capex_per_kw: float
    capex_clamp: str
    total_capex: float
    senior_debt: float
    debt_basis: str
    max_gearing: float
    draws_consumed: int

    @property
    def capture_factor_effective(self) -> float:
        """What the file carries, so §4.3's identity reproduces the revenue (1C-17).

        The capture price is rounded to a whole EUR/MWh and every year's revenue
        is built on the rounded figure, so emitting the nominal factor would
        leave ``capturePrice = baseload x captureFactor`` false by over 1%%  --
        outside the tie-out tolerance, and a number that does not reproduce the
        revenue sitting next to it.
        """
        return self.capture_price / self.baseload_price


def draw_project(site: Site, draw: DrawSource, assumptions: AssumptionSet) -> DrawnProject:
    """Draw and price one project. Consumes seven, eight or nine draws."""
    generator = assumptions.generator
    market = market_key(site.country_code)
    offshore = site.technology is Technology.OFFSHORE_WIND
    taken = 0

    def next_draw() -> float:
        nonlocal taken
        taken += 1
        return draw()

    # 1. Capacity factor. Offshore has no market curve -- it is drawn outright.
    if offshore:
        capacity_factor = _between(next_draw(), generator.offshore_capacity_factor)
    else:
        base = generator.capacity_factor[site.technology][market]
        capacity_factor = base * _between(next_draw(), generator.capacity_factor_jitter)

    # 2. Opex, from the technology's own range.
    opex_per_kw = _between(next_draw(), generator.opex_per_kw_year[site.technology])

    # 3. Development risk: a stage base, an offshore premium, jitter, then the
    #    1-5 scale it is quoted on. Rounded first, clamped second, as §4.4 states
    #    the field to one decimal within that range.
    risk = generator.development_risk_base[site.stage]
    if offshore:
        risk += generator.development_risk_offshore_premium
    risk += next_draw() * generator.development_risk_jitter
    development_risk = min(
        generator.development_risk.high,
        max(generator.development_risk.low, js_round_to(risk, _RISK_PLACES)),
    )

    # 4. Grid connection: secured by definition once past greenfield.
    grid_secured = True
    if site.stage is Stage.GREENFIELD:
        grid_secured = next_draw() > 1 - generator.grid_secured_greenfield_probability

    # 5. O&M: contracted by definition once under construction.
    om_contracted = True
    if site.stage is not Stage.CONSTRUCTION:
        om_contracted = next_draw() > 1 - generator.om_contracted_probability

    # 6. Contracted share, quoted to two decimals.
    ppa_share = js_round_to(
        _between(next_draw(), generator.contracted_share[site.stage]), _SHARE_PLACES
    )

    # 7. Contracted price, off the rounded capture price.
    capture_factor = generator.capture_factor[site.technology]
    baseload = generator.baseload_price[market]
    capture_price = js_round(baseload * capture_factor)
    ppa_price = js_round(capture_price * _between(next_draw(), generator.contract_price_factor))

    # 8. Tenor. A share below the floor is fully merchant and draws nothing,
    #    which §4.3 requires: a zero tenor cannot carry a contracted share.
    ppa_tenor = 0
    if ppa_share > generator.contracted_share_floor:
        choices = generator.contract_tenor_choices
        ppa_tenor = choices[int(next_draw() * len(choices))]
    else:
        ppa_share = 0.0

    # 9. Entry yield: the stage figure, offshore overriding it, jittered.
    base_yield = (
        generator.entry_yield_offshore_override if offshore else generator.entry_yield[site.stage]
    )
    entry_yield = base_yield + _between(next_draw(), generator.entry_yield_jitter)

    # Priced from the revenue case (§9.1), then financed (§9.3, D-3). Neither
    # consumes a draw; both follow from what was drawn.
    probe = _probe(
        site,
        assumptions,
        net_capacity_factor=capacity_factor,
        opex_per_kw_year=opex_per_kw,
        ppa_share=ppa_share,
        ppa_price=ppa_price,
        ppa_tenor_years=ppa_tenor,
        capture_price=capture_price,
    )
    capex_per_kw, capex_clamp, total_capex, senior_debt, debt_basis, max_gearing = _price(
        site, assumptions, probe, entry_yield
    )

    return DrawnProject(
        site=site,
        net_capacity_factor=capacity_factor,
        opex_per_kw_year=opex_per_kw,
        development_risk_score=development_risk,
        grid_secured=grid_secured,
        om_contracted=om_contracted,
        ppa_share=ppa_share,
        ppa_price=ppa_price,
        ppa_tenor_years=ppa_tenor,
        capture_price=capture_price,
        capture_factor_nominal=capture_factor,
        baseload_price=baseload,
        entry_yield=entry_yield,
        capex_per_kw=capex_per_kw,
        capex_clamp=capex_clamp,
        total_capex=total_capex,
        senior_debt=senior_debt,
        debt_basis=debt_basis,
        max_gearing=max_gearing,
        draws_consumed=taken,
    )


def _probe(site: Site, assumptions: AssumptionSet, **drawn: float | int) -> ProjectInputs:
    """The model's inputs for a project whose capital structure is not yet known.

    Entry pricing needs a stabilised EBITDA, and stabilised EBITDA needs none of
    ``totalCapex``, ``seniorDebt`` or anything derived from them -- it is revenue
    less opex in a full year. The placeholder capital structure here is therefore
    inert, and is replaced by :func:`_price`'s own answer.
    """
    generator = assumptions.generator
    return ProjectInputs(
        capacity_mw=site.capacity_mw,
        cod_year=site.cod_year,
        base_year=generator.base_year,
        total_capex=1.0,
        senior_debt=0.0,
        tax_rate=generator.tax_rate,
        depreciation_years=generator.depreciation_years,
        debt_rate=generator.debt_rate,
        debt_tenor_years=generator.debt_tenor_years,
        degradation_rate=generator.degradation[site.technology],
        price_escalation=generator.price_escalation,
        merchant_escalation=generator.merchant_escalation,
        opex_escalation=generator.opex_escalation,
        ramp_factor=generator.ramp_factor,
        hours_per_year_gwh=generator.hours_per_year_gwh,
        **drawn,  # type: ignore[arg-type]
    )


def _price(
    site: Site, assumptions: AssumptionSet, probe: ProjectInputs, entry_yield: float
) -> tuple[float, str, float, float, str, float]:
    """Entry pricing then debt sizing, both off the same stabilised EBITDA.

    Capex is **recomputed from the clamped** EUR/kW rather than carried from the
    unclamped one -- which is the whole point of the clamp, and what lets a
    genuinely uneconomic asset exist in the pipeline for the screens to reject.
    """
    generator = assumptions.generator
    validation = assumptions.validation
    stabilised = stabilised_ebitda(probe)
    band = validation.capex_per_kw[site.technology]
    capex_per_kw, clamp = entry_capex_per_kw(
        stabilised, site.capacity_mw, entry_yield, band.low, band.high
    )
    total_capex = site.capacity_mw * KW_PER_MW * capex_per_kw / EUR_PER_EUR_MILLION
    max_gearing = validation.stage_gearing_ceiling[site.stage]
    terms = DebtTerms(
        target_dscr=generator.target_dscr,
        rate=generator.debt_rate,
        tenor=generator.debt_tenor_years,
        max_gearing=max_gearing,
    )
    senior_debt, basis = size_senior_debt(stabilised, terms, total_capex)
    return capex_per_kw, clamp, total_capex, senior_debt, basis, max_gearing
