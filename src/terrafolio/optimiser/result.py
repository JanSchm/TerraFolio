"""The winner, turned into what an investment committee reads.

**Euros, and plain dataclasses.** ``domain/results.py``'s ``PortfolioAggregates`` and
``Holding`` are pydantic and denominated in €m, and the numeric core may not import
pydantic — so this module emits frozen dataclasses in euros whose field names mirror
those models one for one, minus the ``_m`` suffix. 3A's ``runner`` maps them and
crosses the unit boundary; 2B persists the same shape. A test in
``tests/unit/test_optimiser_result.py`` asserts the two field sets line up, so the
hand-off is a failing test rather than a convention.

**The pooled IRR is not the blended one.** §10.3 has the search use a cheap
equity-weighted blend of per-project rates, because solving the true rate inside the
fitness loop is about three orders of magnitude more expensive. The headline tile shows
the **pooled** rate, solved once here on the portfolio's own aggregated cash flow. The
two differ, and both are reported so nobody has to guess which one a number is.

**The two cash-flow series, again.** ``cashflow_30y`` is thirty elements and carries no
terminal value; ``cashflow_hold`` is ``hold_years`` elements and does. Each is summed
from its own per-project series — neither is sliced from the other.

**Compliance.** Tiles whose threshold is a mandate value carry a verdict. Tiles 1 and 3
carry their *deviation* instead: ``docs/ui-contract.md`` §5.1 bands them at 8% and 8
points, and those bands live in that document rather than in the assumption set, so
banding them here would put a display constant in the numeric core. Raised on #6.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from terrafolio.config.assumptions import AssumptionSet
from terrafolio.domain.enums import Stage, Technology
from terrafolio.domain.scalars import MandateScalars
from terrafolio.economics.irr import irr
from terrafolio.economics.lcoe import lcoe
from terrafolio.economics.returns import ProjectReturns, moic
from terrafolio.optimiser.aggregate import aggregate
from terrafolio.optimiser.features import Features
from terrafolio.optimiser.objective import Terms, quantise, score, score_terms
from terrafolio.pipeline.arrays import BoolVector, ProjectArrays, Vector
from terrafolio.pipeline.derive import annual_generation_gwh, min_dscr

__all__ = [
    "Compliance",
    "Holding",
    "PortfolioTotals",
    "RunResult",
    "SelectionOutcome",
    "build_result",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class SelectionOutcome:
    """What one search produced, before it is turned into tiles and rows.

    ``eligible`` and ``locked`` are indexed against the **whole pipeline**; ``winner``
    is indexed against the eligible rows, because that is what a chromosome is. Keeping
    the two index spaces in one record is what stops them being mixed up at a call
    site — the scatter happens once, in :func:`build_result`.
    """

    eligible: BoolVector
    winner: BoolVector
    locked: BoolVector | None
    returns: ProjectReturns
    contracted_share: Vector


class Compliance(StrEnum):
    """§7.1's tone, and §12's rule that colour is never the only channel.

    ``NEUTRAL`` is not "we did not check" — it is "no mandate target applies to this
    tile", which is a different statement and the one ``ui-contract.md`` §5.1 makes.
    """

    COMPLIANT = "compliant"
    ALERT = "alert"
    NEUTRAL = "neutral"


def _verdict(holds: bool) -> Compliance:
    return Compliance.COMPLIANT if holds else Compliance.ALERT


def _optional(value: float) -> float | None:
    """``NaN`` becomes ``None`` here, where arrays stop and records begin (C-12)."""
    return None if np.isnan(value) else float(value)


@dataclass(frozen=True, slots=True, kw_only=True)
class Holding:
    """One candidate in the run's eligible set — selected or not (A-14).

    ``holdings`` carries the **whole** eligible set rather than only the winners,
    because §7.4's *Show all candidates* has to say what the run rejected and §11
    requires a stored run to reopen without the live pipeline. Money is in euros.
    """

    id: str
    selected: bool
    locked: bool

    technology: Technology
    stage: Stage
    country_code: str
    cod_year: int
    capacity_mw: float
    annual_generation_gwh: float
    development_risk_score: float

    total_capex: float
    senior_debt: float
    equity: float
    gearing: float
    max_gearing: float
    capex_per_kw: float
    debt_rate: float
    debt_tenor_years: int
    opex_per_kw_year: float

    ppa_share: float
    ppa_tenor_years: int
    ppa_price: float
    country_baseload_price: float
    capture_factor: float
    capture_price: float
    contracted_revenue_share: float
    """Revenue-weighted over life, which is **not** ``1 - ppa_share``.

    The capex-weighted merchant exposure the objective and §7.1's tile use is one
    step from ``ppa_share`` and so is not carried twice. This figure is not
    derivable from the other fields here — it needs the escalators and the
    generation series — and it has no home on the wire yet; N-1 on the epic is the
    open question about whether the split should be stored in the file instead.
    """

    lcoe: float
    min_dscr: float | None
    thirty_year_fcfe: float
    equity_irr: float | None
    moic: float | None
    payback_year: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class PortfolioTotals:
    """§7.1's twelve tiles. Euros throughout; the API converts on the way out."""

    project_count: int
    solar_count: int
    wind_count: int

    capacity_mw: float
    capacity_deviation: float
    """Signed fraction of target. ``ui-contract.md`` §5.1 bands it; this does not."""

    solar_share: float
    tech_split_deviation: float
    """Signed difference in share points, banded by the same document."""

    total_capex: float
    senior_debt: float
    equity: float
    capital_deployed: float

    gearing: float
    worst_min_dscr: float | None

    equity_irr: float | None
    """**Pooled** — solved once on the portfolio's own aggregated cash flow."""

    blended_irr: float | None
    """The cheaper equity-weighted approximation the search optimised (§10.3)."""

    moic: float | None
    weighted_lcoe: float
    annual_generation_gwh: float
    co2_avoided_kt: float
    merchant_share: float
    weighted_risk_score: float

    country_shares: dict[str, float]
    largest_country_code: str | None
    largest_country_share: float

    thirty_year_fcfe: float
    fitness: float

    capacity_compliance: Compliance
    return_compliance: Compliance
    leverage_compliance: Compliance
    merchant_compliance: Compliance
    country_compliance: Compliance


@dataclass(frozen=True, slots=True, kw_only=True)
class RunResult:
    """Everything the portfolio screen, the exports and the store need."""

    selected_ids: tuple[str, ...]
    totals: PortfolioTotals
    holdings: tuple[Holding, ...]
    terms: dict[str, float]

    cashflow_30y: Vector
    """Thirty elements, euros, **no** terminal value."""

    cashflow_hold: Vector
    """``hold_years`` elements, euros, terminal value in the final one."""

    hold_years: int


def _country_shares(
    arrays: ProjectArrays, selection: BoolVector, total_capex: float
) -> dict[str, float]:
    if total_capex <= 0.0:
        return {}
    shares: dict[str, float] = {}
    for index in np.flatnonzero(selection).tolist():
        code = arrays.location.country_codes[index]
        shares[code] = shares.get(code, 0.0) + float(arrays.capital.total_capex[index])
    return {code: shares[code] / total_capex for code in sorted(shares)}


def _worst_min_dscr(lowest: Vector, selection: BoolVector) -> float | None:
    """The portfolio's weakest coverage, ignoring projects that carry no debt.

    ``+inf`` stands in for the unlevered so the reduction never meets an all-NaN
    slice, which numpy warns about and ``filterwarnings = ["error"]`` would fail on.
    """
    considered = np.where(selection & ~np.isnan(lowest), lowest, np.inf)
    weakest = float(considered.min()) if considered.size else np.inf
    return None if np.isinf(weakest) else weakest


def _holdings(
    arrays: ProjectArrays,
    returns: ProjectReturns,
    context: _ResultContext,
) -> tuple[Holding, ...]:
    lowest = context.min_dscr
    levelised = context.lcoe
    generation = context.annual_generation
    thirty_year = arrays.statements.cash_flow.fcfe.sum(axis=-1)
    payback = returns.payback

    return tuple(
        Holding(
            id=arrays.ids[index],
            selected=bool(context.selection[index]),
            locked=bool(context.locked[index]),
            technology=arrays.asset.technologies[index],
            stage=arrays.asset.stages[index],
            country_code=arrays.location.country_codes[index],
            cod_year=int(arrays.asset.cod_year[index]),
            capacity_mw=float(arrays.asset.capacity_mw[index]),
            annual_generation_gwh=float(generation[index]),
            development_risk_score=float(arrays.execution.development_risk_score[index]),
            total_capex=float(arrays.capital.total_capex[index]),
            senior_debt=float(arrays.capital.senior_debt[index]),
            equity=float(arrays.capital.equity[index]),
            gearing=float(arrays.capital.gearing[index]),
            max_gearing=float(arrays.capital.max_gearing[index]),
            capex_per_kw=float(arrays.capex_per_kw[index]),
            debt_rate=float(arrays.assumptions.debt_rate[index]),
            debt_tenor_years=int(arrays.assumptions.debt_tenor_years[index]),
            opex_per_kw_year=float(arrays.asset.opex_per_kw_year[index]),
            ppa_share=float(arrays.revenue.ppa_share[index]),
            ppa_tenor_years=int(arrays.revenue.ppa_tenor_years[index]),
            ppa_price=float(arrays.revenue.ppa_price[index]),
            country_baseload_price=float(arrays.revenue.country_baseload_price[index]),
            capture_factor=float(arrays.revenue.capture_factor[index]),
            capture_price=float(arrays.revenue.capture_price[index]),
            contracted_revenue_share=float(context.contracted_share[index]),
            lcoe=float(levelised[index]),
            min_dscr=_optional(float(lowest[index])),
            thirty_year_fcfe=float(thirty_year[index]),
            equity_irr=_optional(float(returns.equity_irr[index])),
            moic=_optional(float(returns.moic[index])),
            payback_year=None if np.isnan(returns.payback[index]) else int(payback[index]),
        )
        for index in np.flatnonzero(context.eligible).tolist()
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class _ResultContext:
    """The per-project columns the tiles and the holdings both read, computed once."""

    eligible: BoolVector
    selection: BoolVector
    locked: BoolVector
    merchant_share: Vector
    contracted_share: Vector
    min_dscr: Vector
    lcoe: Vector
    annual_generation: Vector


def build_result(
    arrays: ProjectArrays,
    features: Features,
    mandate: MandateScalars,
    assumptions: AssumptionSet,
    outcome: SelectionOutcome,
) -> RunResult:
    """Turn the winning chromosome into the twelve tiles, the series and the rows.

    ``winner`` is indexed against the **eligible** rows, as the chromosome is; it is
    scattered back onto the whole pipeline here so every column can be read by its
    original position.
    """
    eligible, winner, returns = outcome.eligible, outcome.winner, outcome.returns
    selection = np.zeros(arrays.count, dtype=np.bool_)
    selection[np.flatnonzero(eligible)] = winner
    lock_mask = (
        outcome.locked if outcome.locked is not None else np.zeros(arrays.count, dtype=np.bool_)
    )
    if lock_mask.shape[0] != arrays.count:
        scattered = np.zeros(arrays.count, dtype=np.bool_)
        scattered[np.flatnonzero(eligible)] = lock_mask
        lock_mask = scattered

    merchant_share = 1.0 - arrays.revenue.ppa_share
    context = _ResultContext(
        eligible=eligible,
        selection=selection,
        locked=lock_mask,
        merchant_share=merchant_share,
        contracted_share=outcome.contracted_share,
        min_dscr=min_dscr(arrays),
        lcoe=lcoe(arrays, assumptions),
        annual_generation=annual_generation_gwh(arrays),
    )

    totals_row = aggregate(features, winner[None, :].astype(np.float64))
    terms: Terms = score_terms(totals_row, mandate, assumptions)
    fitness = float(quantise(score(totals_row, mandate, assumptions), assumptions)[0])

    cashflow_30y: Vector = arrays.statements.cash_flow.fcfe[selection].sum(axis=0)
    cashflow_hold: Vector = returns.series[selection].sum(axis=0)

    pooled_rate, pooled_defined = irr(cashflow_hold[None, :])
    pooled_moic = moic(cashflow_hold[None, :])

    capex = float(totals_row.total_capex[0])
    capacity = float(totals_row.capacity_mw[0])
    generation = float(context.annual_generation[selection].sum())
    shares = _country_shares(arrays, selection, capex)
    largest = max(shares, key=lambda code: (shares[code], code)) if shares else None

    weighted_lcoe = (
        float((context.lcoe[selection] * context.annual_generation[selection]).sum() / generation)
        if generation > 0.0
        else 0.0
    )
    blended = float(totals_row.blended_irr[0])
    gearing = float(totals_row.gearing[0])
    merchant = float(totals_row.merchant_share[0])
    largest_share = shares[largest] if largest is not None else 0.0
    pooled = _optional(float(pooled_rate[0])) if pooled_defined[0] else None

    totals = PortfolioTotals(
        project_count=int(selection.sum()),
        solar_count=int((selection & arrays.is_solar).sum()),
        wind_count=int((selection & arrays.is_wind).sum()),
        capacity_mw=capacity,
        capacity_deviation=(capacity - mandate.capacity_target_mw) / mandate.capacity_target_mw,
        solar_share=float(totals_row.solar_share[0]),
        tech_split_deviation=float(totals_row.solar_share[0]) - mandate.solar_share,
        total_capex=capex,
        senior_debt=float(totals_row.senior_debt[0]),
        equity=float(totals_row.equity[0]),
        capital_deployed=float(totals_row.equity[0]) / mandate.available_capital_eur,
        gearing=gearing,
        worst_min_dscr=_worst_min_dscr(context.min_dscr, selection),
        equity_irr=pooled,
        blended_irr=_optional(blended),
        moic=_optional(float(pooled_moic[0])),
        weighted_lcoe=weighted_lcoe,
        annual_generation_gwh=generation,
        # GWh to MWh is x1000 and tonnes to kilotonnes is /1000, so they cancel.
        co2_avoided_kt=generation * assumptions.co2_t_per_mwh,
        merchant_share=merchant,
        weighted_risk_score=float(totals_row.weighted_risk[0]),
        country_shares=shares,
        largest_country_code=largest,
        largest_country_share=largest_share,
        thirty_year_fcfe=float(cashflow_30y.sum()),
        fitness=fitness,
        # Tiles 1 and 3 carry deviations instead; their bands are ui-contract's.
        capacity_compliance=Compliance.NEUTRAL,
        return_compliance=Compliance.NEUTRAL
        if pooled is None
        else _verdict(pooled >= mandate.target_irr),
        leverage_compliance=_verdict(gearing >= mandate.min_leverage),
        merchant_compliance=_verdict(merchant <= mandate.max_merchant_share),
        country_compliance=_verdict(largest_share <= mandate.max_country_share),
    )

    return RunResult(
        selected_ids=tuple(arrays.ids[index] for index in np.flatnonzero(selection).tolist()),
        totals=totals,
        holdings=_holdings(arrays, returns, context),
        terms={name: float(value[0]) for name, value in terms.contributions.items()},
        cashflow_30y=cashflow_30y,
        cashflow_hold=cashflow_hold,
        hold_years=returns.hold_years,
    )
