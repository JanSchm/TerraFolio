"""The project financial model — spec §9, as the JavaScript reference computes it.

This is the house model. It is **not** the application's source of truth: each
project file is (epic §2). The model exists to do two jobs, and both are checks
on files rather than substitutes for them — it **generates** the seed pipeline so
the application ships with internally consistent data, and it **re-derives** an
ingested file's statements so the variance report can say "our model says X,
your file says Y" without ever gating on the answer.

Everything here is plain scalars and numpy arrays: no pydantic, no file I/O, no
assumption set beyond the values handed in. That is what
``tests/unit/test_import_boundaries.py`` enforces for the numeric core, and it is
why the same code can serve the generator and the variance report.

**Units.** Money in €m and energy in GWh, because that is what a project *file*
carries (``docs/pipeline-schema.md`` §2); the conversion to euros happens once,
in 2A's loader. The two unit factors that appear inside a year — MWh per GWh
against a €/MWh price, kW per MW against a €/kW opex — are definitional, not
calibration.

**Three details that decide whether the numbers reproduce.**

*Stabilised EBITDA is synthetic.* The figure that prices the asset and sizes its
debt is a first-full-year EBITDA with degradation and escalation each applied
**once** and no ramp. It is not ``ebitda[1]`` from the array below, and the two
differ. Epic §6.3 fixes this basis, and it is what gives min DSCR a two-sided
distribution around the sizing target instead of pinning it there.

*Interest in the COD year accrues on the whole facility.* Debt is drawn at COD,
and the reference charges a full year's interest on it against a ramp year at
55% of full output. That is why every project has exactly one negative post-COD
FCFE year, at age 0, and why ``ratios.dscr`` is reported — and well below 1.0 —
in the ramp year while the *minimum* excludes it.

*Depreciation and debt run on age, not on the array index.* A 2033 COD with a
25-year life runs past the 30-year window, which is exactly why the depreciation
tie-out is stated as a PP&E residual (A-3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, NamedTuple

import numpy as np
import numpy.typing as npt

from terrafolio.domain.conventions import EUR_PER_EUR_MILLION, MWH_PER_GWH, YEARS
from terrafolio.model.annuity import Schedule, annuity_pv_factor, schedule

__all__ = [
    "DebtTerms",
    "ProjectInputs",
    "ProjectStatements",
    "entry_capex_per_kw",
    "min_dscr",
    "project_statements",
    "size_senior_debt",
    "stabilised_ebitda",
]

_KW_PER_MW: Final = 1_000  # structural: unit definition, not a calibration value
_F64: Final = np.float64


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectInputs:
    """Everything the model needs about one project, in file units.

    Every field is either declared in a project file or read from the assumption
    set's generator block. Nothing mandate-dependent appears: the hold period,
    the exit multiple and the discount rate belong to the consumer, not here.

    ``capture_price`` is the **realised** capture price in €/MWh, already rounded
    to a whole euro as the reference rounds it. It is passed rather than derived
    from a baseload and a factor because that rounding is what the emitted
    ``revenue.captureFactor`` has to be consistent with (1C-17).
    """

    capacity_mw: float
    cod_year: int
    base_year: int
    net_capacity_factor: float
    opex_per_kw_year: float

    ppa_share: float
    ppa_price: float
    ppa_tenor_years: int
    capture_price: float

    total_capex: float
    senior_debt: float

    tax_rate: float
    depreciation_years: int
    debt_rate: float
    debt_tenor_years: int
    degradation_rate: float
    price_escalation: float
    merchant_escalation: float
    opex_escalation: float

    ramp_factor: float
    hours_per_year_gwh: float

    @property
    def equity(self) -> float:
        """Project equity. Never stored in a file — one subtraction away (§9)."""
        return self.total_capex - self.senior_debt

    @property
    def cod_index(self) -> int:
        """Array index of the COD year. Negative if the asset already operates."""
        return self.cod_year - self.base_year


class ProjectStatements(NamedTuple):
    """Thirty years of statements, every series in file units and file order.

    Names and signs follow ``docs/pipeline-schema.md`` §5 exactly: every line is
    a positive magnitude except ``fcfe``, which is signed, and ``pbt``/``ebit``/
    ``net_income``, which may be negative. ``dscr`` carries NaN outside the debt
    life and is serialised as ``null`` there — the only null a file permits.
    """

    generation_gwh: npt.NDArray[np.float64]
    achieved_price: npt.NDArray[np.float64]
    revenue: npt.NDArray[np.float64]
    opex: npt.NDArray[np.float64]
    ebitda: npt.NDArray[np.float64]
    depreciation: npt.NDArray[np.float64]
    ebit: npt.NDArray[np.float64]
    interest_expense: npt.NDArray[np.float64]
    pbt: npt.NDArray[np.float64]
    tax_expense: npt.NDArray[np.float64]
    net_income: npt.NDArray[np.float64]
    capex: npt.NDArray[np.float64]
    debt_drawdown: npt.NDArray[np.float64]
    equity_drawdown: npt.NDArray[np.float64]
    fcfe: npt.NDArray[np.float64]
    debt_opening: npt.NDArray[np.float64]
    debt_repayment: npt.NDArray[np.float64]
    debt_closing: npt.NDArray[np.float64]
    ppe: npt.NDArray[np.float64]
    dscr: npt.NDArray[np.float64]


def _achieved_price(inputs: ProjectInputs, age: int) -> float:
    """Blended realised price in €/MWh at ``age`` years past COD (§9.2).

    Inside the PPA tenor the contracted price and the capture price escalate at
    their own rates and are weighted by the contracted share; after it, revenue
    is fully merchant. A zero tenor means fully merchant from the start, which
    the schema enforces by requiring a zero share alongside it.

    **The bracketing is load-bearing.** Each term is ``(share x price) x
    escalation``, left to right, because that is the order the reference
    evaluates and float multiplication is not associative: grouping it as
    ``share x (price x escalation)`` moves the result by about 1.4e-14 per year,
    which is inert against the EUR 0.01m tie-out tolerance but loses a
    field-for-field match against the golden corpus. For the same reason the
    merchant term is written out in both branches rather than shared — outside
    the tenor it carries no ``(1 - share)`` factor, so the two are different
    expressions rather than one with a coefficient.
    """
    if inputs.ppa_tenor_years > 0 and age < inputs.ppa_tenor_years:
        contracted = inputs.ppa_share * inputs.ppa_price * (1 + inputs.price_escalation) ** age
        merchant = (
            (1 - inputs.ppa_share) * inputs.capture_price * (1 + inputs.merchant_escalation) ** age
        )
        return contracted + merchant
    return inputs.capture_price * (1 + inputs.merchant_escalation) ** age


def stabilised_ebitda(inputs: ProjectInputs) -> float:
    """First-full-year EBITDA: one year of degradation, one of escalation, no ramp.

    The basis epic §6.3 fixes for debt sizing, and spec §9.1's basis for entry
    pricing. Synthetic on purpose — it is what a *stabilised* year would look
    like, which is the year a lender and a buyer both price off, and it is not
    the same number as the ramp year that actually follows COD.

    Note the tenor guard is ``> 1`` here where the per-year price uses
    ``> 0 and age < tenor``. Both come from the reference and they agree on every
    tenor it can produce (0, 10, 12, 15, 20), so the difference is inert; it is
    preserved rather than tidied because tidying it would be an unverifiable
    change to a number that prices every asset.
    """
    generation = (
        inputs.capacity_mw
        * inputs.hours_per_year_gwh
        * inputs.net_capacity_factor
        * (1 - inputs.degradation_rate)
    )
    if inputs.ppa_tenor_years > 1:
        price = inputs.ppa_share * inputs.ppa_price * (1 + inputs.price_escalation) + (
            1 - inputs.ppa_share
        ) * inputs.capture_price * (1 + inputs.merchant_escalation)
    else:
        price = inputs.capture_price * (1 + inputs.merchant_escalation)

    revenue = generation * MWH_PER_GWH * price / EUR_PER_EUR_MILLION
    opex = (
        inputs.capacity_mw
        * _KW_PER_MW
        * inputs.opex_per_kw_year
        * (1 + inputs.opex_escalation)
        / EUR_PER_EUR_MILLION
    )
    return revenue - opex


def entry_capex_per_kw(
    stabilised: float, capacity_mw: float, entry_yield: float, low: float, high: float
) -> tuple[float, str]:
    """Price the asset off its revenue case and clamp to the technology band (§9.1).

    A developer sells at the EBITDA yield the market demands for the asset's risk
    stage, so capex falls out of the revenue case rather than being supplied
    independently. The clamp is deliberate and load-bearing: it lets genuinely
    uneconomic assets exist in the pipeline, which is what gives the mandate's
    screens something to reject.

    Returns the €/kW figure and which limb of the band bound, if either —
    ``"floor"``, ``"cap"`` or ``"none"``. The caller recomputes total capex from
    the **clamped** figure, which is the whole point of clamping.
    """
    unclamped = stabilised / entry_yield / capacity_mw / _KW_PER_MW * EUR_PER_EUR_MILLION
    if unclamped < low:
        return low, "floor"
    if unclamped > high:
        return high, "cap"
    return unclamped, "none"


@dataclass(frozen=True, slots=True, kw_only=True)
class DebtTerms:
    """The terms a project's debt is sized against.

    They travel together because they are one decision: an analyst picks a
    target cover, a rate, a tenor and the policy ceiling for the stage as a set,
    and the dispersion report surfaces disagreement across the pipeline in the
    same set.
    """

    target_dscr: float
    rate: float
    tenor: int
    max_gearing: float


def size_senior_debt(stabilised: float, terms: DebtTerms, total_capex: float) -> tuple[float, str]:
    """Size senior debt off stabilised EBITDA, then cap it by gearing (§9.3, D-3).

    ``debt = min(max_gearing x capex, stabilised ÷ target_dscr x pv_factor)``.

    Note the **multiplication** by the annuity PV factor. Issue #8's prose writes
    a division, but its own acceptance criterion pins
    ``annuity_pv_factor(0.055, 18) = 11.246…``, which is the present value of one
    unit a year — so dividing by it would put the template project's senior debt
    at €0.70m against its actual €74.39m. The reference divides by the reciprocal
    *payment* factor, which is the same thing. Recorded in ``docs/decisions.md``.

    Returns the quantum and which constraint bound — ``"max-gearing-cap"`` or
    ``"dscr-sculpt"``. Which one binds is what makes min DSCR two-sided: a
    gearing-capped asset carries less debt than its cash flow would support and
    lands above the target, a sculpted one lands at or below it.
    """
    if terms.tenor <= 0 or terms.rate < 0:
        return 0.0, "max-gearing-cap"
    ceiling = terms.max_gearing * total_capex
    serviceable = stabilised / terms.target_dscr * annuity_pv_factor(terms.rate, terms.tenor)
    if ceiling <= serviceable:
        return max(0.0, ceiling), "max-gearing-cap"
    return max(0.0, serviceable), "dscr-sculpt"


def _debt_schedule(inputs: ProjectInputs) -> Schedule:
    if inputs.senior_debt <= 0 or inputs.debt_tenor_years <= 0:
        zeros = np.zeros(max(0, inputs.debt_tenor_years), dtype=_F64)
        return Schedule(zeros, zeros.copy(), zeros.copy(), zeros.copy())
    return schedule(inputs.senior_debt, inputs.debt_rate, inputs.debt_tenor_years)


def _funding(
    inputs: ProjectInputs,
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Capex, debt drawdown and equity drawdown, per ``pipeline-schema.md`` §6 (A-4).

    Equity funds the construction period pro rata, the whole facility is drawn at
    COD, and an asset already operating in the base year books everything in year
    one (§13). ``capex[t] == debtDrawdown[t] + equityDrawdown[t]`` holds in every
    year by construction, so the project is fully funded each year with no
    implicit bridge.
    """
    capex = np.zeros(YEARS, dtype=_F64)
    debt_drawdown = np.zeros(YEARS, dtype=_F64)
    equity_drawdown = np.zeros(YEARS, dtype=_F64)
    cod = inputs.cod_index

    if cod <= 0:
        capex[0] = inputs.total_capex
        equity_drawdown[0] = inputs.equity
        debt_drawdown[0] = inputs.senior_debt
        return capex, debt_drawdown, equity_drawdown

    per_year = inputs.equity / cod
    capex[:cod] = per_year
    equity_drawdown[:cod] = per_year
    if cod < YEARS:
        capex[cod] = inputs.senior_debt
        debt_drawdown[cod] = inputs.senior_debt
    return capex, debt_drawdown, equity_drawdown


def project_statements(inputs: ProjectInputs) -> ProjectStatements:
    """The full 30-year three-statement model for one project.

    Every tie-out in ``docs/pipeline-schema.md`` §7 holds by construction rather
    than by adjustment: the identities are how the lines are computed, not checks
    applied afterwards.

    The debt balance is **not** floored at zero here, unlike the defensive clamp
    in :func:`terrafolio.model.annuity.schedule`. A flow is a quantity and is
    never negative; a balance is the running result of subtracting flows, so one
    that amortises exactly to zero lands either side of it — about -1.2e-12 on a
    EUR 770m facility. Clamping would hide that residue in ``debtSchedule`` while
    leaving it in every other series, and 1A gave the three balance series a
    -1e-9 floor precisely so the honest number validates.

    ``fcfe`` is accumulated as ``ebitda - interest - principal - tax -
    equityDrawdown``. §7.3 states the equivalent ``… - capex + debtDrawdown``
    form, and the two are algebraically identical because
    ``-capex + debtDrawdown == -equityDrawdown`` in every year — but the §7.3
    form subtracts the entire facility and adds it straight back in the COD year,
    which costs the low bits of a small result. 1C-18 measured the difference at
    5e-15 and chose the same form.
    """
    sched = _debt_schedule(inputs)
    capex, debt_drawdown, equity_drawdown = _funding(inputs)

    generation = np.zeros(YEARS, dtype=_F64)
    achieved_price = np.zeros(YEARS, dtype=_F64)
    revenue = np.zeros(YEARS, dtype=_F64)
    opex = np.zeros(YEARS, dtype=_F64)
    depreciation = np.zeros(YEARS, dtype=_F64)
    interest = np.zeros(YEARS, dtype=_F64)
    repayment = np.zeros(YEARS, dtype=_F64)
    tax = np.zeros(YEARS, dtype=_F64)
    opening = np.zeros(YEARS, dtype=_F64)
    closing = np.zeros(YEARS, dtype=_F64)
    dscr = np.full(YEARS, np.nan, dtype=_F64)

    full_year = inputs.capacity_mw * inputs.hours_per_year_gwh * inputs.net_capacity_factor
    annual_depreciation = inputs.total_capex / inputs.depreciation_years
    balance = 0.0

    for index in range(YEARS):
        age = index + inputs.base_year - inputs.cod_year
        opening[index] = balance
        balance += debt_drawdown[index]

        if age < 0:
            closing[index] = balance
            continue

        ramp = inputs.ramp_factor if age == 0 else 1
        generation[index] = full_year * (1 - inputs.degradation_rate) ** age * ramp
        achieved_price[index] = _achieved_price(inputs, age)
        revenue[index] = (
            generation[index] * MWH_PER_GWH * achieved_price[index] / EUR_PER_EUR_MILLION
        )
        opex[index] = (
            inputs.capacity_mw
            * _KW_PER_MW
            * inputs.opex_per_kw_year
            * (1 + inputs.opex_escalation) ** age
            / EUR_PER_EUR_MILLION
        )
        if age < inputs.depreciation_years:
            depreciation[index] = annual_depreciation

        if age < inputs.debt_tenor_years and sched.principal.size:
            interest[index] = balance * inputs.debt_rate
            repayment[index] = sched.principal[age]
            balance -= repayment[index]

        earnings = revenue[index] - opex[index]
        taxable = earnings - interest[index] - depreciation[index]
        tax[index] = max(0.0, taxable * inputs.tax_rate)
        service = interest[index] + repayment[index]
        if age < inputs.debt_tenor_years:
            dscr[index] = earnings / service if service else np.nan
        closing[index] = balance

    ebitda = revenue - opex
    ebit = ebitda - depreciation
    pbt = ebit - interest
    net_income = pbt - tax
    fcfe = ebitda - interest - repayment - tax - equity_drawdown

    # The §7.6 roll-forward, applied year by year rather than as a cumulative
    # sum of `capex - depreciation`. The two are the same identity and differ by
    # about 1e-13 in float, because `(ppe - depreciation) + capex` rounds twice
    # per year where the vectorised form rounds the difference first. Written
    # the way the tie-out states it, which is also the way that reproduces the
    # corpus exactly.
    ppe = np.zeros(YEARS, dtype=_F64)
    carrying = 0.0
    for index in range(YEARS):
        carrying = carrying - depreciation[index] + capex[index]
        ppe[index] = carrying

    return ProjectStatements(
        generation_gwh=generation,
        achieved_price=achieved_price,
        revenue=revenue,
        opex=opex,
        ebitda=ebitda,
        depreciation=depreciation,
        ebit=ebit,
        interest_expense=interest,
        pbt=pbt,
        tax_expense=tax,
        net_income=net_income,
        capex=capex,
        debt_drawdown=debt_drawdown,
        equity_drawdown=equity_drawdown,
        fcfe=fcfe,
        debt_opening=opening,
        debt_repayment=repayment,
        debt_closing=closing,
        ppe=ppe,
        dscr=dscr,
    )


def min_dscr(dscr: npt.NDArray[np.float64], ramp: int | None) -> float:
    """The lowest cover ratio over the debt life, **excluding the ramp year** (§9.4).

    The exclusion is the consumer's job, not the file's: a file reports ``dscr``
    for every debt-life year including the ramp, whose figure is typically well
    below 1.0 because generation is partial against a full year of service.

    Returns NaN for a project with no debt-life year to measure, which is an
    unlevered project rather than a failing one — A-21 has the screens treat a
    null min DSCR as passing, since there is no coverage ratio to fail.
    """
    live = dscr.copy()
    if ramp is not None and 0 <= ramp < live.size:
        live[ramp] = np.nan
    if not np.any(np.isfinite(live)):
        return float("nan")
    return float(np.nanmin(live))
