# Decisions

Spec deviations, resolved ambiguities and open questions for TerraFolio v1.

**How to use this document.** Later issues **append** to the numbered lists below; they do not
restructure them. Every entry keeps its number for the life of the project, so `D-3`, `A-7` and
`Q-2` are stable references. When you resolve something the specification was silent or ambiguous
about, add an entry to [Resolved ambiguities](#resolved-ambiguities) and a line to the
[Log](#log). When an open question is answered, leave it in place and record the answer under it.

Section numbers cited as §n refer to [`spec.md`](spec.md) unless stated otherwise.

---

## Departures from the specification

Three deliberate, measured departures. Two of them are the difference between an optimiser that
converges and one that returns garbage while appearing to converge. **Do not "fix" these back to
what the specification says.** If you think one is wrong, raise it on the epic (issue #1) rather
than changing it.

### D-1 — The spec's GA initialisation does not scale (amends §10.1)

§10.1 says "random with 35% inclusion probability". That is calibrated for the mockup's 48-project
pipeline, where 35% ≈ 17 projects ≈ the right size for €1,200m of equity. At the 300–500 candidates
this application targets, 35% inclusion selects 105–175 projects needing many times the budget.
Every chromosome lands in the budget-rejection band, the landscape is flat, tournament selection
becomes a coin flip, and the run returns garbage while *looking* converged.

Measured at 500 candidates, Standard 90×60:

| Configuration | Best fitness | Projects | Equity | Capacity |
|---|---|---|---|---|
| Spec as written (p=0.35, no repair) | **−28.77** | 128 | €10,524m | 21,514 MW |
| Capital-aware init + budget repair | **+7.88** | 11 | €1,154m | 1,661 MW |

Against a €1,200m budget and a 1,500 MW target; the objective's reachable maximum is ≈9.7.

**Required.** Scale the inclusion probability from

```
p = min(capacity_target ÷ Σ MW, available_capital ÷ Σ equity)
```

rather than a constant, and add a **budget repair operator** that drops holdings until a chromosome
fits its equity budget. Both preserve determinism. The spec's 0.35 survives as a documented ceiling.

**Status:** requires explicit sign-off (amends §10.1). See [Q-1](#q-1--the-three-departures-need-sign-off).

### D-2 — The rejection score must sit below every feasible score (amends §10.2)

§10.2 says a portfolio whose equity exceeds available capital is "rejected before scoring".
Implemented literally as a single constant, over-budget chromosomes become indistinguishable and
selection has nothing to work with — so the score must be **graded** in the overshoot. But the
mockup's `-20 - equity/capital` is the wrong grade: the worst *feasible* portfolio scores about
**−44**

```
3.2·(−0.6) + 3.0·(−0.5) + 2.6·(−1.2) − 7·0.85 − 14 − 8·0.65 − 8·0.85 − 1.6·2.6
```

which is why §10.2 sets the empty portfolio at −50. At −20, **an infeasible portfolio outranks a
feasible one**.

**Required.**

```
reject = −1000 − 100 × (equity ÷ capital − 1)
```

Always below −50, monotone in the overshoot, so it grades without ever beating a feasible portfolio.

**Check order in the objective:**

1. Empty portfolio first — it has equity 0 and so is "feasible", and must score exactly **−50**.
2. Then the graded reject, with a **€1 tolerance** on the cap so a portfolio landing exactly on it
   is not rejected by a rounding artefact. The utilisation reward actively pushes portfolios onto
   that boundary, so this tolerance is load-bearing, not defensive.

**Status:** requires explicit sign-off (amends §10.2). See [Q-1](#q-1--the-three-departures-need-sign-off).

### D-3 — Debt sizing moves from the engine to the generator and validator (affects §9.3)

With statements ingested, gearing and DSCR come from each file, so the spec's ambiguity — whether an
18-year 1.40× sculpt sizes off minimum or stabilised EBITDA — no longer determines the product's
numbers. It still matters twice: the **seed generator** must pick a basis, and it sets the
**plausibility band** the validator warns against.

**Required.** Use **stabilised first-full-year EBITDA**, as the JS reference does.

Sizing off the minimum over the debt life pins min DSCR at exactly 1.40 for a third of the pipeline
and puts nothing below the 1.25 default floor — which would leave §5.2's DSCR slider a dead control
over most of its range, §7.4's "red below the mandate floor" never firing, and §7.1's worst-DSCR
tile a constant. A level annuity against a varying EBITDA profile gives a varying DSCR by
definition, so "sculpted" here means *sized*, not *shaped*.

**Status:** requires explicit sign-off (affects §9.3). See [Q-1](#q-1--the-three-departures-need-sign-off).

---

## What statement ingestion costs

The input model inverts the specification's §8/§9 arrangement. §8 has the application derive every
financial from a small set of drivers; instead, **the pipeline is a directory of ~300 project files,
one per park**, each carrying that project's full 30-year financials as modelled by the analyst who
follows it. These are consequences of that choice, not defects — but §9.3 and §9.4 no longer read as
written.

- **`capex`, `seniorDebt`, `gearing` and `minDSCR` describe what the analyst assumed**, not what
  asset quality supports. §9.3's claim that "portfolio leverage becomes an outcome of asset quality"
  no longer holds. The minimum-leverage constraint still discriminates between portfolios; it just
  discriminates between *declared* capital structures.
- **The assumption set narrows** to what is genuinely portfolio-level: exit multiples, the LCOE
  discount rate, the nine objective weights, the risk-appetite caps, the CO₂ factor, validator
  tolerances and generator parameters. §9.4's "all rates, escalators, tenors, tax rates, capture
  factors and exit multiples are configuration" still holds for everything the application computes;
  per-file rates are now *declared inputs* that the dispersion report surfaces rather than
  configuration the investment team turns. See [A-8](#a-8--the-narrowed-assumption-set).
- **The mandate's hold-period slider still works**, because returns are computed at run time from
  each file's FCFE series plus a terminal value. Nothing mandate-dependent is ever stored in a file
  — see the reject-derived-fields rule in [`pipeline-schema.md`](pipeline-schema.md).
- **The tie-out validator and the cross-file dispersion report are first-class features**, not
  plumbing. 300 independently-authored models only aggregate into something a committee can trust if
  the loader proves each one coherent and surfaces disagreement between them. They replace the
  consistency that central derivation used to guarantee.
- **The project financial model is not discarded.** It moves from source of truth to two jobs: it
  **generates** the 300-file seed pipeline so the application ships with real, internally consistent
  data, and it **checks** ingested files, powering the plausibility warnings and the "our model says
  X, your file says Y" variance report.

---

## Resolved ambiguities

Numbered `A-n`, append-only. Each records what was ambiguous, what was decided, and why.

### A-1 — Statement line items are €m; the physicals identity needs an explicit ÷ 1e6

*Raised by issue #3. Affects: #2, #6, #8, #4.*

The tie-out as written in the epic and in issue #3 reads

```
revenue = generationGwh × 1000 × achievedPrice
```

which is dimensionally **euros**, not €m: 386 GWh × 1000 = 386,000 MWh, × €60/MWh = €23,176,000.
Every other statement line is €m (epic §5 fixes files at €m and GWh), so the identity as written is
off by a factor of 10⁶ against the rest of the file.

**Decided.** The rule is stated with units explicit and the conversion where it belongs:

```
revenue_€m = generationGwh × 1000 × achievedPrice_€/MWh ÷ 1e6
```

and correspondingly `opex_€m = capacityMw × 1000 × opexPerKwYear_€/kW ÷ 1e6`. This is a units
clarification consistent with epic §5, not a change of rule: no check is weakened and the tolerance
is unchanged. The JS reference already divides by `1e6` at both points.

### A-2 — `capex` is a cash-flow line item; the scalar is `totalCapex`

*Raised by issue #3. Affects: #2, #6, #8.*

§8 lists `capex, capexPerKw` as derived **scalars**, while the tie-out table uses `capex` as an
annual cash-flow line (`Σ capex = totalCapex`, `fcfe = … − capex + debtDrawdown`). One name, two
meanings, in the same normative table.

**Decided.** `capex` is **only** the annual capital-expenditure line inside
`statements.cashFlow`. The project-level scalar is **`totalCapex`**. A file carrying a top-level
scalar named `capex` is rejected at load, both because of this collision and under the
reject-derived-fields rule. Consequently the funding tie-out reads
`Σ equityDrawdown = totalCapex − seniorDebt`, not `capex − seniorDebt`.

### A-3 — `Σ depreciation = totalCapex` is false for a late-COD asset; use the PP&E residual

*Raised by issue #3. Affects: #2, #6, #8.*

The tie-out `Σ depreciation = capex over depreciationYears` holds only when the whole depreciation
life falls inside the 30-year window. With `baseYear` 2027, 30 years runs 2027–2056; a 25-year life
from a COD of 2033 (§5.2 allows COD up to 2033) runs 2033–2057 and loses its last year. The check as
written would fail every such file, or — worse — invite an implementation that silently truncates
depreciation to make it pass.

**Decided.** The exact rule, which always holds, is

```
Σ depreciation = totalCapex − ppe[last]
```

with `Σ depreciation = totalCapex` **iff** `codYear + depreciationYears ≤ baseYear + 30`. The PP&E
roll-forward `ppe[t] = ppe[t−1] − depreciation[t] + capex[t]` carries the residual by construction,
so this is strictly stronger than the original — it pins the unamortised balance as well as the
total — and it is not a weakening of the check.

### A-4 — Construction funding: equity pro rata during build, debt drawn at COD

*Raised by issue #3. Affects: #6, #8, #4.*

§9.3 says debt is "drawn at COD" and "equity funds the construction period pro rata across the years
to COD", but the JS reference never books `capex` as a line at all — it books the equity outflow
directly. The statement rendering therefore has to be chosen, and the choice changes the FCFE
*timing* (and so every IRR), not just its total.

**Decided.** For a project with `buildYears = max(1, codYear − baseYear)`:

| Year | `capex` | `equityDrawdown` | `debtDrawdown` |
|---|---|---|---|
| `t < codYear` | `(totalCapex − seniorDebt) ÷ buildYears` | same | 0 |
| `t = codYear` | `seniorDebt` | 0 | `seniorDebt` |
| already operating (`codYear ≤ baseYear`) | `totalCapex` in year one | `totalCapex − seniorDebt` | `seniorDebt` |

Every funding tie-out then holds by construction — `Σ capex = totalCapex`,
`Σ debtDrawdown = seniorDebt`, `Σ equityDrawdown = totalCapex − seniorDebt` — and

```
fcfe = ebitda − interestPaid − debtRepayment − taxPaid − capex + debtDrawdown
```

reproduces §9.4's "FCFE = EBITDA − interest − principal − tax, less construction equity draws"
identically, because `− capex + debtDrawdown = − equityDrawdown` in every year.

Verified against the JS reference on Almonte Solar: `max |statement_fcfe − reference_fcfe| = 5.3e-15`
over all 30 years. The already-operating row is what §13's "project already operating in the base
year — entire equity outflow booked in year one; no construction draw-down" requires.

### A-5 — §5.4 warning order follows the specification, not the mockup

*Raised by issue #3. Affects: #5, #10, #11.*

§5.4 lists the feasibility warnings "in this order of severity". The mockup emits them in a
different order (capacity shortfall, capital absorption, solar mix, leverage, locks).

**Decided.** The **specification's order is normative** and is what
[`ui-contract.md`](ui-contract.md) pins:

1. no candidate passes the screens — the run button is disabled;
2. eligible capacity is below the capacity target;
3. the minimum leverage exceeds what the eligible pool can support;
4. the solar target is more than 20 points away from the eligible pool's own mix;
5. the eligible pool absorbs less than 90% of available capital;
6. projects are locked or excluded — an informational count.

The mockup's *strings* are kept verbatim; only the ordering is corrected. All but the first are
advisory: the user is allowed to run an infeasible-looking mandate and see how close the optimiser
gets.

### A-6 — The two portfolio cash-flow series are named apart on the wire

*Raised by issue #3. Affects: #7, #9, #11.*

The 30-year chart/CSV series and the hold-truncated IRR series are both "the portfolio cash flow",
and conflating them is the single most likely silent bug in the feature: one carries a terminal
value and the other must not.

**Decided.** They never share a name. [`api.md`](api.md) pins:

- **`cashflow30Y_m`** — exactly 30 elements, €m, **no terminal value**. Drives the §7.2 chart, the
  §7.1 "30-year FCFE" tile and `cashflow.csv`.
- **`cashflowHold_m`** — exactly `holdYears` elements, €m, **terminal value added into the final
  element**. Drives portfolio IRR and MOIC only.

Neither is ever derived from the other by slicing.

### A-7 — Outlier declared assumptions warn; they never block a run

*Raised by issue #3, per epic §12 Q3 and Q4. Affects: #6, #8, #9.*

**Decided (recommended default applied).** Plausibility checks and the cross-file dispersion report
**warn**; only tie-out failures block a file from loading. The house model's variance report is
reported alongside the analyst's file and never gates ingestion.

Blocking on an outlier would let one stale file stop all work, and gating on the house model would
make it authoritative again — which is precisely what the input design set out to change. See
[Q-3](#q-3--do-outlier-declared-assumptions-block-a-run-or-only-warn) and
[Q-4](#q-4--should-the-house-models-variance-report-gate-ingestion).

### A-8 — The narrowed assumption set

*Raised by issue #3, per epic §2. Affects: #2, #6, #8.*

With statements ingested, the assumption set narrows to what is genuinely portfolio-level. It holds,
and only holds:

| Group | Contents |
|---|---|
| Exit | Exit EV/EBITDA multiples by technology |
| Discounting | The LCOE discount rate (6% real) |
| Objective | The nine §10.2 weights, the capacity floor, the split divisor, the IRR clamp |
| Risk appetite | The pre-screen caps and the portfolio-average penalty caps for Low / Balanced / High |
| Environmental | The CO₂ avoided factor (0.32 t/MWh) |
| Validation | Tie-out tolerances, plausibility bands, stage gearing ceilings |
| Generator | Entry yields, €/kW bands, capture factors, escalators, degradation, ramp fraction, debt terms |

Per-file rates — each project's own tax rate, debt rate, tenor and depreciation life — are
**declared inputs** carried in the file's `assumptions` block, not configuration. The dispersion
report surfaces disagreement between them.

Every rate, weight, floor, clamp, tolerance and band in the table above lives in the assumption set
and **never** as a code constant (§10.2, §9.4): changing one must be an auditable event.

### A-9 — `spec.md` is extracted verbatim, including the source's own glyph quirks

*Raised by issue #3. Affects: #6, and anyone citing §10.2.*

`tools/extract_spec.py` emits exactly what the PDF's `/ToUnicode` CMaps say, with no substitution,
because the alternative — a table of "corrections" — is a place for silent edits to hide and would
break byte-identical reproducibility the moment the table changed.

One consequence is visible. §10.2's two concentration-penalty rows read

```
Ʃ max(0, country share − cap)
```

where `Ʃ` is **U+01A9 LATIN CAPITAL LETTER ESH**, not `Σ` U+03A3. That is what the design tool
embedded. Read it as a summation; do not treat it as a defect in the extractor, and do not
"correct" `docs/spec.md` by hand — it is regenerated, and `tools/extract_spec.py --check` will fail.

The extractor makes exactly three structural transformations, all mechanical: wrapped lines are
rejoined (closing a hyphen or en dash broken across a line), raised runs become `^exponent`, and the
running header and footer are dropped by font size.

### A-10 — §7.1's compliance colour and §12's colour-only ban are reconciled by a second channel

*Raised by issue #3. Affects: #5, #10, #11.*

§7.1 says each headline tile is "coloured to indicate compliance". §12 requires "no colour-only
status encoding". Read literally these conflict, and the conflict recurs at the holdings table's
"red below the mandate floor" (§7.4) and at the map's solar-versus-wind markers (§7.3).

**Decided.** Colour stays, as an *additional* channel. Every status also carries a mark or a word:
`✓` or `!` on a tile sub-label, a trailing `!` and a spoken `below the {n}× floor` on a breaching
DSCR cell, `Not selected` in a shaded row's accessible name, technology in each map marker's
accessible name. [`ui-contract.md` §7.1](ui-contract.md#71-colour-is-never-the-only-signal) lists
every place and its required second signal.

Note that the palette contains no red. "Red below the mandate floor" renders in
`--color-accent-800`, the alert tone, plus the mark.

### A-11 — the mockup's search-screen copy does not conform to §14 and is replaced

*Raised by issue #3. Affects: #5, #10, #11.*

§14 removes algorithm-internal vocabulary — population, crossover, mutation, fitness, generation —
from user-facing copy, confining it to technical documentation and the run record. The mockup's
screen 02 is written entirely in it: "Searching the solution space", "Population of 90 candidate
portfolios, tournament selection, uniform crossover, 2.5% mutation", "Fitness convergence",
"GENERATION 07 / 60", "crossover and mutation · pruning infeasible portfolios".

**Decided.** The mockup is the source for *layout and geometry*, not for this screen's words.
[`ui-contract.md` §4](ui-contract.md#4-screen-02--search) carries a replacement for every instance,
and §8 carries the banned and preferred vocabularies. "Generation" becomes **round**, "fitness"
becomes **mandate score**, "population mean" becomes **average of all candidates**.

The run record and the SSE event names keep the technical terms: they are not user-facing, and
renaming them would make the engine harder to reason about for no gain.

### A-12 — statements are always euros; `currency` is the revenue currency

*Raised by issue #3, in review. Affects: #2, #6, #8, #9.*

Q-8's first answer — "statements are denominated in the file's own `currency`, and non-EUR files are
only ever screened out" — does not survive contact with §5.3. **The EUR-only toggle defaults to
off.** So a Polish project loads, passes the screens, and enters a portfolio whose aggregates are
plain sums; its PLN-denominated statements would be added to euro statements as though they were
euros. The default path through the product produces a portfolio whose capex, equity and cash flows
are the sum of incomparable numbers, and nothing in the UI would show it.

**Decided.** Every statement line in every file is in **euros**. §3 scopes v1 to a single-currency
EUR pipeline and §15's first open question confirms non-EUR markets are "screened in or out, not
hedged with a modelled cost" — there is no conversion step anywhere in the design, so there is
nothing that could make a non-EUR statement comparable.

`currency` keeps its name and its place in the file, redefined precisely: it is the currency the
project's **revenue** is earned in. That is what §5.3's control actually says — "EUR-denominated
**revenue** only" — what §8 means by "an ISO currency code driving the execution screens", and what
§7.5's drawer renders as `hedge required`. It is a risk flag, not a unit.

The alternative, converting at load, was rejected: it needs an FX curve per market per year, which
§3 puts explicitly out of v1, and it would make every run depend on a rate nobody recorded.

### A-13 — one base year across the pipeline, enforced at load

*Raised by issue #3, in review. Affects: #2, #6, #8, #9.*

`baseYear` was validated per file — each file's `years` must run `baseYear … baseYear + 29` — with
no rule tying files to each other. But the portfolio aggregates the 30-element arrays **by
position**. Two valid files based in 2027 and 2028 would have their 2027 and 2028 figures summed
together, and every portfolio cash flow, exit year and return would mix calendar years, silently.
`GET /pipeline` compounds it by exposing a single `baseYear` for the whole set.

**Decided.** The pipeline base year is the `baseYear` **every** loaded file shares. Disagreement
fails the load as a whole — not file by file — reporting every declared base year with its file
count and ids, so an analyst can see whether one file is stale or a re-basing is half-finished. The
full distribution rather than a majority: a half-migrated pipeline splits evenly and has no majority
to report. Re-basing is the
analyst's job; the loader never shifts a series to make it fit.

This is the one declared assumption that is not merely reported by the dispersion report. Every
other field in the `assumptions` block may legitimately vary between files, and the report surfaces
the spread; a varying base year is not a disagreement about modelling, it is a broken index.

### A-14 — a stored run carries its own candidate snapshot

*Raised by issue #3, in review. Affects: #7, #9, #11.*

The run result carried only the selected projects, in the sixteen holdings-table columns. Three
things on the portfolio screen need more than that, and all three break once the pipeline moves —
which §13 explicitly anticipates ("stored runs keep their snapshot"):

- §7.4's *Show all candidates* toggle shows the **eligible** set, "so the user can see what the
  optimiser rejected". Merging against the live pipeline cannot reproduce it.
- §7.3's map needs `lat`/`lon` per selected site; nothing in the result carried coordinates at all,
  so the map could not be drawn from a stored run even immediately after it finished.
- §7.5's drawer needs capture price, PPA terms, opex, payback and the debt terms.

**Decided.** `holdings` carries **every project in the run's eligible candidate set**, each with the
full per-project scalar record plus `selected` and `locked`. `holdings.csv` is that array filtered
to `selected` and projected onto the sixteen columns, so the table and the export still cannot
disagree. About 400 KB at 500 candidates, on an endpoint fetched once per result — the opposite
trade to `GET /pipeline`, made for the same reason stated there, and it is what §11's "a run ID
reopens the exact result" costs.

---

## Open questions

Numbered `Q-n`, append-only. Each carries a recommended default that has been applied, so a
different answer is a configuration change rather than a rewrite. Raise one on the epic (issue #1)
if your issue depends on it.

### Q-1 — The three departures need sign-off

[D-1](#d-1--the-specs-ga-initialisation-does-not-scale-amends-101),
[D-2](#d-2--the-rejection-score-must-sit-below-every-feasible-score-amends-102) and
[D-3](#d-3--debt-sizing-moves-from-the-engine-to-the-generator-and-validator-affects-93) amend
§10.1, §10.2 and §9.3 respectively. All three need explicit sign-off.

**Default applied:** all three as specified above.

### Q-2 — What ingestion costs

Leverage and DSCR now describe analyst assumptions rather than derived asset quality, and per-fund
rate configuration narrows. These are consequences of the input model, not defects, but §9.3 and
§9.4 no longer read as written. Confirm the trade is accepted.

**Default applied:** accepted; see [What statement ingestion costs](#what-statement-ingestion-costs)
and [A-8](#a-8--the-narrowed-assumption-set).

### Q-3 — Do outlier declared assumptions block a run or only warn?

**Recommended:** warn, plus the dispersion report. Blocking would let one stale file stop all work.

**Default applied:** see [A-7](#a-7--outlier-declared-assumptions-warn-they-never-block-a-run).

### Q-4 — Should the house model's variance report gate ingestion?

**Recommended:** no. Report the variance, let the analyst's file stand. Gating would make the house
model authoritative again and defeat the input design.

**Default applied:** see [A-7](#a-7--outlier-declared-assumptions-warn-they-never-block-a-run).

### Q-5 — Exhaustive at 2,000 candidates

Is there a hard target? §12 wants 2,000 candidates but budgets only Standard-at-500. Exhaustive
160×110 at 2,000 candidates currently measures 30 s.

**Default applied:** none — no target is asserted. Closing the gap is issue #12's repair-masking
work, not a redesign.

### Q-6 — Brownfield assets with debt already in place

These cannot be modelled from the template as drafted. §3 scopes v1 to greenfield / ready-to-build /
construction so it does not arise.

**Default applied:** out of scope for v1. v1.1 would need an `existingDebt` block and a validator
branch.

### Q-7 — Shareability versus role-based visibility

§11 calls a run id shareable; §12 demands role-based visibility by fund. These conflict.

**Recommended:** resolve toward the security requirement. Authentication is not otherwise in this
backlog; if it is a v1 gate it needs its own issue in group 3.

**Default applied:** the run id is treated as an internal identifier, not a bearer token. No
authentication is implemented in this backlog.

### Q-8 — Currency

Confirm that a non-EUR file's statements are denominated in that currency — and therefore only ever
screened out — rather than pre-converted to EUR by the analyst.

**Default applied, corrected:** statements are **always in euros**; `currency` records the revenue
currency and drives the §5.3 screen and the hedging flag. The first answer here — statements in the
file's own currency — was wrong, because that toggle defaults to *off*, so non-EUR files would enter
portfolios unconverted. See [A-12](#a-12--statements-are-always-euros-currency-is-the-revenue-currency)
and [`pipeline-schema.md` §2](pipeline-schema.md#2-units-and-conventions).

---

## Log

| Date | Issue | Entry |
|---|---|---|
| 2026-09-21 | #3 | D-1, D-2, D-3 recorded from epic §6; Q-1…Q-8 recorded from epic §12 with defaults applied. |
| 2026-09-21 | #3 | A-1 — physicals identity carries an explicit ÷ 1e6. |
| 2026-09-21 | #3 | A-2 — `capex` is a cash-flow line; the scalar is `totalCapex`. |
| 2026-09-21 | #3 | A-3 — `Σ depreciation = totalCapex − ppe[last]`. |
| 2026-09-21 | #3 | A-4 — construction funding convention fixed and verified to 5.3e-15. |
| 2026-09-21 | #3 | A-5 — §5.4 warning order follows the specification, not the mockup. |
| 2026-09-21 | #3 | A-6 — `cashflow30Y_m` and `cashflowHold_m` named apart on the wire. |
| 2026-09-21 | #3 | A-7 — plausibility and dispersion warn; only tie-out failures block. |
| 2026-09-21 | #3 | A-8 — the narrowed assumption set enumerated. |
| 2026-09-21 | #3 | A-9 — `spec.md` is verbatim; §10.2's `Ʃ` is U+01A9 in the source. |
| 2026-09-21 | #3 | A-10 — compliance colour always carries a mark or a word as well. |
| 2026-09-21 | #3 | A-11 — the mockup's search-screen copy is replaced to conform to §14. |
| 2026-09-21 | #3 | A-12 — statements are always euros; `currency` is the revenue currency. Corrects Q-8. |
| 2026-09-21 | #3 | A-13 — one base year across the pipeline, enforced at load. |
| 2026-09-21 | #3 | A-14 — a stored run carries its own candidate snapshot, with coordinates. |
| 2026-09-21 | #3 | `POST /mandate/preview` returns `runnable: false` for exactly the two conditions `POST /optimisations` answers with 422. |
| 2026-09-21 | #3 | `GET /pipeline` carries `debtRate` and `debtTenorYears`, and the statements endpoint carries the file's `assumptions`, for §7.5's drawer. |
| 2026-09-21 | #3 | The workbook's tie-outs apply both limbs of the tolerance per year, and check DSCR coverage rather than assuming a blank cell is correct. |
| 2026-09-21 | #3 | `GET /pipeline` carries an **abbreviated** `provenance` — basis and confidence, no notes. Measured: notes are 654 of the block's 1,093 bytes per project. |
| 2026-09-21 | #3 | `api.md` payload sizes restated from measurement (400 KB scalars, 1.8 MB statements at 300 projects) rather than estimate. |
| 2026-09-21 | #3 | `_m` marks an amount in €m; a per-unit price (€/MWh, €/kW) carries its unit in the noun and takes no suffix. |
| 2026-09-21 | #3 | Holdings sort keys are `holdings` field names, not the mockup's internal state names. |
| 2026-09-21 | #3 | `holdings.csv` has its own seventeen-column list; §7.4's sixteen are a screen layout, not a column set. |
| 2026-09-21 | #3 | Half-up rounding is implemented explicitly on both client and server; neither language gives it by default. |
| 2026-09-21 | #3 | A base-year disagreement reports the full distribution, not a majority — a half-migrated pipeline has none. |
| 2026-09-21 | #3 | `tools/extract_spec.py` validates the extracted document's own invariants; `--check` alone only catches drift from a file it produced. |
