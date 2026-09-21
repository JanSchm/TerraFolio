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

### A-15 — the pages are classic scripts, because a module cannot load over `file://`

*Raised by issue #5. Affects: #10, #11.*

§12 requires the pages to open from disk with no server. Issue #5 also describes the JavaScript as
"plain ES modules loaded directly". These conflict: every browser CORS-blocks `<script type=
"module">` and every module `import` from a `file://` origin, whose origin is opaque. Verified in
Chrome rather than assumed — a module script on a `file://` page fires `error` with no message and
never executes.

**Decided.** §12 wins; "loaded directly" is honoured in the sense that matters, which is that
nothing is bundled. `format.js`, `feasibility.js` and `controls.js` are classic scripts attaching to
one `window.TerraFolio` namespace, each ending with a CommonJS tail so bare node can require them.
`feasibility.js` resolves its one dependency as `typeof require === 'function' ? require(
'./format.js') : window.TerraFolio.format`, so it still imports nothing but `format.js` and still
runs under bare node, which is what #12 needs to check it against #6's Python.

`fetch()` is blocked from `file://` for the same reason, so `web/public/countries-110m.json` cannot
be read from disk by the map. `tools/vendor.mjs` generates `countries-110m.js` beside it — the same
data as a classic script — from the same source.

**#11 writes seven more page scripts and must follow the same convention**, or the pages stop
opening from disk.

### A-16 — a control emits a wire value; its label is a separate input

*Raised by issue #5. Affects: #10, #11.*

Two controls were built emitting their own display labels: the stage chips emitted
`Ready-to-build` where every consumer screens on `ready_to_build`, and the risk-appetite control
emitted `Balanced` as the key into `assumptions.risk_caps`. Neither fails loudly — the first
empties the pool, which reads as a mandate that is merely too tight.

**Decided.** Every control that offers a choice takes `values` (canonical, what it emits) and
`labels` (what the user reads) as separate inputs. `chipGroup` and `segmented` both expose
`label(value)`, and neither renders a raw value.
[`api.md` §6.1](api.md#61-the-mandate-object) is the authority for the names; the mandate this
front end emits matches it field for field.

### A-17 — every rate and share in the mandate is a fraction

*Raised by issue #5. Affects: #11.*

The mockup stores `hurdle: 11` and `minLev: 60` as whole percent but `solarShare: 0.45` as a
fraction, then divides at each comparison. Mixing the two conventions in one object is how a
factor-of-100 error gets written, and one was: the percentage spinners emitted `35` where the
sliders emitted `0.35`, so a 35% merchant cap would have reached the objective as 3500% — which
does not fail, it silently disables the constraint.

**Decided.** Fractions throughout, matching [`api.md` §6.1](api.md#61-the-mandate-object) and its
rule that the wire never carries a percentage. The native range and number inputs still work in
whole percent, because that is what a user types; each control divides on the way out.

### A-18 — feasibility recomputes on `input`, not `change`

*Raised by issue #5. Affects: #11.*

[`ui-contract.md` §3.4](ui-contract.md#34-footer--live-feasibility) budgets the footer at under
100 ms precisely so it can be live. The DOM `change` event does not fire while a slider is being
dragged or a number typed without blurring, so the footer would have sat stale through the whole
interaction.

**Decided.** Ranges and number fields dispatch on `input`; radios stay on `change`, which is when a
radio changes. Measured: the client-side computation is 0.205 ms at 500 candidates and 0.450 ms at
2,000, so the budget is not the constraint — the event is.

### A-19 — the split bar's focus ring is drawn by the bar, not by its slider

*Raised by issue #5. Affects: #10.*

The technology-split control is a transparent `<input type="range">` stretched over a painted bar,
which is what makes it drag like a bar while staying a real slider. `opacity: 0` also makes its
`:focus-visible` outline transparent, so the control was keyboard-operable with no visible focus at
all.

**Decided.** The visible bar takes the ring, through a `has-[:focus-visible]` treatment on the
wrapper. Any control drawn this way owes the same.

### A-20 — the ported palette drops the mockup's bare accent

*Raised by issue #5. Affects: #10, #11.*

The mockup's `--color-accent` (`#5980a6`) measures **3.71:1** on the page background and is used
for links, ghost buttons, outline chips and the primary button's label — all below the 4.5:1 floor.
Micro-labels at 55% ink measure 3.64:1, table headers at 60% ink 4.25:1, control borders at
`neutral-400` 1.79:1 against a 3:1 requirement.

**Decided.** The ladder is ported exactly as issue #5 specifies; only the role-to-token mapping
moves, which is what #5's own semantic mapping already says — `accent-700` for active data ink
(5.78:1), `neutral-700` for muted body (5.87:1), `neutral-600` for control boundaries (3.82:1).
**The ported system therefore has no bare `--color-accent` and no `accent-2` ladder.**
`accent-400` keeps its mandated value as the wind tone and gains an `accent-700` hairline, so the
shape has a 3:1 boundary without a new colour. All 29 pairs are asserted in
`web/tests/contrast.test.js`, which also fails if either value reappears.

This is the mechanism A-10 asks for, applied to the palette rather than to the status marks.

### A-21 — an unlevered project passes the DSCR screen

*Raised by issue #5. Affects: #6, #9.*

[`api.md` §2](api.md#2-get-pipeline) gives `minDscr` as `number | null`, null "where the project
carries no debt". §5.2's DSCR screen is written as a floor, and `null >= 1.25` is false in both
JavaScript and Python, so the naive reading silently screens out every unlevered project.

**Decided.** A null `minDscr` **passes**. A project with no debt has no debt service to fail to
cover; rejecting it would drop the safest assets in the pipeline for having no risk to measure,
and the mandate's minimum-leverage constraint is the control that actually expresses a preference
against them.

`web/js/feasibility.js` implements it and `#6` must match, because
[`api.md` §5](api.md#5-post-mandatepreview) requires the client and the preview endpoint to agree
field for field.

### A-22 — the solar-mix warning is one-sided

*Raised by issue #5. Affects: #10, #11.*

[`ui-contract.md` §3.5](ui-contract.md#35-warning-strings) gives the trigger as the eligible solar
share being "more than 20 points from the target", which reads as symmetric. The string it pins is
not: *"Solar target of {solar}% may be unreachable: eligible pool is {poolSolar}% solar."*

**Decided.** The test fires only when the pool has **less** solar than the target, which is the
mockup's own condition. A pool with far more solar than the target can still reach it by selecting
fewer solar projects, so the target is not unreachable and the message would be wrong. A pool with
far less cannot reach it at all.

Raise it on #3 if the symmetric reading was intended; the change is one comparison.

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

---

## 1C — the JavaScript reference as golden fixtures

These concern `tools/extract_reference.mjs` and the fixtures under `tests/golden/fixtures/`.
Figures were measured by running the extracted reference. Numbering is 1C-n; the D-, A- and
Q- series above are #3's.

### 1C-1 · Construction funding: capex and the debt drawdown are booked pro-rata
>
> **Superseded by 1C-16.** `docs/pipeline-schema.md` §6 fixes the funding convention, and 1C now
> follows it: equity pro rata through construction, debt drawn in one go at COD. The reasoning
> below stands — it is why the tie-out was broken and what had to be true of any fix — but the
> schedule it describes (capex pro-rata with the drawdown spread alongside) is no longer what is
> emitted.

**Context.** The issue names this: the reference "books capex and the debt draw in different
years", so `Σ debtDrawdown = seniorDebt` and the debt roll-forward do not both hold as written.

More precisely, the reference books *neither*. Its cash flow carries only the equity draw, spread
evenly over `buildYears = max(1, COD − 2027)`; capex and the debt drawdown appear nowhere, and the
debt simply exists at its full amount from COD.

**Decision.** Book capex pro-rata across exactly the years the reference draws equity, and set
`debtDrawdown_t = capex_t − equityDrawdown_t`. Because both are spread on the same schedule this
reduces to `seniorDebt / buildYears` per year, so `Σ drawdown = seniorDebt` exactly and the
roll-forward `closing = opening + drawdown − principal` holds in every year.

**Why this and not something else.** It is the only reconstruction that makes the funding tie
without touching the economics. The equity cash-flow series is the reference's `cfEquity`
**byte-identical** — verified with `Object.is` across all 48 × 30 values — so every IRR, MOIC and
payback derived downstream is unchanged. Capitalising interest during construction would have
changed capex and broken the €7,673.072m total.

**Consequence.** Zero IDC: no interest accrues before COD, matching the reference. Interest in the
COD year accrues on the balance *after* that year's drawdown, which matters only for the ten
projects whose COD is 2027 — the reference draws and charges a full year's interest in the same
year. The tie-out is stated as `interest = (opening + drawdown) × debtRate` for exactly this reason.

### 1C-2 · Ramp-year shortfall: distributions and equity support are split
>
> **Superseded by 1C-16.** The schema's `cashFlow` carries a single signed `fcfe` and no
> `distributions`/`equitySupport` pair, and §3 makes unknown keys a validation error, so the split
> is not representable. 1B was asked about this on #3 before the template was fixed and chose the
> single signed line. The underlying fact — post-COD cash flow is negative in exactly the ramp year,
> for all 48 projects — is unchanged and still asserted by the tool.

**Context.** Post-COD operating FCFE is negative in the ramp year for **all 48 projects** — 55% of
full generation against a full year of level debt service and tax. It is negative in no other year,
in any project.

**Decision.** Split operating FCFE into `distributions ≥ 0` and `equitySupport ≥ 0`, with
`fcfe = distributions − equityDrawdown − equitySupport`.

**Why.** A single signed distributions line would show every project paying a negative dividend in
its first operating year, which is not what a statement means. The split says what actually
happens — the sponsor funds a ramp-year shortfall — while leaving signed FCFE exactly as the
reference computes it. `equitySupport` flows to share capital, so the balance sheet still balances
and the project cash account stays at zero throughout.

**Open to 1B.** If `templates/project-template.json` prefers a single signed line, this is a
one-line change. Raised on #3.

### 1C-3 · No tax-loss carryforward, preserved

The reference computes `tax = max(0, (EBITDA − interest − depreciation) × 20%)` with no
carryforward: a loss year is untaxed and never relieved later. **126 project-years across all 48
projects** have profit before tax below zero, so this is material, not a curiosity.

Preserved as-is rather than "improved". Changing it would change every IRR and break parity with
the reference, which is the entire point of these fixtures. Each file states
`taxRate` and `depreciationYears` explicitly, and a provenance note records the absence, because a
port written by someone who knows project finance will otherwise implement a carryforward.

### 1C-4 · Minimum DSCR: both the raw value and the reference's rounded one are emitted

The reference stores `round(min(minDSCR, 3.2), 2)` and screens eligibility on that. The cap is
inert for this corpus — the highest min DSCR is 2.13 — but the 2 dp rounding is not: it decides
whether a project at 1.2449 clears a 1.25 floor.

Files therefore carry `minDSCR` (raw, which ties out against the debt schedule exactly),
`minDSCRReference` (what the screens compare), and `minDSCRBindingYearAge`.

**Why the binding year is recorded.** "Sculpted to 1.40×" does not mean the minimum is 1.40. The
binding year is the first full year for only **34 of 48** projects; for the other 14 it is age 9,
11, 14 or 17, because contracted revenue escalates at 0.5% while solar generation degrades at 0.5%
and opex escalates at 2.1%, so EBITDA declines through the PPA period and steps up at rolloff.
Min DSCRs run 1.24–2.13. A port that assumes sculpting pins year one at 1.40 will look correct on
34 projects and be wrong on 14.

This also confirms the epic §6.3 departure empirically: sizing off stabilised first-full-year
EBITDA gives a DSCR distribution with real spread, including one project below the 1.25 default
floor — which is what keeps §5.2's DSCR slider a live control.

### 1C-5 · Currency: the labels are preserved, the statements are not converted

Fourteen of the 48 projects carry a non-EUR label (PLN, RON, DKK, SEK, GBP) but **all their
statements are EUR-denominated** — the reference has no FX anywhere.

The label is preserved because §5.2's "hedged only" screen depends on it. A provenance note in
every file records that the statements are EUR regardless.

When 1C raised this it was epic open question 8, and #3 has since resolved it as **A-12**:
statements are always euros, and `currency` is the revenue currency — an execution-screen input and
a hedging flag, never a denomination. That is exactly what these files carry, so the concern is
closed and the 14 non-EUR labels are correct rather than a deviation.

### 1C-6 · `aggregate()` folds an undefined IRR to zero — recorded, not copied

The reference computes `irrW += (this.projIrr(p, s.hold) || 0) * p.equity`. An undefined IRR
becomes `0` and is then weighted into the portfolio average, directly contrary to the epic's
invariant that undefined IRR is NaN/null and is *excluded* from every weighted average.

**This path is live, not theoretical.** At hold 10 no project has an undefined IRR, but the hold
period is a user control over 5–30 years, and undefined IRRs appear at the short end:

| Hold | Undefined IRRs, across exit-multiple bases 8.5 / 9 / 9.5 |
|---|---|
| 5 | 24 of 144 |
| 6 | 7 of 144 |
| 7 and above | 0 |

The fixtures record per-project IRR with `null` left as `null`, and `objective_cases.json` includes
a hold-5 mandate so the coalescing path is covered by cases rather than left to be discovered. 2A
should implement the invariant, not the reference, and expect a deliberate divergence from
`objective_cases.json` at holds 5 and 6.

### 1C-7 · The reject score: reference and epic §6.2 amendment are recorded side by side

Every over-budget case in `objective_cases.json` carries both `fitnessReference`
(`−20 − equity/capital`, what the reference computes) and `fitnessAmendedPerEpic6_2`
(`−1000 − 100 × (equity/capital − 1)`).

Recording only the reference's value would give 2A an oracle that contradicts the epic; silently
recording only the amended one would mean the fixture no longer describes the reference. Both,
clearly labelled, is the honest option. 2A should assert the amended value.

### 1C-8 · "Exit multiples 9.0/8.5/9.5" is emitted under all three readings

The issue asks for IRR at hold 10 "with exit multiples 9.0/8.5/9.5". Those three numbers are also
exactly what the reference derives from a base of 9 (solar `base`, wind `base − 0.5`, offshore
`base + 0.5`), so the phrase reads either as one multiple set or as three bases.

`derived_expectations.json` gives every figure under bases 8.5, 9 and 9.5, each recording the
technology-specific multiple actually applied. Both readings are satisfied and the ambiguity
disappears.

### 1C-9 · Full IEEE-754 precision everywhere; values the reference rounds carry a raw companion

No emitted number is rounded. These fixtures are an oracle for a Python reimplementation; rounding
to a display precision would cap what the Python tests can assert at a level that hides real
errors, and would put a slack term into every tie-out far larger than the model's own ~1e-12
residuals.

Byte-identity is not at risk: `Number.prototype.toString` is specified as shortest-round-trip, and
Python's `json.load` recovers the identical double.

The exception is values the **reference itself** rounds — min DSCR to 2 dp, LCOE to an integer.
Those are stored exactly as the reference produced them, with the unrounded companion alongside, so
the rounding step is a testable transformation rather than an invisible one.

### 1C-10 · LCOE and IRR are excluded from the statement files

The epic's invariant is that nothing mandate-dependent is stored. IRR, MOIC, terminal value and
payback are excluded for that reason, and a check over every emitted file enforces it.

LCOE is excluded on a related ground: it turns on the 6% real discount rate, which the epic places
in the assumption set, so storing it in a file would make an assumption-set change fail to
propagate. It lives in `derived_expectations.json` instead, with its methodology recorded — the
reference's LCOE discounts *unescalated* opex from the model base year rather than from COD, and
leaves capex undiscounted, all of which a reimplementation will otherwise "fix".

### 1C-11 · The GA trace is a reference-semantics oracle, not a production target

`ga_trace_fast_seed42.json` traces the reference's genetic algorithm: 35% inclusion probability at
initialisation (spec §10.1 as written) and no repair operator. The production GA departs from both
per epic §6.1 and must not be expected to reproduce this trace.

It remains worth having: it pins the PRNG consumption order, the tournament and crossover
mechanics, the elitism, and the sort stability that a port has to get right regardless of which
initialisation it uses. The reference seeds from `Date.now()`, so the trace is produced by
overriding `seeded` on the instance after construction — seed 42 — rather than by editing
reference source.

The GA is **driven, not transcribed**: the tool stubs the timer functions and pumps the reference's
own `run()`. Transcribing it would have put the project's most intricate parity target behind a
hand-copied loop.

### 1C-12 · Two dead branches this corpus cannot exercise

`ppaTenorYears` is 10, 12, 15 or 20 for all 48 projects — never 0 or 1. The reference's
merchant-only pricing branch (`ppaTenor > 1` false) and its `ppaShare > 0.05` tenor draw are
therefore never exercised by these fixtures. Recorded in every file's provenance so that nobody
treats a green golden run as evidence those branches work.

Separately, `capexPerKW` is clamped to its entry-pricing band for **28 of 48** projects (25 at the
floor, 3 at the cap), so "capex falls out of the revenue case" is literally true for only 20. Each
file records `capexPerKWClamp` and `debtSizingBasis` (27 gearing-capped, 21 DSCR-sculpted).

### 1C-13 · The extracted modules are `.mjs`, and the Node floor is enforced

The extracted reference is ESM. Emitting it as `component.js` left its module classification to
Node's module-syntax detection, which is version-dependent and can be switched off — under
`--no-experimental-detect-module` the import fails with
`Named export 'defineComponent' not found`. Both documented commands depended on an undeclared
Node behaviour.

The modules are now `component.mjs` and `harness.mjs`, which is unambiguous on every Node that
supports ESM at all, and the tool asserts a Node 18 floor rather than leaving it implicit. The
alternative — adding a `package.json` with `"type": "module"` — was rejected: `package.json` is not
in 1C's ownership, and dropping one into `tests/golden/fixtures/` would change how Node resolves
every future `.js` file under that directory for other issues.

### 1C-14 · Regeneration is transactional

The tool used to build straight into the live fixture directory, and `loadReference()` writes the
extracted modules before anything has been validated. A failure after that point left new modules
paired with old fixtures and an old manifest — a tree that is internally inconsistent while its
manifest claims it is intact. Writing was also overwrite-only, so a renamed or removed artefact
lingered: a project whose slug changed would appear twice and a directory scan would load both.

The tool now stages the complete set in a sibling directory, validates it there, and only then
swaps it in, reporting any stale files it removed. Staging is a sibling rather than `os.tmpdir()`
so the rename stays on one filesystem and cannot fail with `EXDEV`. A failed build leaves the
committed fixtures byte-identical; a successful one leaves nothing stale behind.

### 1C-15 · A second GA trace with locked projects

The reference's `run()` forces locked genes to 1 at initialisation and on every child. With an
empty lock list that operator is the identity, so a single unlocked trace cannot distinguish a
correct port from one that dropped lock enforcement entirely — and "locked projects survive the
run" is a user-visible promise (§7.4's lock control), not an internal detail.

`ga_trace_fast_seed42_locked.json` locks two projects **chosen from the set the unlocked run
rejected**, worst IRR first, so they are demonstrably projects the objective does not want.
Fitness falls from 7.220 to 3.483, which the tool asserts — if locking projects the optimiser
rejected did not cost anything, the locks would not be binding and the trace would prove nothing.

Two assertions cover the two halves of the operator, verified by neutering each in turn:
initialisation forcing is caught by the initial-population reconstruction, and crossover forcing by
the requirement that every locked id appears in the best selection of every generation. The force
operator and both of its call sites are also pinned as extraction integrity markers.

`locked` is now carried in every emitted mandate. It was previously omitted because `fitness()`
never reads it — but a fixture should describe the whole mandate, not the part that happens to
reach the objective function.

### 1C-16 · Conformance to 1B's closed template

`templates/project-template.json` and `docs/pipeline-schema.md` landed in #3 after 1C's first
version was built against an interim shape. 1C now emits 1B's shape exactly. Three of that
schema's rules drove the rewrite:

**§3 — the template is closed.** "Unknown keys at any level are a validation error, not a warning."
So the richer balance sheet (ten lines) and extra ratios 1C carried are not smuggled in as
extensions. `balanceSheet` is `ppe` alone and `ratios` is `dscr` alone, as the schema specifies;
§5.6 records that a fuller balance sheet is deliberately deferred.

**§9 — the reject-derived-fields rule.** 1C's earlier files carried `minDSCR`, `gearing`,
`capexPerKW` and `equityEURm`, every one of which is on the reject list. Those files would have
been **rejected at load**, not merely reshaped. They are each one arithmetic step from a field that
is present, and the rule is right: two sources for one number is one source too many.

Nothing is lost, it moves to where it belongs. `derived_expectations.json` — 1C's own oracle, not a
pipeline file — now carries `minDSCRRaw`, `minDSCRReference`, `minDSCRBindingYearAge`, `gearing`,
`equityEURm`, `capexPerKW`, `capexPerKWClamp`, `debtSizingBasis`, `lcoeEURPerMWh` and the nominal
capture factor. A port can check its own derivation against them without any of them being stored
in a file.

**§6 — the construction-funding convention.** Equity pro rata through construction, the whole
facility drawn at COD, everything in year one for an already-operating asset. This replaces 1C-1's
schedule and is better: the reference starts charging interest on the full facility at COD, so
drawing it there is what the debt schedule actually describes. Interest in the COD year accrues on
`opening + drawdown`. Signed FCFE is unchanged, so every IRR downstream is unchanged.

The emitted files now match 1B's worked example field for field, and all 48 share exactly one key
structure. The tool compares against `templates/project-template.json` in both directions when it
is present, and says loudly when it is not rather than passing silently.

### 1C-17 · `captureFactor` is emitted effective, not nominal

Schema §4.3 states `capture price = countryBaseloadPrice × captureFactor`. The reference **rounds**
its capture price to a whole €/MWh — `Math.round(58 × 0.68) = 39` for Iberian solar — and the
achieved price, revenue and EBITDA are all built on the rounded 39.

Emitting the nominal factor would leave that identity false: `58 × 0.68 = 39.44`, off by 1.13%,
outside the €0.01m / 0.1% tolerance. It is not one of §7's blocking tie-outs, so such a file would
load — and then anyone deriving a capture price from it would get a number that does not reproduce
the revenue sitting next to it.

1C emits the **effective** factor, `capturePrice ÷ countryBaseloadPrice` (0.672414 for Iberian
solar), so the identity holds exactly for all 48. The nominal factor is recorded in
`derived_expectations.json`.

This is the one field where 1C's files differ from 1B's worked example, which carries 0.68 against
an achieved price built on 39 and is inconsistent on that point. Raised on #3: either the schema
should say the capture price is rounded and the factor effective, or the seed generator should stop
rounding. 1C cannot stop rounding without changing the reference's economics.

### 1C-18 · `fcfe` is accumulated the reference's way, not §7.3's

§7.3 states `fcfe = ebitda − interestPaid − debtRepayment − taxPaid − capex + debtDrawdown`, and §6
notes that `−capex + debtDrawdown` is exactly `−equityDrawdown`.

1C uses the `equityDrawdown` form. The §7.3 form subtracts the entire facility and adds it straight
back in the COD year — algebraically nil, numerically lossy, costing the low bits of a small
result. Computed literally it put `fcfe` 5e-15 away from the reference's own series and broke the
`Object.is` assertion that is 1C's strongest integrity check.

The `equityDrawdown` form is bit-identical to the reference, and §7.3's form still holds to 4.97e-14
across all 48 × 30 — eleven orders of magnitude inside its tolerance. Both are true; only one is
exact.

### 1C-19 · Provenance for machine-generated files

Schema §8 requires `preparedBy`, `preparedOn`, `modelVersion` and an `estimateBasis`/`confidence`/
`note` for each of seven field groups — analyst metadata, for files an analyst wrote. These files
were not written by an analyst.

So they say so: `preparedBy` is `tools/extract_reference.mjs`, `modelVersion` is
`js-reference@<bundle sha prefix>`, and every note states that the figure comes from the JavaScript
reference model. `preparedOn` is a fixed date — the date 1C first extracted the fixtures, never
`new Date()`, because the emitted bytes must be identical on every machine.

`estimateBasis` and `confidence` follow what the reference actually models, which does vary by
stage: grid connection, O&M contracting, contracted revenue share and entry pricing all move with
it. The result carries the gradation §8.3 describes — 17 greenfield projects at `internal_model` /
`low` for capex and debt terms, 11 construction projects at `binding_offer`, `grid` at
`placeholder` for the 11 without a secured connection — so a consumer displaying provenance has
something real to display, and the fixtures exercise the vocabulary rather than flat-lining it.

### 1C-20 · The project-file contract is a parameter until the epic settles it

1A's pydantic `ProjectFile` and 1B's `docs/pipeline-schema.md` landed incompatible. **1B's own
`templates/project-template.json` fails 1A's model with exactly the six errors 1C's files did**, so
this is a disagreement between those two contracts rather than a defect in either's
implementation of one. Raised on #1; no resolution yet.

Two disagreements, and after investigation only one survives:

1. **The technology enum** — `solar` (schema §4.2) against `solar_pv` (1A's model). Genuinely
   arbitrary, genuinely irreconcilable: one file cannot carry both.
2. **Five `assumptions` fields** — `degradationRate`, `priceEscalation`, `merchantEscalation`,
   `opexEscalation`, `targetDscr`, required by 1A and absent from 1B's template. Since §3 makes
   unknown keys an error, no single file satisfies both **as they stand** — but every value is in
   the reference, so this is a gap 1B can close by adding five fields rather than a conflict.

So 1C does not guess. `--contract=<name>` selects one, the committed fixtures are emitted under
`DEFAULT_CONTRACT`, and settling the question is a one-constant change:

| Contract | Emits | Verified |
|---|---|---|
| `pipeline-schema-1.0` *(default)* | 1B's enum, no extra assumptions | 48/48 against `templates/project-template.json`, key for key |
| `domain-model-1a` | 1A's enum, five extra assumptions | **48/48 against 1A's pydantic `ProjectFile`** |
| `reconciled` | 1B's enum, five extra assumptions | 26/48 against 1A — only the 22 solar projects fail, on the enum alone |

The default is `pipeline-schema-1.0` because the epic calls that document Normative and puts `docs/`
and `templates/` in 1B's ownership row, so where the two merely differ in spelling, 1B's wins by
construction.

The closed-template check is 1B's oracle, so it governs `pipeline-schema-1.0` and is skipped — with
its reason printed — under any other contract. A non-default contract cannot overwrite the committed
fixtures; it requires `--out`.

**What this reduces the disagreement to.** `domain-model-1a` validating 48/48 and `reconciled`
failing only on `asset.technology` together prove the entire remaining gap is one enum value. Every
other difference is now closed.

### 1C-21 · Escalation is a rate in a file, a multiplier in the arithmetic

Emitting the escalators, 1C first wrote the reference's literals — `1.005`, `1.021` — and 1A's model
rejected them: `Input should be less than or equal to 0.25`. It is right, and so is schema §2:
"shares and rates are fractions of one, never percentages". Every other rate in the file already
reads that way — `taxRate` 0.2, `debtRate` 0.055, `degradationRate` 0.005.

Files carry `0.005` and `0.021`; the arithmetic keeps `1.005` and `1.021`, exactly as the reference
writes them. Both are declared as literals and the tool asserts `1 + rate === multiplier` at
startup, because deriving one from the other gives `1.021 - 1 = 0.020999999999999908` — not the
number anyone means, and not one that would survive a validator's bounds check cleanly.

**If 1B adds the five fields, they should be rates.** That is what 1A expects and what §2 already
requires of everything else in the block.

---

---

## 1A — the file schema, the domain model and the assumption set

*From issue #2. Entries are numbered `C-n`, as posted in the `CONTRACT` comment on #1, so
they do not collide with 1B's `D-n`/`A-n`/`Q-n` or 1C's `1C-n`.*

### Schema decisions

1A owns the project-file schema under the rewritten epic §8: the pydantic models
in `src/terrafolio/domain/` are its executable definition, and
`docs/pipeline-schema.md` documents what they land. The baseline is 1B's
document as written — `execution` rather than a risk block, `provenance.fields`
by seven groups, the `ppe`-only balance sheet, `UK` as an alias of `GB`, and
A-1 through A-4 — with four changes.

None of the four alters the meaning of a financial value, adds or changes a
tie-out identity, or touches a sign convention. Two changes that *would* have
are raised as open questions below rather than taken.

#### C-1 — `asset.technology` is `solar`, reversing 1A's own `solar_pv`

**Reversed.** 1A briefly spelled this `solar_pv`, on the grounds that the value
names a technology rather than a resource and is symmetric with `onshore_wind`
and `offshore_wind`; spec §7.4 writes the three as "Solar PV, onshore wind,
offshore wind".

The argument for making the change was explicitly a cost one — one line each in
`docs/pipeline-schema.md`, the template, 1C's emitter and 1D's adapter, "and
more every day after that". That premise stopped being true: #3, #4 and #5
merged on `solar` before picking the change up, so the cost moved from one line
each to regenerating a verified 48-file corpus in another issue's ownership row.
Insisting on the better spelling at that price is not a trade worth making, and
§8's ownership rule is a tie-breaker for genuine disagreements rather than a
licence to impose a spelling after the cost has moved.

1C reached the same place independently and built the escape hatch: a
`--contract` parameter with three settings, and a recommendation of
**`reconciled`** — 1B's enum spelling with 1A's five `assumptions` fields
(1C-20). That is what is implemented. The five fields stay, for the reason 1C
gives better than the original entry did: a validator that wants to *reproduce*
the physicals rather than take them on trust needs the degradation rate and the
three escalators, and `targetDscr` records the sizing basis epic §6.3 fixes.

Verified: 1C's 48 golden files emitted under `reconciled` all validate against
`ProjectFile` **and round-trip byte-identically**. The remaining action is one
constant — `DEFAULT_CONTRACT` in `tools/extract_reference.mjs` — in 1C's row.

The Python enum member is `Technology.SOLAR`, matching its value.

#### C-2 — three declared escalators added to `assumptions`

`priceEscalation`, `merchantEscalation`, `opexEscalation`; fractions, domain
−0.10 … 0.25.

Epic §2 and issue 2A both specify a cross-file dispersion report covering
"the three escalators", and the schema as landed carried none of them. Nothing
else in a file lets a consumer recover them, so the report could not have been
built as specified. They are declared inputs and participate in no tie-out. The
reference's values are 0.005 contracted, 0.021 merchant, 0.021 opex.

#### C-3 — `assumptions.degradationRate` added

Fraction per year, domain 0 … 0.10. Issue 2C's variance report re-derives
generation from a file's declared assumptions and cannot do so without it.
Already present in 1C's posted field inventory.

#### C-4 — `assumptions.targetDscr` added

Domain 1.0 … 3.0. The declared debt-sizing basis, 1.40× in the reference, and
already in 1C's inventory. It also lets the §10 DSCR plausibility check compare
against the file's own declared target rather than only a global band; 1A adds
the field, 2A owns whether the check uses it.

#### C-5 — project ids must be zero-padded to a uniform width within a pipeline

Not a schema change — the id pattern is unchanged — but a determinism
requirement that nothing else states. Canonical order is plain lexicographic
`sorted(id)`, so `"P10" < "P9"`, and the GA's PRNG draws are indexed by
position: a pipeline mixing `P9` with `P10` reorders the population and changes
every result, silently.

`canonical_order()` in `domain/conventions.py` documents this rather than
repairing it. Sorting numerically instead would only move the problem to ids
that are not numeric, which the pattern permits. Generators zero-pad (1C's
`P01…P48`, 2C's `P001…P300`, both fine on their own); the loader is the right
place to reject a non-uniform set.

---

### Engineering decisions

#### C-6 — `domain` is split into a pydantic-free leaf and a pydantic shell

The epic's chain is `api → runner → optimiser → economics → pipeline → config →
domain`, and the numeric core may import "numpy, stdlib and `config/` only,
never pydantic". Those two statements conflict as written: `config` imports
`domain` to key its tables, so if `domain` is pydantic then `optimiser` imports
pydantic **transitively, through an edge the rules explicitly allow**, and a
per-file import check passes while the boundary is a fiction.

So `domain/enums.py`, `conventions.py`, `scalars.py`, `mandate_bounds.py` and
`file_bounds.py` are standard library only, and `project_file.py`, `mandate.py`,
`results.py`, `reduce.py` and `errors.py` are the pydantic shell.
`domain/__init__.py` stays empty of imports — a convenience re-export there
would execute on every `from terrafolio.domain.enums import ...` and undo the
split — and a test asserts it.

`tests/unit/test_import_boundaries.py` checks this three ways: per module,
transitively over the first-party graph, and at runtime in a child process that
poisons `pydantic` on `sys.meta_path`. Only the third catches a pydantic import
added to `domain.enums`.

#### C-7 — `AssumptionSet` is a tree of frozen dataclasses, not a pydantic model

Follows directly from C-6: `config` is imported by the numeric core, so nothing
on that path may touch pydantic. Issue 1A's "one frozen model" is satisfied by
`@dataclass(frozen=True, slots=True, kw_only=True)` plus a hand-written
`tomllib` loader.

The cost is hand-written parsing. It is repaid twice: `mypy --strict` needs no
plugin for the type the numeric core consumes, and the errors are better for a
file analysts edit by hand — `objective.risk_weight: missing key` rather than a
nested validation path.

#### C-8 — mandate steps are published as metadata, not enforced

Ranges validate; each step goes out as `multipleOf` JSON-schema metadata so the
controls and any generated client get it, and a mandate landing between steps
is accepted.

§5's steps describe slider affordances, not properties of a mandate. Three
reasons not to enforce them:

- The API is also a programmatic surface, and §12 promises regression tests
  across releases; a caller sweeping the IRR hurdle in 0.1 steps is legitimate.
- Float modulo does not work: `1.15 % 0.05` is `0.049999999999999906`.
- It would break run immutability. §11 promises a run id reopens the exact
  result; if a future release tightened a step, re-validating a stored
  `RunRecord` would make old runs unreadable.

Reproducibility is unaffected — the mandate is hashed exactly as submitted.
Quantisation buys tidiness, not determinism.

#### C-9 — `WarningCode` is an ordered `IntEnum` that crosses every boundary by name

The ordering is spec §5.4's severity ordering, ascending, so `sorted(codes)` is
display order and the most severe comes first. Values are banded with gaps, so
inserting a code never renumbers a neighbour.

The integer never leaves the process. `docs/api.md` pins
`{"code": "CAPACITY_BELOW_TARGET"}`, and the reason is durability rather than
readability: runs are immutable and addressable indefinitely (§11), so an
ordinal that shifted would silently rewrite the meaning of every run already
stored. `WarningCodeName` in `domain/results.py` serialises the name and
validates from it, and a test asserts no stored artefact contains the ordinal.

**The ordinal is refused on the way in as well.** Found in review: the first
version accepted `1010` and `"1010"` and resolved them through the enum, which
made the guarantee one-directional and worthless — a record written before a
code was inserted could be reinterpreted as a different warning after one was.

**`severity` cannot contradict its code.** It is a property of the code, not of
the occurrence; it appears on the wire because `docs/api.md` puts it there. It
may be omitted when constructing a warning in Python, in which case it is
derived, and a supplied value that disagrees is rejected. Otherwise a client
could render a blocking condition in an informational tone while the rest of the
system still treated it as blocking.

`WarningSeverity` has the two values `docs/api.md` allows, `alert` and `info`.
Blocking is not a severity: `WarningCode.disables_run` carries it, and it is
true for exactly `NO_CANDIDATES` and `LOCKS_EXCEED_CAPITAL` — the latter added
as a seventh code from §13 and `ui-contract.md` §3.6, where it is the 422 case.

#### C-10 — `assumption_set_id` digests the calibration values only

`sha256(canonical_json(values))[:16]`, with `[meta]` excluded. Fixing a typo in
a label must not invalidate the comparability of every run stored against that
calibration, and `label`, `version`, `created_by` and `supersedes` move nothing.

Two further properties, both tested:

- Hashed from the **normalised structure**, never the file bytes, so
  reformatting or adding a comment does not invent a new calibration.
- Every numeric leaf is normalised to a float first, so `9` and `9.0` are one
  exit multiple. `json.dumps` would otherwise write them differently. Booleans
  are excluded from that normalisation, because `isinstance(True, int)` is true
  and a flag hashed as `1.0` would both read wrongly and collide.

Integer-typed *fields* stay strictly integers in the file — `debt_tenor_years =
18.5` is a typo worth naming rather than a value worth rounding.

**Open, for #3/#9:** `docs/api.md` §6.2 shows `"assumptionSetId": "default-2026"`,
which is the file stem, while §8.3's run record carries both `assumptionSetId`
and `assumptionSetHash`. A stem does not change when someone edits the file,
which defeats the audit trail. `AssumptionSet` therefore exposes all three —
`name` (the stem), `assumption_set_id` (the content digest) and `content_hash`
(the full prefixed digest) — so the wire can be mapped either way without a
schema change. 1A's recommendation is that `assumptionSetId` carry the content
digest.

#### C-11 — `assumptions/` stays at the repository root and is copied into the wheel

The epic's file map puts it there and it is user-editable calibration: changing
a weight should be a reviewable diff, which it cannot be inside a package
directory. Files outside `src/` are not package data, so hatchling
`force-include` copies the directory to `terrafolio/_assumptions` at build time
— one source of truth in git, and an installed wheel that still works.

Resolution order in the loader: an explicit path, then
`$TERRAFOLIO_ASSUMPTIONS_DIR`, then upward from the working directory, then
upward from the installed package, then the packaged copy.

#### C-12 — undefined values become `None` at the pydantic boundary, once

The numeric core works in numpy and represents an undefined IRR as `NaN` with a
defined-mask. The conversion to `None` happens exactly where arrays become
models, and from there it serialises to JSON `null` and renders as an em dash.

To make that boundary enforceable rather than conventional, `allow_inf_nan` is
**off** on every model. It defaults to *on* in pydantic, which would let a file
carry `NaN` revenue — and once a NaN can mean "corrupt input" as well as
"undefined IRR", the distinction §13 requires is gone. A NaN reaching a result
model now fails at the boundary that owes the conversion, rather than becoming a
plausible zero three layers later.

#### C-13 — immutability reaches the contents, not just the attributes

`frozen=True` freezes attribute *assignment* and nothing more. Both container
kinds on these models needed closing:

- **Series are tuples, not lists.** `file.statements.cashFlow.fcfe.append(0.0)`
  would otherwise succeed on a "frozen" model, and the pipeline snapshot hash
  would stop describing what is in memory. Tuples serialise to JSON arrays
  identically and cost nothing.
- **Mappings are frozen after validation.** `provenance.fields`,
  `aggregates.countryShares` and `provenance.fileHashes` were plain `dict`s, so
  `file.provenance.fields.pop(...)` worked. They are now declared as
  `Mapping[...]` — so type checkers withhold `pop` and `__setitem__` — backed by
  a `mappingproxy` at runtime and serialised back to a `dict`. The two result
  mappings are also sorted on validation, because two identical results should
  not differ by insertion order.

Found in review; the first version of this issue claimed immutability it did not
have.

#### C-14 — numbers are validated strictly, not coerced

Pydantic's default lax mode accepts `"180"` where a `float` is declared, and
converts `true` to `1.0`. For a file an analyst writes by hand — or exports from
a spreadsheet, where a column formatted as text is an ordinary accident — that
turns a type error into a *plausible financial input* that satisfies every
tie-out.

All numeric fields on files, mandates and results are therefore strict. `int`
for `float` is still accepted, because JSON has one number type and `34` is how
a spreadsheet writes `34.0`; everything else is refused. Integer fields reject
`18.5`, `18.0` and `"18"` alike, and booleans reject `1` and `"true"`.

Model-wide `strict=True` was not used: it also rejects the string forms of
enums and dates, which are how `technology` and `preparedOn` legitimately
arrive.

#### C-15 — a flow is never negative; a balance is never *materially* negative

`docs/pipeline-schema.md` §2 states `opex`, `depreciation`, `interestExpense`,
`taxExpense`, `capex` and `debtRepayment` as positive magnitudes that carry
their sign through the identity consuming them, and the debt schedule as all
positive. Those lines now reject negatives, along with the physicals and the
PP&E balance.

This is worth enforcing at the schema rather than leaving to the tie-outs: the
identities are all linear, so a negative `opex` satisfies every one of them
while making EBITDA — and therefore the whole portfolio's earnings — look better
than it is. `revenue`, `ebitda`, `ebit`, `pbt`, `netIncome`, `fcfe` and `dscr`
stay signed, matching what the schema document annotates.

**Balances are not flows.** Applying the same rule to `debtSchedule.opening`,
`debtSchedule.closing` and `balanceSheet.ppe` rejected all 48 of 1C's golden
files — on arithmetic residue of about `-1.3e-13`. A flow is a quantity; a
balance is the running result of subtracting flows, so one amortised exactly to
zero lands either side of it. Those three carry a floor of
`-BALANCE_TOLERANCE_M` (1e-9 €m) instead: six orders tighter than the €0.01m
tie-out tolerance, so a balance that has genuinely gone negative is still
caught, and float noise is not mistaken for one.

Found by running the models against 1C's corpus rather than against the
template, which carries six decimals and hid it.

---

### The narrowed assumption set

Issue 1A asks for this to be recorded explicitly, and it is the consequence of
the input model rather than a defect.

With each project file declaring its own tax rate, debt rate, debt tenor,
depreciation life, escalators and degradation, **per-fund rate and escalator
configuration has narrowed**. §9.4's promise that rates and escalators are
configuration now applies to a smaller surface: what remains genuinely
portfolio-level is exit multiples, the LCOE real discount rate, the CO₂ factor,
the nine objective weights with every floor, clamp and normaliser, the GA
parameters, the risk-appetite pairs, the validator's tolerances and plausibility
bands, and the generator's parameters.

The per-project rates have not become unconfigurable — they have become *inputs*
that the investment team does not turn, which is what makes the cross-file
dispersion report load-bearing rather than a nicety (A-7, A-8).

---

#### C-16 — `holdings` is the run's own candidate snapshot

Conforming to 1B's A-14 rather than a decision of 1A's, recorded here because
1A's models changed shape after it landed. `holdings` carries **every project in
the run's eligible candidate set** — each with the full per-project scalar
record of `GET /pipeline`, plus `selected` and `locked` — not one row per
selected project projected onto the sixteen §7.4 columns.

The reason is §11's "a run ID reopens the exact result": §7.4's *show all
candidates* toggle, §7.3's map and §7.5's drawer all need more than the selected
rows, and none of them can be reconstructed by merging against a live pipeline
that has since moved. `ProjectScalars` models the shared record so `GET
/pipeline` and a stored holding cannot drift apart, and `Holding` is that plus
the two flags.

Three consistency rules come with it, all stated in `docs/api.md` §8.2 and none
of them previously checked: holdings are ordered by `id`, the `selected` flags
agree with `selectedIds`, and the two cash-flow series carry their documented
lengths — thirty years for `cashflow30Y_m`, the mandate's hold period for
`cashflowHold_m`. A run still in flight has no aggregates and is exempt.

---

#### C-17 — a result model is bound by the same domains as the file it describes

`ProjectScalars` re-states about twenty fields that `ProjectFile` already
declares, and the first version of it carried none of their constraints: a
stored holding could put a site at latitude 999, a contracted share at 7, an id
of `!!` or a `countryCode` of `Germany`. §7.3's map plots those coordinates and
the mandate's country screen matches that code, so the constraints matter as
much on the way out of the store as on the way in.

Both sides now draw their bounds from `domain/file_bounds.py`, and the shared
`Fraction`, `Magnitude` and `Positive` types in `domain/fields.py` carry the
repeated ones. The two provenance rules are the same on both sides too: all
seven groups required, and senior debt not above total cost.

Found in review, along with four consistency gaps in validators added the round
before — duplicate holding ids passing an ordering check that used `sorted`,
the selected-ids check short-circuited by `if self.holdings and`, completeness
read from `aggregates` rather than `status`, and a naive `createdAt` accepted
into an audit trail that is ordered by time.

#### C-18 — a guard that can be switched off quietly is not a guard

Three holes in the two guards, all found in review:

- The `# structural:` escape was matched as a substring of the raw line, so
  `S = "# structural: ..."` — or a docstring describing the rule — exempted its
  own line. It is tokenised now, and only a real comment counts.
- `scan_tree` decided tier A membership with `key.split("/")[0]`, so a module at
  `optimiser.py` rather than `optimiser/ga.py` was read as a package named
  `optimiser.py` and dropped out of the strictest tier. Issue 2A adds `cli.py`
  at the package root.
- Tier C called `int(value)` before testing finiteness, so `1e999` — which
  Python parses to `inf` — aborted the whole scan with `OverflowError` instead
  of reporting a finding.

And one in the import guard: `transitive_third_party` followed edges from the
starting package, but nothing names `terrafolio` as an import target, so a
third-party import added to `terrafolio/__init__.py` was invisible — although
every `import terrafolio.optimiser` executes it. The walk is seeded with the
package root now.

The two files also defined "the numeric core" twice, with different members.
The literal scan derives its set from the import rule's now, so the difference —
`pipeline` and `generate` consume the assumption set but may import pydantic —
is stated once and asserted.

#### C-19 — `nan` and `inf` are not calibration values

TOML has both as literals. A non-finite weight would propagate silently through
every fitness comparison, and it also made the assumption-set digest fail inside
`json.dumps` with `Out of range float values are not JSON compliant` — naming
neither the key nor the file, in a loader whose entire error contract is to name
the key path. Rejected now, before the digest, with the path.

Two neighbouring gaps closed with it: an empty market table loaded as a valid
assumption set, and a market with a capacity factor but no baseload price — or
the reverse — was accepted and failed as a `KeyError` mid-run.

---

### Open questions from 1A

Both add a line item that participates in a new tie-out identity, which the
rewritten §8 reserves for a human. Neither is blocking; the schema stands
without them.

#### N-1 — Should `incomeStatement.revenue` be split into contracted and merchant?

Tie-out would be `revenue = contractedRevenue + merchantRevenue`.

Issue 2A must compute contracted share "revenue-weighted over life, not simply
`1 − ppaShare`". With C-2's escalators present it *can* be re-derived from
declared assumptions, so this is not a blocker — but re-deriving means
reconstructing the whole price path inside `optimiser/`, across the import
boundary from the model that produced it, with its own rounding. Storing the
split costs two series and removes the duplication.

#### N-2 — Should the balance sheet be complete?

`cash`, `seniorDebtBalance`, `shareCapital` and `retainedEarnings` alongside
`ppe`, with `ppe + cash = seniorDebtBalance + shareCapital + retainedEarnings`.

1B deferred this deliberately and that is a defensible v1 scope. Raised only
because epic §2 says files carry a balance sheet and 1C reports it can already
emit one that balances to 4.8e-13 — so the cost looks close to zero, and the
"balance sheet" in a file is currently a single line.

## 2B — the run store and the CSV exports

Issue #7. The store behind §11's addressable run and §12's audit trail, and the
two exports §7.7 asks for.

### 2B-1 — the CSV cells carry raw numbers; §14 formatting goes in the header

Two landed documents disagree. [`api.md` §9](api.md#9-exports) says money
columns "carry raw numbers, not formatted strings — a CSV is for a spreadsheet,
not for reading". [`ui-contract.md` §2](ui-contract.md) says these numbers are
"formatted twice: client-side in `js/format.js`, and server-side for
`holdings.csv`". Issue #7 asks for §14 formatting and for "€ signs and
thousands separators intact" in Excel.

**Decided: raw cells, formatted header.** `api.md` is the contract the endpoint
implements and is specific to the export, so it wins for the cells. Three
reasons beyond precedence:

- A CSV cannot carry cell *formatting* at all. `€1,200m` in a cell is a string,
  and Excel imports it as text — so the column stops summing, which is the one
  thing a spreadsheet is for.
- #7's own acceptance criterion requires `cashflow.csv` totals to match
  `cashflow30Y_m`. Rounded display values cannot sum to the stored number; raw
  ones do, exactly.
- The euro signs and separators the criterion asks for **are** in the file, in
  the `#` comment lines carrying the run reference and the mandate, where a
  human reads them. The UTF-8 BOM is what keeps them intact in Excel, and
  `api.md` §9 already requires the BOM for exactly that reason.

The §14 half-up formatter is implemented and exported from `export/csv.py`, so
`ui-contract.md` §2's requirement is met where it applies — the header now, the
committee pack and #12's client/server parity tests next. Rounding is `Decimal`
with `ROUND_HALF_UP`: Python's `round` is half-even, so left to the default the
same stored run shows `1.13×` on screen and `1.12×` in its own export.

**`ui-contract.md` §2's sentence about `holdings.csv` needs narrowing to the
committee pack.** That file is #3's and then #10's; raised on #1 rather than
edited here.

### 2B-2 — `holdings.csv` has seventeen columns, not the eighteen in issue #7

Issue #7's scope says "emit **eighteen** columns, not §7.4's sixteen: split
project name, country and internal ID into their own columns".
[`api.md` §9.1](api.md#91-holdingscsv-columns) had already answered the same
question and got seventeen, by the same reasoning: of §7.4's sixteen, the first
is the lock control — a button rather than a value — and the second packs three
fields into one cell, so `16 − 1 + 2 = 17`. The Log already records it.

**Decided: seventeen, per `api.md`.** The wire contract names and orders them,
#9 serves them and #11 tests against them; forking it for one extra column
would be a contract change with no beneficiary. The issue's count appears to
predate `api.md` §9.1 rather than to disagree with it.

### 2B-3 — the run reference is an integer; `run_ref` is its label

§7.6 says "each run gets an incrementing reference used in the export", and
`RunRecord.run_ref` is a string whose only published example is `api.md` §8's
`"A-4"`. Nothing defines the letter.

**Decided.** `run_reference` is the monotonic integer and the ordering key for
every list query — never `created_at`, whose clock can skew between worker
hosts and silently reorder history. `run_ref` is `f"{series}-{reference}"`,
which reproduces `A-4` for run 4.

The series letter is a **store-generation marker**, fixed at `A` for v1, and it
is the primary key of the `run_sequence` table rather than a hardcoded prefix.
It exists so that a store rebuilt from a backup, or a reference that ever has
to be scoped per fund, can start a distinct series instead of minting labels
that collide with references a committee has already been shown.

### 2B-4 — the reference is claimed from a counter, not from `AUTOINCREMENT`

`POST /optimisations` answers **202 with a run id** before any result exists,
and a run still in flight answers `GET /optimisations/{id}` with a valid body.
So the queued `RunRecord` already carries its `runRef`, and the reference has
to exist *before* the row is written.

**Decided: a one-row `run_sequence` table, incremented by
`UPDATE … RETURNING` as the first statement of an IMMEDIATE transaction.** This
is a fifth table beyond the four #7 names, and it is worth it:

- `INTEGER PRIMARY KEY AUTOINCREMENT` can only report a value *after* the
  insert, which forces a nullable `run_ref` and `result_json` plus a second
  UPDATE — so the row would be briefly invalid, in a table whose whole point is
  that its rows are not.
- `MAX(run_reference) + 1` is correct only while nothing is ever deleted. That
  is enforced by a trigger, which is a policy rather than a law; a counter does
  not reuse a number even if the policy is someday relaxed.

Under WAL there is one writer at a time database-wide, and IMMEDIATE takes the
write lock at `BEGIN`, so the read-modify-write is serialised. A *deferred*
transaction would take its snapshot first and fail with `SQLITE_BUSY_SNAPSHOT`,
which the busy handler does not retry — so the store never uses one. Asserted
by four writer processes minting a contiguous 1…20.

### 2B-5 — a run carries its inputs; only its outcome is written later

`queued → running → succeeded | failed | cancelled` is a lifecycle, not
mutation, but everything fixed at submission is frozen by a trigger: the id,
the reference, the mandate, the lock and exclusion sets, the eligible set, the
seed, the pipeline hash and the assumption snapshot. Without that, finishing a
run could quietly re-point it at a different mandate and leave a stored pair
that is internally consistent and historically false.

**A second `finish_run` on a finished run raises** — unless the bytes are
byte-identical, which is a redelivered acknowledgement after a crash between
commit and reply, and is returned unchanged. Differing bytes mean two workers
believe they own the run, and that must reach a human rather than overwrite an
audit record.

`eligible_ids` is recorded at submission rather than derived from `holdings`
afterwards: the screens have already run by then (§13 makes `NO_CANDIDATES` a
422 at submission), min DSCR is a *derived* screen so the set is not
recoverable from the mandate, and the search indexes it by position.

### 2B-6 — the assumption snapshot is keyed on its own bytes, and both digests are kept

An assumption set is copied into the database on first use. The table is keyed
on the digest of **the payload stored here**, not on `assumption_set_id`,
because [C-10](#c-10) excludes `[meta]` from the loader's id by design — so
fixing a typo in a label yields the same id and different bytes. Keying on the
id would silently keep the first `[meta]` and misattribute every later run.

The payload's digest is **not** `assumption_set_hash`, by construction: the
dataclass tree flattens the TOML's nesting and the payload keeps `meta`. The
schema records `snapshot_hash`, `assumption_set_id` and `assumption_set_hash`
separately, under names that cannot be mistaken for one another, and a test
asserts the first differs from the third so that nobody later "fixes" them into
one value.

`dataclasses.asdict` cannot produce the payload — it deep-copies anything that
is not a dataclass or a builtin container, and `AssumptionSet`'s mappings are
`MappingProxyType`, which is unpicklable. The recursion is hand-rolled, and it
is also what renders an enum *key* as `solar` rather than `Technology.SOLAR`. A
test walks `dataclasses.fields` recursively and asserts no field is missing, so
a field added to the tree and forgotten by the serialiser cannot leave an audit
record that merely looks whole.

**Not** passed through `numbers_as_floats`: by the time an `AssumptionSet`
exists, int-ness is a typed fact, and `generations: 110.0` in an audit payload
would be both wrong-looking and lossy.

### 2B-7 — `pipeline_snapshot` records 2A's verdict without knowing its shape

#7 requires the "validation status of the loaded set", and #6 owns the loader
and its report type while running in parallel with this.

**Decided.** The store defines a four-value `ValidationStatus`
(`valid | warnings | invalid | unknown`) that answers only the question the
store has to answer — may a run cite this snapshot? — and keeps #6's report
beside it as an opaque JSON object it stores, checks for well-formedness and
never parses. Mapping one onto the other belongs above both, in `runner`.
`unknown` is the only status allowed to carry no report, so a caller written
before #6 lands is visibly uncertain rather than silently clean.

### 2B-8 — the base year lives on the snapshot, because nothing else carries it

`cashflow.csv` needs "one row per year from the base year". Neither
`RunRecord` nor `Mandate` carries a base year — only `GET /pipeline` does — and
[A-13](#a-13) makes it one value for the whole loaded set, enforced at load.

**Decided: `pipeline_snapshot.base_year`**, returned on `StoredRun`, so the
exports stay pure functions of a stored run. This is a gap in the wire contract
rather than in the store: `ui-contract.md` §5.2's thirty bars are "one per year
from the base year", so #11 cannot label the chart of a reopened run either.
Raised on #1 for #3 and #9; adding `baseYear` to `GET /optimisations/{id}` would
close it for everyone.

### 2B-9 — ULIDs on the standard library

No ULID package exists in the lock file, CI runs `uv sync --frozen`, and a new
dependency has to be argued on the epic first. The encoding is thirty lines,
and taking one would make every run's id depend on a library version that
nothing in the run record names.

Crockford's base32 drops I, L, O and U, so an id read aloud from a committee
pack cannot be retyped into a different run. Ids minted inside one millisecond
step the random component rather than redrawing it, so a burst still sorts in
submission order — though `run_reference` remains the authoritative order, since
a ULID is only as monotonic as the clock that minted it.

Worth recording for #12: none of this needed a `# structural:` escape. The
numbers a naive implementation writes — 5, 10, 16, 80 — are all in the
assumption set, and deriving them (`RANDOM_BITS = ULID_BITS - TIMESTAMP_BITS`,
`len(_ALPHABET)`) is both cheaper and clearer than an opt-out. The one
collision that did surface, a `3` inside a SQLite version tuple, was resolved by
storing the floor as text.

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
| 2026-09-21 | #4 | The 48 golden files are emitted under a selectable contract; `pipeline-schema-1.0` is the default. |
| 2026-09-21 | #4 | Escalation is a rate in a file and a multiplier in the arithmetic; both are literals, asserted to agree. |
| 2026-09-21 | #4 | `revenue.captureFactor` is emitted effective, not nominal, so §4.3's identity reproduces the revenue. |
| 2026-09-21 | #4 | `fcfe` is accumulated as §6's `-equityDrawdown` form; §7.3's form is lossy in the COD year. |
| 2026-09-21 | #4 | The reference GA is driven, not transcribed; a second trace with locked projects gives `force` an oracle. |
| 2026-09-21 | #5 | A-15 — pages are classic scripts; a module script cannot load over `file://`. |
| 2026-09-21 | #5 | A-16 — controls emit canonical values; display labels are a separate input. |
| 2026-09-21 | #5 | A-17 — every rate and share in the mandate is a fraction, per `api.md` §6.1. |
| 2026-09-21 | #5 | A-18 — the feasibility footer recomputes on `input`; measured 0.205 ms at 500 candidates. |
| 2026-09-21 | #5 | A-19 — the split bar's focus ring is drawn by the bar, not by its transparent slider. |
| 2026-09-21 | #5 | A-20 — the mockup's bare accent fails AA at 3.71:1 and is not ported; roles move to `accent-700`. |
| 2026-09-21 | #5 | A-21 — a null `minDscr` passes the DSCR screen; an unlevered project has no coverage to fail. |
| 2026-09-21 | #5 | A-22 — the solar-mix warning fires only when the pool is short of solar, matching its pinned string. |
| 2026-09-21 | #5 | Front end reconciled with `api.md` and `ui-contract.md` after #3 merged: mandate and project field names, seven warning codes in §5.4 order, `runnable`, three footer figures, §4 search copy. |
| 2026-09-21 | #2 | C-1 — `asset.technology` is `solar_pv`; it names a technology, not a resource. |
| 2026-09-21 | #2 | C-2…C-4 — three declared escalators, `degradationRate` and `targetDscr`; the dispersion and variance reports need them. |
| 2026-09-21 | #2 | C-5 — project ids must be zero-padded to a uniform width; canonical order is lexicographic and PRNG draws are position-indexed. |
| 2026-09-21 | #2 | C-6/C-7 — `domain` splits into a stdlib-only leaf and a pydantic shell, and `AssumptionSet` is frozen dataclasses, so the numeric core's import rule is real rather than nominal. |
| 2026-09-21 | #2 | C-8 — mandate steps are published as `multipleOf` metadata, never enforced. |
| 2026-09-21 | #2 | C-9 — `WarningCode` crosses every boundary by name, in both directions; severity is a property of the code. |
| 2026-09-21 | #2 | C-10 — `assumption_set_id` digests the calibration values only, from the normalised structure. |
| 2026-09-21 | #2 | C-11 — `assumptions/` stays at the repo root and is force-included into the wheel. |
| 2026-09-21 | #2 | C-12/C-13 — NaN becomes `None` at the pydantic boundary; immutability reaches tuple and mapping contents. |
| 2026-09-21 | #2 | C-14/C-15 — numbers are validated strictly, not coerced; magnitude statement lines reject negatives. |
| 2026-09-21 | #2 | C-16 — `holdings` implements A-14: the whole eligible candidate set, with `ProjectScalars` shared with `GET /pipeline`. |
| 2026-09-21 | #2 | C-17…C-19 — result models carry the file's domains; the two guards cannot be switched off quietly; `nan` and `inf` are not calibration values. |
| 2026-09-21 | #2 | C-1 **reversed** — `asset.technology` is `solar` after all; the cost argument for `solar_pv` stopped holding once #3, #4 and #5 merged. 1C's `reconciled` contract is what the epic should settle on. |
| 2026-09-21 | #2 | C-15 extended — balances (`debtSchedule.opening`/`closing`, `balanceSheet.ppe`) carry a 1e-9 floor; a flow gets none. All 48 golden files validate and round-trip under `reconciled`. |
| 2026-09-22 | #7 | 2B-1 — CSV cells carry raw numbers per `api.md` §9; §14 formatting serves the comment header, where the euro signs survive on the BOM. `ui-contract.md` §2 needs narrowing. |
| 2026-09-22 | #7 | 2B-2 — `holdings.csv` is the seventeen columns `api.md` §9.1 names, not the eighteen in #7's body. |
| 2026-09-22 | #7 | 2B-3/2B-4 — `run_reference` is the monotonic integer and the sort key; `run_ref` is its `A-4` label; both come from a `run_sequence` counter claimed under an IMMEDIATE transaction. |
| 2026-09-22 | #7 | 2B-5 — inputs are frozen at submission by trigger; a second finish raises unless the bytes are identical, which is a redelivery. |
| 2026-09-22 | #7 | 2B-6 — the assumption snapshot is keyed on its own payload digest, and `snapshot_hash` ≠ `assumption_set_hash` by construction. |
| 2026-09-22 | #7 | 2B-7 — `ValidationStatus` is the store's own four-value vocabulary; #6's report is stored opaquely beside it. |
| 2026-09-22 | #7 | 2B-8 — `base_year` lives on `pipeline_snapshot`; the run result carries none, which also blocks #11's chart labels. Raised on #1. |
| 2026-09-22 | #7 | 2B-9 — ULIDs on the standard library, no new dependency, and no `# structural:` escape spent. |
