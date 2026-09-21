# Renewables Portfolio Optimiser

Product specification for engineering — v1.0

Owner: Product · Reviewers: Investment team, Quantitative analytics, Engineering lead · Status: ready for estimation

## 1. Summary

The Portfolio Optimiser turns an investment mandate into a concrete list of solar and wind projects to acquire. An investment professional states what the fund wants — capital available, capacity target, technology mix, return hurdle — and the constraints it must respect, including eligible countries, minimum leverage, minimum debt service cover and concentration limits. The system searches the firm's live candidate pipeline and returns the combination of projects that best satisfies the mandate, with the portfolio metrics and the per-project detail needed to take the result into an investment committee.

The problem is combinatorial. A pipeline of 300 candidates has more subsets than can be enumerated, the objectives conflict (capacity versus return, diversification versus quality), and several constraints are portfolio-level rather than project-level, so no ranking of individual projects produces the right answer. Today this work is done by hand in spreadsheets: an analyst picks a shortlist, models it, finds it breaches a concentration limit, and starts again. One mandate takes two to three days and produces one answer with no record of the alternatives.

Version 1 delivers that answer in under ten seconds, every constraint enforced, every assumption recorded and reproducible.

## 2. Users and jobs to be done

| USER | JOB | SUCCESS LOOKS LIKE |
|---|---|---|
| Investment associate | Build a candidate portfolio for a new fund or a capital tranche | A defensible shortlist in minutes, with the model behind every number |
| Portfolio manager | Test how the answer moves when the mandate moves | Ten mandate variants compared in an afternoon |
| Investment committee | Interrogate a proposal before approving capital | Every constraint visibly met; each project's case available on one screen |
| Origination lead | See which pipeline gaps block the mandate | Clear signal that, e.g., the solar target is unreachable in the eligible countries |

## 3. Scope

| IN SCOPE FOR V1 | EXPLICITLY OUT OF V1 |
|---|---|
| Single-currency (EUR) European pipeline | Multi-currency portfolios and FX hedging cost modelling |
| Solar PV, onshore wind, offshore wind | Batteries, hydro, grid assets, secondary stakes |
| Greenfield, ready-to-build and under-construction assets | Stochastic price scenarios and P90 sensitivity runs |
| One mandate per run; unlimited runs | Fund-level waterfall, fees and carried interest |
| Deterministic, reproducible optimisation | Writing decisions back to the pipeline system of record |
| CSV export of holdings and cash flows | Multi-user concurrent editing of a mandate |

## 4. End-to-end flow

Three states, one linear path, with a loop back from the result to the mandate.

1. Mandate. The user states the objective and the constraints. The eligible-candidate count, capacity and equity requirement update live as constraints change, and infeasibility warnings appear before the run.
2. Search. The optimisation runs with visible progress: generation counter, best and mean fitness curves, and the running best portfolio's capacity, project count, equity drawn and blended return.
3. Portfolio. Headline metrics, a 30-year cash-flow chart, a map of the selected sites, and a sortable, filterable holdings table with a per-project detail sheet. From here the user locks or excludes projects and re-runs, or returns to the mandate.

## 5. Screen 1 — Mandate

Inputs are grouped into three panels: Objective (what the portfolio should achieve), Hard constraints (breaches are rejected, not penalised) and Risk & execution (screens applied to the candidate set before the search begins). Every control persists per user between sessions and is carried in the run record.

### 5.1 Objective

| FIELD | TYPE | RANGE / DEFAULT | SEMANTICS AND VALIDATION |
|---|---|---|---|
| Available equity capital | Slider, €m | 200–4,000, step 50; default 1,200 | Hard cap. Any portfolio whose total equity requirement exceeds it is rejected outright. |
| Capacity target | Slider, MW | 200–4,000, step 50; default 1,500 | Soft objective. Deviation is penalised symmetrically, normalised by the target. |
| Technology split | Split bar, % solar | 0–100, step 5; default 45 | Soft objective on capacity-weighted solar share. Offshore wind counts as wind. |
| Target equity IRR | Slider, % | 6–18, step 0.5; default 11 | Hurdle. Return above it is rewarded; below it is penalised. Never a hard reject — the user needs to see how close the pipeline gets. |
| Hold period | Number, years | 5–30; default 10 | Sets the exit year for every IRR and MOIC on the page. Exit year is shown read-only. |

### 5.2 Hard constraints

| FIELD | TYPE | RANGE / DEFAULT | SEMANTICS AND VALIDATION |
|---|---|---|---|
| Eligible countries | Multi-select chips | 14 markets; all on by default | Pre-screen. Candidates outside the set never enter the search. Empty selection disables the run. |
| Minimum portfolio leverage | Slider, % | 0–85, step 1; default 60 | Portfolio-level: total senior debt ÷ total project cost. Shortfall is penalised steeply. |
| Minimum DSCR | Number, × | 1.00–2.00, step 0.05; default 1.25 | Project-level pre-screen on the minimum annual debt service cover over the debt life. |
| Maximum merchant share | Number, % | 0–100, step 5; default 35 | Portfolio-level: capex-weighted share of revenue not under contract. |
| Maximum per country | Number, % | 10–100, step 5; default 35 | Portfolio-level concentration cap on capex by country. |
| Maximum per project | Number, % | 5–100, step 5; default 15 | Portfolio-level concentration cap on capex by single asset. |
| COD window | Two numbers, year | 2027–2033; default 2027–2032 | Pre-screen on commercial operation date. from greater than to is a validation error. |

### 5.3 Risk and execution screens

| FIELD | TYPE | RANGE / DEFAULT | SEMANTICS AND VALIDATION |
|---|---|---|---|
| Development risk appetite | Segmented | Low / Balanced / High; default Balanced | Caps the per-project development risk score at 2.6, 3.6 or 5.0 as a pre-screen, and caps the capex-weighted portfolio average at 2.4, 3.2 or 4.2 as a penalty. |
| Stages in scope | Multi-select chips | Greenfield, ready-to-build, construction; all on | Pre-screen on development stage. |
| Grid connection secured only | Toggle | Off | Pre-screen: requires a firm connection agreement. |
| EUR-denominated revenue only | Toggle | Off | Pre-screen: excludes PLN, RON, DKK, SEK and GBP revenue. |
| O&M partner contracted | Toggle | Off | Pre-screen: requires a signed long-term service agreement. |

### 5.4 Live feasibility feedback

The mandate footer recomputes on every change and shows candidates passing screens, eligible capacity and the equity required to buy the whole eligible set. Warnings are raised, in this order of severity, when:

- no candidate passes the screens — the run button is disabled;
- eligible capacity is below the capacity target;
- the minimum leverage exceeds what the eligible pool can support;
- the solar target is more than 20 points away from the eligible pool's own mix;
- the eligible pool absorbs less than 90% of available capital;
- projects are locked or excluded — shown as an informational count.

These are advisory except the first. The user is allowed to run an infeasible-looking mandate and see how close the optimiser gets.

## 6. Screen 2 — Search

The search screen exists to make a multi-second computation legible and to give the user confidence the result converged rather than timed out. It shows a generation counter against the total, a progress bar, two fitness curves (best and population mean), and five live figures from the running best portfolio: fitness, capacity, project count, equity drawn and blended IRR.

Engineering note: the curves must be driven by real per-generation data streamed from the engine, not a simulated animation. If the run completes faster than 1.5 seconds, hold the screen for that minimum so the transition is readable. Copy on this screen is written for an investor, not an algorithm engineer — see §14.

## 7. Screen 3 — Portfolio

### 7.1 Headline metrics

Twelve tiles in a single strip. Each shows a value and a sub-label that states the mandate's target or cap for that metric, coloured to indicate compliance.

| METRIC | DEFINITION |
|---|---|
| Installed capacity | Sum of nameplate MW. Compared with the capacity target. |
| Projects | Count, split solar versus wind. |
| Technology split | Capacity-weighted solar share, against the target mix. |
| Equity required | Sum of project equity, and the percentage of available capital deployed. |
| Total project cost | Sum of capex, with the senior debt quantum beneath it. |
| Equity IRR | IRR of the aggregated portfolio equity cash flow over the hold period, including exit. Shown with MOIC and the hurdle. |
| Leverage | Total debt ÷ total capex, with the portfolio's worst project-level minimum DSCR. |
| Weighted LCOE | Generation-weighted levelised cost of energy, 6% real discount rate. |
| Annual generation | P50 GWh in a full operating year, with CO₂ avoided at 0.32 t/MWh. |
| 30-year FCFE | Undiscounted sum of free cash flow to equity, post debt service and tax. |
| Merchant exposure | Capex-weighted uncontracted revenue share, against the cap. |
| Largest country | Largest single-country capex share, with the portfolio's capex-weighted development risk score. |

### 7.2 Cash-flow chart

Thirty bars, one per year from the current base year, showing free cash flow to equity in nominal euros after debt service and tax. Years before commercial operation are negative — they are the equity draw-down during construction — and render below the zero line in a lighter tone. Hovering a bar reveals the year and amount. The caption carries the undiscounted 30-year total.

### 7.3 Site map

Real geographic projection of the selected sites: countries with holdings are tinted, each site is a marker whose area scales with capacity and whose colour distinguishes solar from wind. Geometry comes from a vendored Natural Earth dataset, not a hand-drawn outline, and the map degrades to a notice if the dataset fails to load.

### 7.4 Holdings table

Sixteen columns, sortable on every numeric and text column, with a free-text search and filters on country, technology and stage. A toggle switches between the selected portfolio and the full eligible candidate set, so the user can see what the optimiser rejected; non-selected rows are shaded.

| COLUMN | DEFINITION |
|---|---|
| Lock | Forces the project into the next run. Persists across runs until cleared. |
| Project / country | Name, country and internal project ID. |
| Technology, Stage | Solar PV, onshore wind, offshore wind; greenfield, ready-to-build, construction. |
| MW, COD | Nameplate capacity and commercial operation year. |
| Capex €m, Equity €m, Leverage | Total project cost, equity cheque and sculpted senior debt as a share of cost. |
| Capacity factor, P50 GWh/y | Net capacity factor and expected annual generation. |
| LCOE €/MWh | Levelised cost of energy at 6% real. |
| Contracted | Share of revenue under PPA. |
| Equity IRR | Project-level IRR over the mandate's hold period, including exit. |
| Min DSCR | Minimum annual debt service cover over the debt life; red below the mandate floor. |
| Risk | Development risk score, 1–5. |

### 7.5 Project detail sheet

A drawer opened from any row, with four headline figures (capacity, IRR, equity, MOIC) and four grouped sections:

- Technical — net capacity factor, P50 generation, COD, grid connection status, O&M arrangement.
- Capital structure — total cost and €/kW, senior debt with gearing, rate and tenor, minimum DSCR, equity payback year, currency and hedging requirement.
- Revenue — contracted share, PPA price and tenor, capture price against country baseload, LCOE, opex per kW.
- Risk — development risk score, status in the portfolio, lock state.

The drawer also carries the two actions that steer the next run: lock into portfolio and exclude from search.

### 7.6 Steering and re-running

Locks and exclusions accumulate across runs. Locked projects are forced into every candidate portfolio in the population, so the optimiser solves around them; excluded projects are removed from the candidate set before the search. When either set changes, or the mandate is edited, the primary action relabels to re-run with changes. Each run gets an incrementing reference used in the export.

### 7.7 Export

Three outputs: the holdings table as CSV (all sixteen columns, selection only), the 30-year portfolio cash-flow schedule as CSV, and a print-to-PDF investment committee sheet. All three carry the run reference and the mandate.

## 8. Domain model

A Project is the unit of everything. Fields marked source come from the pipeline system of record; fields marked derived are computed by the financial engine and must not be stored as inputs.

| FIELD | ORIGIN | NOTES |
|---|---|---|
| id, name, country, ISO code, lat/lon | source | Coordinates required for the map; site-centroid precision is sufficient. |
| technology, stage | source | Enumerations, not free text. |
| capacityMw, codYear | source | COD as a year; month-level precision is deferred. |
| netCapacityFactor | source | P50, net of losses and availability. The single most sensitive input in the model. |
| opexPerKwYear | source | Real, base-year euros. |
| ppaShare, ppaPrice, ppaTenorYears | source | Zero tenor means fully merchant. |
| countryBaseloadPrice | source | Long-term forecast curve, by market. |
| captureFactor | source | Technology- and market-specific ratio of achieved price to baseload. |
| developmentRiskScore | source | 1–5, set by the origination team at intake. |
| gridSecured, omContracted, currency | source | Booleans and an ISO currency code driving the execution screens. |
| maxGearing | source | Policy ceiling by stage: 75% construction, 72% ready-to-build, 65% greenfield. |
| capex, capexPerKw | derived | From the stabilised revenue case and a stage-dependent entry yield — see §9.1. |
| debt, equity, leverage | derived | Sculpted — see §9.3. |
| cashflowSchedule, minDscr, lcoe, irr, moic | derived | Recomputed whenever the hold period or exit assumption changes. |

## 9. Financial engine

Every project carries a 30-year annual model, computed once per pipeline load and cached. Year indices run from the current base year; age is years since commercial operation.

### 9.1 Entry pricing

Capex is derived from the revenue case rather than supplied independently, because a developer prices an asset at the EBITDA yield the market demands for its risk stage. Stabilised first-full-year EBITDA is divided by a stage entry yield — 11.6% greenfield, 10.3% ready-to-build, 9.5% construction, 9.0% offshore, jittered ±0.7 points — and the result is clamped to a technology band in €/kW (solar 560–950, onshore wind 1,050–1,700, offshore 2,200–3,400). Clamping is deliberate: it lets genuinely uneconomic assets exist in the pipeline so the screens have something to reject.

### 9.2 Revenue and cost

- Generation = MW × 8.760 × capacity factor × (1 − degradation)^age GWh, degradation 0.5% solar and 0.2% wind, with the first operating year at 55% for a partial-year ramp.
- Capture price = country baseload × capture factor (0.68 solar, 0.88 onshore wind, 0.86 offshore).
- Achieved price within the PPA tenor blends the contracted price, escalating 0.5% a year, with the capture price, escalating 2.1%, weighted by contracted share. After the tenor, revenue is fully merchant.
- Opex = MW × 1,000 × opex per kW × 1.021^age.

### 9.3 Debt

Senior debt is sculpted to a 1.40× base-case DSCR on an 18-year annuity at 5.5%, then capped by the project's maximum gearing. Weaker assets therefore carry less leverage rather than being unfinanceable, and portfolio leverage becomes an outcome of asset quality — which is what makes the minimum-leverage constraint meaningful. Drawn at COD; equity funds the construction period pro rata across the years to COD, or in full in the base year for assets already operating.

### 9.4 Returns

- Tax: 20% of EBITDA less interest less straight-line depreciation over 25 years, floored at zero.
- FCFE = EBITDA − interest − principal − tax, less construction equity draws.
- Minimum DSCR = the lowest annual EBITDA ÷ debt service over the debt life, excluding the ramp year.
- LCOE = (capex + PV of opex) ÷ PV of generation, 6% real.
- IRR by bisection over the equity cash flow truncated at the hold year, with a terminal value of the exit multiple × exit-year EBITDA less outstanding debt. Default multiples: 9.0× solar, 8.5× onshore wind, 9.5× offshore. Returns null where the series has no sign change; the UI must render that as an em dash, never as zero.

> All rates, escalators, tenors, tax rates, capture factors and exit multiples are configuration, not code constants. The investment team must be able to change them per fund without a release, and every run must record the set it used.

## 10. Optimisation engine

A population-based combinatorial search over subsets of the eligible candidate set.

### 10.1 Encoding and operators

| ELEMENT | SPECIFICATION |
|---|---|
| Chromosome | Bit vector of length n, one bit per eligible candidate; 1 means include. |
| Initialisation | Random with 35% inclusion probability; locked bits forced to 1. |
| Population / generations | 90 × 60 at Standard effort; 50 × 35 Fast; 160 × 110 Exhaustive. |
| Selection | Binary tournament. |
| Crossover | Uniform, per-bit. |
| Mutation | Bit flip at 2.5%. |
| Elitism | Top two chromosomes carried unchanged. |
| Repair | Locked bits re-forced after crossover and mutation. |
| Determinism | Seeded PRNG. The same mandate, pipeline snapshot and seed must produce an identical portfolio. |

### 10.2 Objective function

A weighted sum of rewards less constraint penalties. An empty portfolio scores −50; one whose equity exceeds available capital is rejected before scoring.

| TERM | WEIGHT | FORM |
|---|---|---|
| Capacity match | 3.2 | 1 − \|MW − target\| ÷ target, floored at −0.6 |
| Technology split match | 3.0 | 1 − \|solar share − target\| ÷ 0.35, floored at −0.5 |
| Return versus hurdle | 2.6 | (blended IRR − hurdle) ÷ 5pp, clamped to ±1.2 |
| Capital utilisation | 0.9 | equity ÷ available capital |
| Leverage shortfall | −7 | max(0, minimum − actual) |
| Merchant overshoot | −14 | max(0, actual − cap) |
| Country concentration | −8 | Ʃ max(0, country share − cap) |
| Single-project concentration | −8 | Ʃ max(0, project share − cap) |
| Risk overshoot | −1.6 | max(0, weighted risk − appetite cap) |

> Weights are tuned so that a constraint breach always costs more than the reward available from breaching it. They belong in configuration, and changing them must be an auditable event: the same mandate against the same pipeline should not silently produce a different portfolio next quarter.

### 10.3 Performance

During the search, project IRRs are taken from a pre-computed per-project cache and blended by equity weight; the true portfolio IRR is solved once, on the winning chromosome. Full IRR solving inside the fitness loop is roughly three orders of magnitude more expensive and is not acceptable. Budget: a Standard run over 500 candidates completes in under five seconds, streaming a generation update at least every 100 ms.

## 11. Interfaces

| ENDPOINT | BEHAVIOUR |
|---|---|
| GET /pipeline | Current candidate set with derived economics and a snapshot hash. Cacheable; the hash pins a run to the data it saw. |
| POST /optimisations | Body: mandate, locked IDs, excluded IDs, effort, optional seed. Returns 202 with a run ID. |
| GET /optimisations/{id}/stream | Server-sent events: generation, best fitness, mean fitness, running best summary. |
| GET /optimisations/{id} | Final result: selected IDs, portfolio aggregates, 30-year cash flow, mandate and assumption set, pipeline hash, seed, duration. |
| GET /optimisations/{id}/holdings.csv, /cashflow.csv | The two exports, generated server-side so they match the stored result exactly. |

Runs are immutable and addressable: a run ID is shareable and reopens the exact result, including the mandate that produced it. This is the audit trail the investment committee needs.

## 12. Non-functional requirements

| AREA | REQUIREMENT |
|---|---|
| Reproducibility | Mandate + pipeline hash + assumption set + seed determines the result exactly. Regression tests assert this across releases. |
| Auditability | Every run stores its inputs and outputs indefinitely. Assumption-set changes are versioned and attributable. |
| Performance | Mandate feedback under 100 ms; Standard run under 5 s at 500 candidates; table sort and filter under 50 ms at 500 rows. |
| Scale | Pipeline up to 2,000 candidates without architectural change. |
| Accessibility | Full keyboard operation, visible focus, 4.5:1 contrast for body text, no colour-only status encoding. |
| Security | Pipeline data is commercially sensitive: authenticated access, role-based visibility by fund, no third-party analytics on result pages. |
| Offline | The result view must export to a self-contained file that opens without network access, for committee packs. |

## 13. Edge cases and error states

| CASE | EXPECTED BEHAVIOUR |
|---|---|
| No candidate passes the screens | Run disabled, explicit warning naming the screens to widen. |
| Locked projects alone exceed available capital | Block the run and say which locks to release. |
| Locked projects alone breach a concentration cap | Run proceeds; the breach is surfaced on the result tile in the alert colour rather than hidden. |
| Capacity target unreachable | Run proceeds and returns the best feasible portfolio; the tile shows the shortfall against target. |
| IRR undefined (no sign change in the cash flow) | Render an em dash. Never coerce to zero, and exclude from weighted averages. |
| Project already operating in the base year | Entire equity outflow booked in year one; no construction draw-down. |
| Hold period shorter than the last COD | Project contributes only construction outflows and an exit value; flagged in the detail sheet. |
| Pipeline changes mid-session | Stored runs keep their snapshot. Re-running warns that the pipeline moved. |
| Map geometry unavailable | Map panel degrades to a notice; the rest of the page is unaffected. |

## 14. Content and tone

The interface speaks the language of an investment professional, not of an optimisation engineer. Screens refer to the optimiser and to searching the pipeline. Algorithm-internal vocabulary — population, crossover, mutation, fitness, generation — is being removed from user-facing copy and confined to the technical documentation and the run record. Numbers are formatted consistently: euros in millions with thousands separators, capacity in MW, IRR to one decimal, DSCR to two with a multiplication sign.

## 15. Assumptions and open questions

1. Single-currency modelling. Non-EUR markets are screened in or out, not hedged with a modelled cost. Confirm whether v1.1 needs an explicit hedging drag.
2. Debt terms are uniform (5.5%, 18-year annuity). Should these vary by country and stage in v1?
3. Exit is modelled as an EBITDA multiple. The alternative — exit at a target unlevered IRR — changes results materially for late-COD assets. Decision needed before build.
4. Development risk score is a manual field. Its consistency across originators determines whether the risk-appetite control means anything.
5. No allowance for transaction costs, development fees or contingency. Confirm whether these sit inside capex or outside the model.
6. One mandate, one answer. Presenting a set of trade-off portfolios along the capacity/return frontier is the obvious next step — see §16.

## 16. Delivery

| PHASE | CONTENTS |
|---|---|
| Phase 1 | Pipeline ingestion, financial engine, deterministic optimiser, three screens, CSV export. The specification above. |
| Phase 2 | Saved mandates and named scenarios; side-by-side comparison of two runs; the committee PDF as a designed artefact rather than a print of the screen. |
| Phase 3 | Trade-off frontier: several portfolios spanning capacity against return, chosen by the user rather than by the weight set. Sensitivity runs on price and capacity factor. |
| Phase 4 | Multi-currency with hedging cost, additional technologies, write-back of investment decisions to the pipeline system of record. |
