# Project file schema

**Normative.** This document defines the project statement file: every field, its unit, its domain,
its validation rule, and the tie-outs a file must satisfy before it is loaded. The pydantic
expression of this schema lives in `src/terrafolio/domain/` (issue #2); the loader and validator
live in `src/terrafolio/pipeline/` (issue #6). Where they disagree with this document, this document
is right and they are a defect.

[`../templates/project-template.json`](../templates/project-template.json) is the canonical example
— Almonte Solar, 180 MW solar PV in Spain, COD 2028 — and every figure quoted below comes from it.
[`../templates/project-template.xlsx`](../templates/project-template.xlsx) carries the identical
fields laid out the way a financial model actually is: line items as rows, the 30 years as columns.
There is one schema, not two.

Section numbers cited as §n refer to [`spec.md`](spec.md). Decisions cited as A-n or D-n refer to
[`decisions.md`](decisions.md).

---

## 1. What a file is

**The pipeline is a directory of project files, one per park.** Each file carries that project's
full 30-year financials as modelled by the analyst who follows it — income statement, cash flow
statement, balance sheet, debt schedule and ratios — alongside identity, capital structure,
contracts and risk attributes. A user adds a project by dropping a file in and removes one by
deleting it.

Every file follows this one fixed template with identical structure. **No file is loaded until it
passes tie-out validation.** The earlier a project's stage, the more of the file is prediction
rather than contract, which the file records in its [`provenance`](#8-provenance) block.

A file is UTF-8 JSON. Its name is not significant; `id` is.

---

## 2. Units and conventions

These are the only two unit boundaries in the system. Converting anywhere else is the classic
failure mode in this kind of model.

| Where | Units |
|---|---|
| **This file** | Money in **€m**. Energy in **GWh**. Capacity in **MW**. Prices in **€/MWh**. Opex in **€/kW/year**. |
| `ProjectArrays` and everything downstream | **Euros** and **GWh**, `float64`. Converted once, at load. |
| The HTTP API | Back to €m, with an explicit **`_m` field suffix** — see [`api.md`](api.md). |

Further conventions:

- **Shares and rates are fractions of one**, never percentages: `ppaShare` `0.55`, `taxRate` `0.20`,
  `maxGearing` `0.72`. The UI renders them as percentages; the file never does.
- **Signs.** `fcfe` is signed, negative meaning an outflow. Every other statement line is stated
  positive and carries its sign through the identity that consumes it: `opex`, `depreciation`,
  `interestExpense`, `taxExpense`, `capex`, `debtRepayment` are all positive magnitudes.
- **Projects are canonically ordered by `id` ascending**, everywhere in the system. The optimiser's
  PRNG draws are indexed by position, so this is a determinism requirement, not tidiness.
- **Every statement line is in euros, in every file.** §3 scopes v1 to a single-currency EUR
  pipeline, and the aggregates are plain sums, so a statement denominated in anything else would be
  added to euros as though it were euros. `currency` is the **revenue** currency, an execution-screen
  input and a hedging flag — never the denomination of the statements (A-12).
- **Year 0 is `assumptions.baseYear`, and every file in a pipeline shares it.** All 30 arrays are
  indexed from it, and the portfolio aggregates them **by position**, so files that disagree would
  sum different calendar years. See [§4.6](#46-assumptions).

### 2.1 Nothing mandate-dependent is ever stored

A file describes a project. It does not describe a project *under a mandate*. IRR, MOIC, terminal
value, payback and min DSCR all depend on the mandate's hold period and the portfolio-level
assumption set, so none of them may appear in a file — see
[§9, the reject-derived-fields rule](#9-the-reject-derived-fields-rule). Storing one silently stops
the hold-period slider working.

---

## 3. File layout

```
{
  "schemaVersion":    "1.0",
  "id":               …,
  "name":             …,
  "location":         { … },
  "asset":            { … },
  "revenue":          { … },
  "execution":        { … },
  "capitalStructure": { … },
  "assumptions":      { … },
  "statements":       { years, physicals, incomeStatement, cashFlow,
                        debtSchedule, balanceSheet, ratios },
  "provenance":       { … }
}
```

Every one of these keys is required. Unknown keys at any level are a validation **error**, not a
warning: a misspelled field that is silently ignored is how a wrong number reaches a committee.

---

## 4. Identity

| Field | Type | Unit | Domain | Validation |
|---|---|---|---|---|
| `schemaVersion` | string | — | `"1.0"` | Exact match. A file at another version is rejected with its version named. |
| `id` | string | — | `^[A-Z][A-Za-z0-9_-]{1,31}$` | Unique across the pipeline. Duplicate ids abort the load naming both files. Sorts as the canonical order. |
| `name` | string | — | 1–120 chars, non-blank | Free text. Shown in the holdings table and the detail sheet. |

### 4.1 `location`

| Field | Type | Unit | Domain | Validation |
|---|---|---|---|---|
| `country` | string | — | 1–60 chars | Display name. Must agree with `countryCode` against the loader's country table. |
| `countryCode` | string | — | ISO 3166-1 alpha-2 | Drives the eligible-countries screen and the per-country concentration cap. `UK` is accepted as an alias of `GB`. |
| `iso3` | string | — | ISO 3166-1 alpha-3 | Joins to the map's Natural Earth geometry (§7.3). |
| `lat` | number | degrees | −90 … 90 | Required — the map needs it. Site-centroid precision is sufficient (§8). |
| `lon` | number | degrees | −180 … 180 | As above. |

### 4.2 `asset`

| Field | Type | Unit | Domain | Validation |
|---|---|---|---|---|
| `technology` | enum | — | `solar` \| `onshore_wind` \| `offshore_wind` | Enumeration, not free text (§8). Offshore wind counts as wind in the technology-split objective. |
| `stage` | enum | — | `greenfield` \| `ready_to_build` \| `construction` | Enumeration. Sets the gearing ceiling used by the plausibility check. Brownfield is out of scope for v1 (Q-6). |
| `capacityMw` | number | MW | > 0, ≤ 2000 | Nameplate. Drives every capacity aggregate. |
| `codYear` | integer | year | `baseYear` − 10 … `baseYear` + 29 | Commercial operation date as a **year**; month precision is deferred (§8). A COD before `baseYear` means the asset is already operating. |
| `netCapacityFactor` | number | fraction | 0 … 1 | P50, net of losses and availability. **The single most sensitive input in the model** (§8). Plausibility band 0.05–0.60. |
| `opexPerKwYear` | number | €/kW/yr | > 0, ≤ 300 | Real, base-year euros (§8). |

### 4.3 `revenue`

| Field | Type | Unit | Domain | Validation |
|---|---|---|---|---|
| `ppaShare` | number | fraction | 0 … 1 | Share of revenue under contract. `1 − ppaShare` is the project's merchant share; the portfolio's is capex-weighted. |
| `ppaPrice` | number | €/MWh | ≥ 0, ≤ 500 | Contracted price in base-year terms. |
| `ppaTenorYears` | integer | years | 0 … 30 | **Zero tenor means fully merchant** (§8). `ppaTenorYears = 0` requires `ppaShare = 0`. |
| `countryBaseloadPrice` | number | €/MWh | > 0, ≤ 500 | The market's long-term forecast baseload curve, by market (§8). |
| `captureFactor` | number | fraction | 0 … 2 | Technology- and market-specific ratio of achieved price to baseload (§8). Capture price = `countryBaseloadPrice × captureFactor`. |

### 4.4 `execution`

| Field | Type | Unit | Domain | Validation |
|---|---|---|---|---|
| `developmentRiskScore` | number | score | 1.0 … 5.0, one decimal | Set by the origination team at intake (§8). Pre-screened against the risk-appetite cap; the capex-weighted portfolio average is penalised. |
| `gridSecured` | boolean | — | — | True iff a firm connection agreement exists. Drives the §5.3 screen. |
| `omContracted` | boolean | — | — | True iff a signed long-term service agreement exists. Drives the §5.3 screen. |
| `currency` | string | — | ISO 4217 | The currency the project's **revenue** is earned in, which is what the §5.3 EUR-only screen tests and what the detail sheet flags as `hedge required`. **Not** the denomination of the statements, which are always euros (A-12). |

### 4.5 `capitalStructure`

These describe **what the analyst assumed**, not what asset quality supports (A-8). §9.3's claim
that portfolio leverage is an outcome of asset quality no longer holds; the minimum-leverage
constraint discriminates between *declared* capital structures.

| Field | Type | Unit | Domain | Validation |
|---|---|---|---|---|
| `totalCapex` | number | €m | > 0 | Total project cost. **The scalar is `totalCapex`; `capex` is the annual cash-flow line** (A-2). Ties to `Σ cashFlow.capex`. |
| `seniorDebt` | number | €m | ≥ 0, ≤ `totalCapex` | Senior debt quantum. Ties to `Σ cashFlow.debtDrawdown`. |
| `maxGearing` | number | fraction | 0 … 1 | The policy ceiling the analyst sized against. Plausibility-checked against the stage ceiling (§10). |

Project equity is `totalCapex − seniorDebt` and gearing is `seniorDebt ÷ totalCapex`. Neither is
stored — both are one subtraction away and storing them invites drift (§9).

### 4.6 `assumptions`

The file's **declared** assumptions. These are inputs, not configuration: the investment team does
not turn them, and the cross-file dispersion report surfaces disagreement between files (A-8, §11).

| Field | Type | Unit | Domain | Validation |
|---|---|---|---|---|
| `baseYear` | integer | year | 2000 … 2100 | Year 0 of every array. `statements.years` must run `baseYear … baseYear + 29`, and **every file in the pipeline must carry the same value** — see [§4.6.1](#461-the-pipeline-base-year-is-unanimous). |
| `taxRate` | number | fraction | 0 … 0.6 | Used by the `taxExpense` plausibility check. |
| `depreciationYears` | integer | years | 1 … 40 | Straight-line life from COD. |
| `debtRate` | number | fraction | 0 … 0.25 | Nominal senior rate. Used by the dispersion report. |
| `debtTenorYears` | integer | years | 0 … 30 | Debt life from COD. Determines exactly which years carry a `dscr` (§7). |

#### 4.6.1 The pipeline base year is unanimous

`baseYear` is the one declared assumption that is **not** merely reported by the dispersion report.
Every other field in this block may vary between files and the report simply surfaces the spread. A
`baseYear` that varies cannot be tolerated, because the portfolio aggregates the 30-element arrays
**by position**: a file based in 2027 and a file based in 2028 would have their 2027 and 2028
figures added together, and every portfolio cash flow, exit year and return would silently mix
calendar years (A-13).

So:

- The **pipeline base year** is the `baseYear` shared by every loaded file.
- If the files do not agree, the **load fails as a whole** — not file by file. The error names the
  majority year, and every file that disagrees with it, so an analyst can see at once whether one
  file is stale or a re-basing is half-finished.
- `GET /pipeline` exposes that single `baseYear` ([`api.md` §2](api.md#2-get-pipeline)), and the
  mandate's exit year is `baseYear + holdYears`.

Re-basing a file is the analyst's job: the loader never shifts a series to make it fit.

---

## 5. `statements`

Six arrays-of-arrays plus `years`. **Every series is exactly 30 elements**, indexed from
`assumptions.baseYear`. There are no nulls anywhere except `ratios.dscr` outside the debt life.

### 5.1 `years`

`[baseYear, baseYear + 1, …, baseYear + 29]` — 30 contiguous integers. Redundant by construction and
present on purpose: it makes a mis-indexed file fail loudly instead of quietly.

### 5.2 `physicals`

| Series | Unit | Notes |
|---|---|---|
| `generationGwh` | GWh | P50 output. Zero before COD. The first operating year is a **ramp year** at a partial-year fraction of full output. |
| `achievedPrice` | €/MWh | Blended realised price. Zero before COD. Within the PPA tenor it blends the contracted price with the capture price, weighted by `ppaShare`; after the tenor revenue is fully merchant (§9.2). |

### 5.3 `incomeStatement`

| Series | Unit | Notes |
|---|---|---|
| `revenue` | €m | Ties to physicals — see [§7](#7-tie-outs). |
| `opex` | €m | Positive magnitude. |
| `ebitda` | €m | Revenue less opex (§9 glossary). |
| `depreciation` | €m | Positive. Straight line over `depreciationYears` from COD. |
| `ebit` | €m | |
| `interestExpense` | €m | Accrual basis. Positive. |
| `pbt` | €m | May be negative. |
| `taxExpense` | €m | Positive or zero; floored at zero (§9.4). |
| `netIncome` | €m | May be negative. |

### 5.4 `cashFlow`

| Series | Unit | Notes |
|---|---|---|
| `interestPaid` | €m | Cash basis, positive. Equals `interestExpense` in the seed pipeline but is a distinct concept; the DSCR tie-out uses **this** one. |
| `debtRepayment` | €m | Principal repaid, positive. |
| `taxPaid` | €m | Cash basis, positive. |
| `capex` | €m | **Annual** capital expenditure, positive. Not a scalar (A-2). |
| `debtDrawdown` | €m | Positive. |
| `equityDrawdown` | €m | Positive. |
| `fcfe` | €m | **Signed**; negative is an outflow. Free cash flow to equity. |

### 5.5 `debtSchedule`

`opening`, `drawdown`, `repayment`, `closing`, all €m and all positive. `opening` excludes that
year's `drawdown`; the roll-forward is `closing = opening − repayment + drawdown`.

### 5.6 `balanceSheet`

`ppe` — €m, the closing property, plant and equipment balance. v1 validates the PP&E roll-forward
only; the senior debt balance lives in `debtSchedule` and a fuller balance sheet is deferred.

### 5.7 `ratios`

`dscr` — a multiple, or `null`. **Non-null exactly in the debt life**, that is where
`0 ≤ year − codYear < assumptions.debtTenorYears`; `null` everywhere else. This is the only place a
null is permitted in a file.

The file reports `dscr` for every debt-life year **including the ramp year**, whose figure is
typically well below 1.0 because generation is partial. The portfolio's *minimum* DSCR excludes the
ramp year (§9.4) — that exclusion is the consumer's job, not the file's.

---

## 6. The construction-funding convention

Fixed by A-4, because §9.3 pins the economics but not the statement rendering, and the rendering
changes FCFE *timing* and therefore every IRR.

With `buildYears = max(1, codYear − baseYear)` and `equity = totalCapex − seniorDebt`:

| Year | `capex` | `equityDrawdown` | `debtDrawdown` |
|---|---|---|---|
| before COD | `equity ÷ buildYears` | `equity ÷ buildYears` | 0 |
| the COD year | `seniorDebt` | 0 | `seniorDebt` |
| already operating (`codYear ≤ baseYear`) | `totalCapex`, all in year one | `equity` | `seniorDebt` |

This is §9.3 exactly — equity funds the construction period pro rata, debt is drawn at COD — and it
makes `capex[t] = debtDrawdown[t] + equityDrawdown[t]` true in every year, so the project is fully
funded each year with no implicit bridge. Because `− capex + debtDrawdown = − equityDrawdown`, the
`fcfe` identity in [§7](#7-tie-outs) reproduces §9.4's "less construction equity draws" identically.

The already-operating row is what §13 requires: "entire equity outflow booked in year one; no
construction draw-down".

---

## 7. Tie-outs

**Blocking.** A file that fails any of these does not load, and the loader reports the check, the
year and the residual. Tolerance is **€0.01m absolute or 0.1% relative**, whichever is looser, read
from the assumption set (`validation.tolerance_abs_m`, `validation.tolerance_rel`) and **never as a
code constant**.

### 7.1 Income statement

| Check | Rule |
|---|---|
| EBITDA | `ebitda = revenue − opex` |
| EBIT | `ebit = ebitda − depreciation` |
| PBT | `pbt = ebit − interestExpense` |
| Net income | `netIncome = pbt − taxExpense` |

### 7.2 Revenue ties to physicals

```
revenue_€m = generationGwh × 1000 × achievedPrice_€/MWh ÷ 1e6
```

The `÷ 1e6` is not optional and is not a fudge: GWh × 1000 gives MWh, MWh × €/MWh gives **euros**,
and every statement line in this file is **€m** (A-1). The same applies to opex, which an analyst's
model will compute as `capacityMw × 1000 × opexPerKwYear ÷ 1e6`.

### 7.3 Cash flow

```
fcfe = ebitda − interestPaid − debtRepayment − taxPaid − capex + debtDrawdown
```

### 7.4 Debt schedule

| Check | Rule |
|---|---|
| Roll-forward | `closing[t] = opening[t] − repayment[t] + drawdown[t]`, every year |
| Continuity | `opening[t] = closing[t−1]`, and `opening[0] = 0` |
| Fully amortised | `closing[last] = 0` |

### 7.5 Funding

| Check | Rule |
|---|---|
| Debt | `Σ debtDrawdown = seniorDebt` |
| Equity | `Σ equityDrawdown = totalCapex − seniorDebt` (A-2 — `totalCapex`, not `capex`) |
| Capex | `Σ capex = totalCapex` |
| Per year | `capex[t] = debtDrawdown[t] + equityDrawdown[t]` |

### 7.6 Depreciation and PP&E

| Check | Rule |
|---|---|
| PP&E roll-forward | `ppe[t] = ppe[t−1] − depreciation[t] + capex[t]`, with `ppe[−1] = 0` |
| Depreciation total | `Σ depreciation = totalCapex − ppe[last]` |

**Why the residual form** (A-3). `Σ depreciation = totalCapex` is false whenever the depreciation
life runs past the 30-year window — a 25-year life from a COD of 2033 against a 2027 base year ends
in 2057 and loses a year, and §5.2 allows COD up to 2033. The residual form always holds, and it
pins the unamortised balance as well as the total, so it is strictly stronger. Equality with
`totalCapex` holds **iff** `codYear + depreciationYears ≤ baseYear + 30`.

### 7.7 DSCR

Where `dscr` is non-null:

```
dscr = ebitda ÷ (interestPaid + debtRepayment)
```

### 7.8 Shape

| Check | Rule |
|---|---|
| Length | Every series in `statements` has exactly 30 elements |
| Years | `years = [baseYear … baseYear + 29]`, contiguous and ascending |
| Nulls | No nulls anywhere except `ratios.dscr` |
| DSCR coverage | `dscr` is non-null **exactly** where `0 ≤ years[t] − codYear < debtTenorYears` |
| Keys | No unknown keys at any level; no missing keys |

### 7.9 Pipeline-level

These are checked across files, once the individual files have passed. They fail the **load**, not a
file, because no subset of the pipeline is usable when one of them breaks.

| Check | Rule |
|---|---|
| Unique ids | No two files declare the same `id`. The error names both files. |
| One base year | Every file declares the same `assumptions.baseYear` (A-13, [§4.6.1](#461-the-pipeline-base-year-is-unanimous)). The error names the majority year and every file that disagrees. |

---

## 8. Provenance

The earlier a project's stage, the more of the file is prediction rather than contract. The
`provenance` block records which, so the dispersion report and the detail sheet can say how much
weight a number carries.

```
"provenance": {
  "preparedBy":   "…",          // an identifier for the analyst or desk
  "preparedOn":   "YYYY-MM-DD", // ISO 8601 date
  "modelVersion": "…",          // the analyst's own model revision
  "fields": { <group>: { "estimateBasis": …, "confidence": …, "note": … }, … }
}
```

`fields` carries one entry for each of these seven groups, all required:

| Group | Covers |
|---|---|
| `generation` | `netCapacityFactor`, `physicals.generationGwh` |
| `price` | `ppaPrice`, `ppaTenorYears`, `countryBaseloadPrice`, `captureFactor`, `physicals.achievedPrice` |
| `capex` | `capitalStructure.totalCapex`, `cashFlow.capex` |
| `opex` | `opexPerKwYear`, `incomeStatement.opex` |
| `debtTerms` | `seniorDebt`, `maxGearing`, `assumptions.debtRate`, `assumptions.debtTenorYears`, `debtSchedule` |
| `grid` | `gridSecured` |
| `om` | `omContracted` |

### 8.1 `estimateBasis`

Ordered from hardest to softest. This is a closed vocabulary; anything else is a validation error.

| Value | Meaning |
|---|---|
| `contracted` | A signed contract fixes it. Changing it means amending a contract. |
| `binding_offer` | A counterparty has offered it in writing, not yet signed. A term sheet, a valid EPC offer. |
| `engineering_estimate` | A quantified study specific to this site — a yield study, a BoP take-off. |
| `benchmark` | Taken from comparable assets, not from this one. |
| `internal_model` | Produced by the house model rather than observed. |
| `placeholder` | A stand-in. The number is there to make the file well-formed, not because anyone believes it. |

### 8.2 `confidence`

`high` | `medium` | `low` — the analyst's own judgement of the range around the number, independent
of basis. A `contracted` price can still be `medium` confidence if volumes are uncertain.

### 8.3 What this looks like for an early-stage project

A greenfield project two years from ready-to-build will typically carry `generation` at
`engineering_estimate`/`medium` or `benchmark`/`low`, `price` at `internal_model`, `capex` at
`benchmark`, `debtTerms` at `internal_model`, and `grid` at `placeholder` with `gridSecured: false`.
That is a legitimate, loadable file. The construction-stage asset next to it will carry `contracted`
across most groups.

### 8.4 Provenance never blocks

**A weak provenance block is reported, never rejected** (A-7, Q-3, Q-4). It feeds:

- the cross-file dispersion report (§11);
- the detail sheet, so a committee can see which numbers are contracts;
- the house model's variance report, which reports "our model says X, your file says Y" and lets the
  analyst's file stand.

Gating on provenance would make the house model authoritative again and defeat the input design.

---

## 9. The reject-derived-fields rule

**A file carrying any of these is rejected at load**, naming the offending key:

`irr` · `moic` · `terminalValue` · `exitValue` · `payback` · `paybackYear` · `minDscr` · `lcoe` ·
`leverage` · `gearing` · `equity` · `capexPerKw` · a top-level scalar named `capex` ·
`cashflowSchedule`

Two distinct reasons, both load-bearing:

1. **Mandate dependence.** IRR, MOIC, terminal value, payback and min DSCR all depend on the
   mandate's hold period and on the portfolio-level assumption set. §8 marks them *derived* and says
   they "must not be stored as inputs"; §5.1 makes the hold period a live slider that "sets the exit
   year for every IRR and MOIC on the page". A stored IRR is a number computed under somebody else's
   mandate, and shipping one means the slider silently stops working. The same applies to any cache
   of them not keyed by `holdYears`.
2. **Redundancy.** `equity`, `leverage`, `gearing`, `capexPerKw` and `lcoe` are each one arithmetic
   step from fields that are already present. Two sources for one number is one source too many, and
   the one that drifts is always the stored one. `minDscr` is additionally a *reduction* over
   `ratios.dscr` with a documented exclusion (the ramp year), which belongs with the consumer.

`capex` as a **scalar** is rejected for the naming collision in A-2 as well: `capex` is the annual
cash-flow line, and `totalCapex` is the project total.

---

## 10. Plausibility checks

**Warning, never blocking** (A-7). These say a file looks unusual, not that it is incoherent. A
blocking rule here would let one stale file stop all work. Every band below lives in the assumption
set, not in code.

| Check | Rule |
|---|---|
| Capacity factor | `0.05 ≤ netCapacityFactor ≤ 0.60` |
| Cost per kW | `totalCapex × 1e6 ÷ (capacityMw × 1000)` inside the technology band: solar **560–950**, onshore wind **1,050–1,700**, offshore wind **2,200–3,400** €/kW |
| Gearing | `seniorDebt ÷ totalCapex ≤` the stage ceiling: construction **75%**, ready-to-build **72%**, greenfield **65%** |
| Declared gearing | `seniorDebt ÷ totalCapex ≤ maxGearing` |
| Tax | `taxExpense ≈ taxRate × max(0, pbt)`, year by year |
| DSCR sizing | min DSCR over the debt life, excluding the ramp year, within **1.20–2.50** — the band D-3's stabilised-EBITDA sizing produces |

**Ceiling checks carry the tolerance.** `≤` here means `≤ ceiling × (1 + tolerance_rel)`. A file
whose gearing is exactly at its ceiling will straddle it once the statements are rounded to the
file's precision: Almonte's 0.72 reads as 0.7200000062 from the committed rounded figures. Comparing
a rounded quotient to an exact ceiling would warn on a file that is right.

---

## 11. Cross-file dispersion

The loader reports the distribution of each **declared** assumption across the pipeline —
`taxRate`, `debtRate`, `debtTenorYears`, `depreciationYears`, `captureFactor`,
`countryBaseloadPrice` by market — with count, median, and the files at each tail.

This is a first-class feature, not plumbing. 300 independently-authored models only aggregate into
something a committee can trust if the loader proves each one coherent and surfaces disagreement
between them; the dispersion report replaces the consistency that central derivation used to
guarantee. It warns; it does not block (A-7).

---

## 12. Worked example — Almonte Solar

[`../templates/project-template.json`](../templates/project-template.json) in full. 180 MW solar PV,
Spain, ready-to-build, COD 2028, base year 2027.

**Scalars.** `netCapacityFactor` 0.230324 · `opexPerKwYear` €12.813182/kW · `ppaShare` 0.55 ·
`ppaPrice` €34/MWh for 10 years · `countryBaseloadPrice` €58/MWh · `captureFactor` 0.68 (capture
price €39/MWh) · `developmentRiskScore` 2.7 · `totalCapex` €103.322563m (€574.0/kW) · `seniorDebt`
€74.392246m · `maxGearing` 0.72.

**Gearing binds.** Sizing to a 1.40× DSCR on an 18-year annuity at 5.5% off stabilised first-full-
year EBITDA (D-3) would support more debt than the 72% ready-to-build ceiling allows, so the ceiling
binds and gearing lands at exactly 0.72. Min DSCR over the debt life, excluding the 2028 ramp year,
is therefore **1.65×**, not 1.40× — which is the point of D-3: a level annuity against a varying
EBITDA profile gives a varying DSCR.

**Funding**, per [§6](#6-the-construction-funding-convention):

| Year | `capex` | `equityDrawdown` | `debtDrawdown` | `fcfe` |
|---|---|---|---|---|
| 2027 | 28.930318 | 28.930318 | 0 | −28.930318 |
| 2028 (COD) | 74.392246 | 0 | 74.392246 | −1.680524 |
| 2029 | 0 | 0 | 0 | 3.731327 |

2028 is negative despite the debt draw because it is the ramp year: generation is 199.75 GWh against
361.36 GWh in 2029, so EBITDA of €4.934m does not cover €6.615m of debt service. Its DSCR is 0.746×
— reported, and excluded from the minimum.

**Every tie-out in [§7](#7-tie-outs) holds**, maximum residual `1.2e-05` against a €0.01m tolerance.
The residuals are rounding, not error: the file carries six decimals.

---

## 13. The spreadsheet layout

[`../templates/project-template.xlsx`](../templates/project-template.xlsx) is the analyst-facing
form of this same schema — **identical field names, so there is one schema rather than two**. Issue
#8 owns the converter that reads it and the content thereafter.

| Sheet | Layout |
|---|---|
| `Identity` | Two columns: the JSON path (`asset.capacityMw`) and the value. Covers `id`, `name`, `location`, `asset`, `revenue`, `execution`, `capitalStructure`. |
| `Assumptions` | Two columns, same shape, for the `assumptions` block. |
| `Provenance` | One row per provenance group: group, `estimateBasis`, `confidence`, `note`. |
| `Statements` | **Line items as rows, the 30 years as columns.** Column A is the JSON path (`incomeStatement.ebitda`), column B the unit, columns C…AF the years, header row carrying `years`. Blocks appear in file order: physicals, income statement, cash flow, debt schedule, balance sheet, ratios. |
| `TieOuts` | One row per check in [§7](#7-tie-outs), each a **live formula** against `Statements`. Each per-year cell holds the **breach over tolerance** — `max(0, |residual| − max(0.01, 0.001 × |reference|))` — so it applies both limbs of the §7 tolerance year by year and points at the failing year. Zero means inside tolerance. The workbook proves itself. |

An empty `dscr` cell means `null`. Everything else is a number.

The `TieOuts` sheet checks DSCR **coverage** as well as its value: a blank cell inside the debt life,
or a filled one outside it, is a FAIL, derived from `codYear` and `debtTenorYears` rather than from
whether the cell happens to be empty. A workbook that shows PASS is a file the loader will accept.
