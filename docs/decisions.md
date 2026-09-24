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

### A-23 — the exit year's own FCFE counts alongside the terminal value

*Raised by issue #8. Affects: #6, #7, #9, #11.*

§9.4 puts the IRR "over the equity cash flow truncated at the hold year, with a terminal value of
the exit multiple x exit-year EBITDA less outstanding debt". That does not say whether the exit
year's *own* free cash flow is still in the series or whether the terminal value replaces it, and
the two give materially different returns.

**Decided.** It counts. The terminal value is **added to** the exit year's FCFE, at the same index.

Measured against [`derived_expectations.json`](../tests/golden/fixtures/derived_expectations.json)
over 48 projects at three exit-multiple bases: including it reproduces **144 of 144** IRRs and
MOICs to within an ulp; excluding it reproduces **none**. Kept as
`interpretation.exit_year_fcfe_included` so that a fund taking the other reading is a configuration
change, and pinned by `tests/golden/test_interpretations.py`, which also asserts the alternative
fails.

The debt netted off is the balance **after** the exit year's repayment, so the exit-year cash flow
and the exit bridge do not both take credit for it.

### A-24 — LCOE discounts unescalated opex from the base year, which is neither reading issue #8 names

*Raised by issue #8. Affects: #6, #9, #11.*

§9.4 gives LCOE as "(capex + PV of opex) / PV of generation, 6% real" without saying what the opex
is discounted *from*, or whether it escalates. Issue #8 offers two readings: real opex from COD, or
nominal opex from the base year. Measured, **neither is right**.

**Decided.** `real_from_base`: **unescalated** opex, discounted from the model's base year, with
capex entering undiscounted at t0. Spelled as `economics/lcoe.py`'s `REAL_FROM_BASE`, which owns the
vocabulary — 2A reached the same answer independently and recorded it as 2A-4, and the two were
reconciled on the merge.

| basis | reproduces the 48 reference LCOEs |
|---|---|
| `real_from_base` | **48 / 48, exactly** |
| `real_from_cod` | 10 / 48, missing by up to 16 EUR/MWh |
| `nominal_from_base_year` | 0 / 48 |

Discounting from the base year rather than from COD means a late-COD asset is discounted for years
before it exists, which is arguably wrong as economics and is unarguably what the reference
computes and what every published figure in this project was calibrated against. It is pinned
because it reproduces the corpus, and named for what it is rather than filed under one of issue
#8's two labels, because calling it something it is not is how the next reader gets it wrong. `docs/spec.md` §15 is the place to revisit the economics; changing it here is one flag.

### A-25 — debt is sized by dividing by the annuity payment factor

*Raised by issue #8, from its own arithmetic. Affects: #6.*

Issue #8 writes the sizing as `min(maxGearing x capex, stabilised_ebitda / 1.40 /
annuity_pv_factor(0.055, 18))` and, in the same list, pins
`annuity_pv_factor(0.055, 18) = 11.246074465287`. Those contradict: the PV factor is the present
value of one unit a year, so **dividing** by it gives a project financing of EUR 0.70m where the
template project's senior debt is EUR 74.39m.

**Decided.** `debt = min(max_gearing x capex, stabilised / target_dscr / annuity_payment_factor)`,
where the payment factor is `r / (1 - (1+r)^-n)` -- the reciprocal, and what the reference divides
by. `annuity_pv_factor` is implemented and tested at the pinned value, because that is a real and
useful quantity and the criterion names it.

Written as a division rather than as `x annuity_pv_factor`, which is the same quantity the other
way up: `(x x d) / r` and `x x (d / r)` round differently, and the multiplied form sits 5.7e-14 from
the corpus on the DSCR-sculpted projects. Inert against the EUR 0.01m tolerance, fatal to a
field-for-field match.

### A-26 — a range the generator draws from states its span; a band it tests against states a high

*Raised by issue #8, in measurement. Affects: #2, #6.*

The generator's jitter was landed as `low`/`high` pairs and drawn as `low + u x (high - low)`. The
reference writes the width directly -- `0.94 + rnd() * 0.12` -- and the two are not the same number:
`1.06 - 0.94` is `0.1200000000000001`. Four of the ten generator ranges are like that.

**Decided.** Two types. `Band` is an inclusive pair something is **tested** against, as §10's
plausibility checks use it. `Range` is one a value is **drawn** from, stated as `low` and `span` and
drawn as `low + u x span`, with `high` derived.

The gap is about 1e-17 relative and would be unreachable anywhere else in this system. It matters
here because it lands in `netCapacityFactor`, which is emitted at full precision and multiplies
every year of generation -- so it is the difference between reproducing the reference corpus and
merely agreeing with it.

### A-27 — the generator has two seeding modes, and only one of them ships

*Raised by issue #8. Affects: #6, #7, #9.*

Issue #8 requires two things that one seeding scheme cannot do. "Seeded to match the reference, the
generator reproduces 1C's 48 projects field for field" needs the reference's **index**-keyed
`xorshift32`, seeded `i * 97 + 13`. "Inserting a project at the head of the pipeline changes no
other project's capex" needs a key that does not move when the directory does.

**Decided.** The draw plan and every formula are shared; only the stream source differs.

- **Parity mode** seeds `Xorshift32(index * 97 + 13)`. Used to reproduce the corpus, and by nothing
  that ships.
- **The shipped generator** keys on `(project id, assumption set id)` through
  `SeedSequence([salt, sha256(id), attempt])`, never Python's `hash`, which is salted per process
  and would make a pipeline irreproducible between runs of the same build.

Because the salt is the assumption-set id, re-calibrating reprices the whole pipeline -- which is
correct, and is what makes a stored run explain its own numbers.

The **site pool** is drawn from numpy rather than the ported PRNG. Seven draws per site means every
technology choice reads a stride-7 subsequence of one xorshift32 stream, and that subsequence is not
uniform: the measured mix came out 49.7 / 38.7 / 11.7 against a target of 45 / 44 / 11, a five-point
bias more samples did not wash out. The port exists for parity; the reference has no opinion about a
300-project pipeline's composition.

### A-28 — issue #8's provenance names are mapped onto §8.1's closed vocabulary

*Raised by issue #8. Affects: #6, #9.*

Issue #8 asks generated files to carry greenfield `analyst_estimate` / low, ready-to-build
`budget_quote` / medium and construction `signed_contract` / high. None of those three values is in
[`pipeline-schema.md` §8.1](pipeline-schema.md#81-estimatebasis), which is a closed vocabulary.

**Decided.** They are mapped to the values that mean the same thing: `internal_model`,
`binding_offer` and `contracted` respectively. The confidences are used as given.

Grid and O&M carry what the project actually has rather than what its stage suggests -- a greenfield
project with a firm connection agreement has a contract, and saying otherwise would make the
provenance block disagree with the field it describes.

This is also why the 48 parity files differ from 1C's in their `provenance` and nowhere else: 1C's
files were produced by its extractor and say so, and these are produced by the house model. Copying
1C's notes across to make the comparison total would put false provenance in every generated file.

### A-29 — a generated project outside the min-DSCR band is redrawn, not shipped with a warning

*Raised by issue #8. Affects: #6.*

Issue #8 wants 300 files that pass with **zero** plausibility warnings, and separately wants a
realistic share of them below the 1.25 default mandate floor. §10's band is 1.20-2.50, so the
target window is narrow on one side.

**Decided.** A project whose min DSCR falls outside the band is redrawn from its own stream --
`SeedSequence([salt, sha256(id), attempt])` -- up to `generator.dscr_resample_attempts`. Each
attempt stays a pure function of the project's own identity, so a redraw disturbs nothing else in
the directory. A project that cannot be placed is emitted anyway with its attempt count, rather than
dropped: silently dropping one would make `--count` a suggestion.

Measured on the shipped pipeline: 283 of 300 land first time, min DSCR spans 1.2013-2.1210, none
falls outside the band, and 17 (5.7%) sit below 1.25 -- which is what keeps §5.2's DSCR slider a
live control and §7.4's red-below-floor able to fire.

Parity mode does not resample. The reference's 48 are already inside the band, so the two modes
cannot disagree about the corpus.

**What the caller decided** (added after review). `build_project` reports `within_band`, and
`pipeline generate` refuses to write **anything** if any project is outside it, naming each one. An
earlier version counted every multi-attempt project as "redrawn to bring min DSCR inside its
plausibility band" and exited zero, which was simply false when the band could not be met — a
recalibration could have shipped a knowingly out-of-band pipeline that reported success. "Zero
plausibility warnings" is a property of what ships or it is nothing.

**And a site that cannot be placed at all is skipped** (added on the 2A merge, which moved
`assumption_set_id` and so every jitter stream). Redrawing fixes an unlucky jitter; it cannot fix a
market. A solar park in Finland draws a 0.098 capacity factor and clamps to the 560 €/kW floor, so
its cover ratio is whatever the resource allows — 0.97 after twenty-four redraws. §9.1 puts that
asset in the pipeline deliberately, "so the screens have something to reject"; §10 then calls it
implausible, and issue #8 asks for zero plausibility warnings. Both cannot hold for one shipped
file, so the generator draws the pool deeper than asked and takes the first `count` placeable sites.

Ids come from a site's position in the pool, so skipping one leaves a gap and changes nothing about
any other project — the same property that keying the jitter on the id buys (A-27). Ids are unique
and uniformly padded, which is all §4 and C-5 ask; they were never promised to be contiguous.

### A-30 — a workbook is written at full double precision, which Excel itself does not keep

*Raised by issue #8. Affects: #9.*

`openpyxl` formats every number with `%.16g`, and a binary64 needs **seventeen** significant digits
to survive a round trip. Measured over 40 generated files, 8,578 of 32,400 values came back changed
in their last digit -- about 1e-16 relative, far inside the EUR 0.01m tie-out tolerance, and not
lossless.

**Decided.** The writer installs `repr` -- the shortest string that reads back as the identical
double -- for the duration of one save, and asserts the attribute it replaces still exists so an
`openpyxl` restructure fails loudly. A JSON -> xlsx -> JSON round trip is then exact: 32,400 leaves,
zero differences.

**This does not make Excel exact.** Excel carries fifteen significant digits, so a workbook a human
opens and saves loses the last two, by about 1e-15 relative -- three orders inside the file's own
tie-out tolerance, and a property of the format rather than of this code. Recorded rather than
hidden, because "lossless" is otherwise a promise the spreadsheet path cannot keep.

### A-31 — the exit bridge reads `debtSchedule.closing` rather than re-amortising the facility

*Raised by issue #8. Affects: #6.*

The JavaScript reference computes the debt outstanding at exit in a **third** independent
amortisation loop, separate from both the one that builds the schedule and the one that finds min
DSCR. Its answer sits an ulp from the `debtSchedule.closing` in the same file.

**Decided.** Read the schedule. §7.4 ties `closing` out year by year, and
[§9](pipeline-schema.md#9-the-reject-derived-fields-rule) exists precisely to stop a second source
for one number -- "the one that drifts is always the stored one" applies just as well to a fourth
derivation in code.

The cost is visible and bounded: 35 of 1,440 oracle cells in
`derived_expectations.json` differ in their last bit, which is why the interpretation sweep compares
the hold-truncated series, IRR and MOIC at 1e-12 rather than exactly. LCOE and the 30-year IRR have
no second derivation and are compared exactly, 48/48.


### A-32 — a regenerated pipeline replaces what it generated, and nothing else

*Raised by issue #8, in adversarial review. Affects: #6, #9.*

`write_pipeline` wrote its files and left everything else in place. Two ways that goes wrong, and
both were reachable from ordinary flags:

- Dropping `--count` from 300 to 100 left **200 stale projects** behind, which a loader then reads
  as part of the run.
- Changing `--seed` renames every site, so every *filename* changes while every *id* stays the same.
  Overwriting by filename produced **two files per id** — and §7.9 aborts the whole load on a
  duplicate id, so the pipeline became unusable rather than merely stale. Measured: 12 projects
  regenerated under a new seed gave 24 files and 12 duplicated ids.

**Decided.** Three rules, in this order.

1. **The previously generated set is removed, not overwritten.** Ownership is read from
   `provenance.preparedBy`, which the generator stamps on everything it writes.
2. **Everything else is left untouched.** [§1](pipeline-schema.md#1-what-a-file-is) has a user
   adding a project by dropping a file in, so the directory is not the generator's to empty. A file
   it cannot parse counts as somebody's, not as rubbish.
3. **A foreign file carrying an id this run would produce raises before anything is written.** That
   is a genuine conflict with somebody's work, and silently winning it is worse than refusing.

Everything is written to a staging directory and moved in afterwards, so an interrupted run leaves
the previous pipeline intact rather than a directory half from each generation.

### A-33 — a tie-out failure blocks ingestion; a house-model variance does not

*Raised by issue #8, in adversarial review. Affects: #6, #9.*

`pipeline ingest` validated a workbook through `ProjectFile` and wrote it. That checks shape, domain
and the reject-derived rule — it does **not** check that the statements agree with each other, and
`read_workbook` does not evaluate the workbook's own `TieOuts` sheet. A workbook of entirely
plausible positive numbers whose EBITDA identity was out by €5m was therefore accepted and written
as canonical pipeline data, against §7's plain statement that such a file *does not load*.

**Decided.** The §7 identities are checked before anything is written, and a failure refuses the
file with the check, the year and the residual — which is what §7 asks a loader to report.

**The distinction is the point, and it is not the same test.**

| | means | verdict |
|---|---|---|
| tie-out failure | the file **disagrees with itself** | blocking |
| house-model variance | *this model* would have computed something else | advisory (A-7, Q-4) |

There is no coherent reading of a file whose EBITDA is not revenue less opex, so nobody can
overrule it. There are many good reasons an analyst's model differs from this one — a curtailment
regime, a tax-loss carryforward, a merchant floor — and gating on those would make the house model
authoritative again, which is exactly what the input design set out to change. A file whose
*inputs* were altered still ingests, with the variance reported.

**Superseded in part by A-35.** The checks were briefly `model/tieouts.py`, written before 2A
landed. 2A's `pipeline/validator.py` is the owner, takes a `ProjectFile` directly and produces
better messages, so the CLI calls that instead and the duplicate is gone. What the entry decides —
that tie-outs block ingestion and a variance does not — is unchanged.

### A-34 — a project's own text is written to a workbook as text, never as a formula

*Raised by issue #8, in adversarial review. Affects: #9.*

openpyxl infers a cell's type from its value, and a string beginning with `=` becomes a **formula**.
A project file's `name` is 1–120 characters of free text, and `provenance.preparedBy`,
`modelVersion` and every `note` likewise; nothing in the schema forbids a leading `=`, and nothing
should. Exporting such a project wrote a live formula into the workbook, which Excel evaluates when
an analyst opens it — `HYPERLINK` and the `WEBSERVICE` family reach the network — and the cell
stopped round-tripping as the text it was.

This is reachable without an attacker: `pipeline ingest` accepts an analyst-authored workbook, and
`pipeline export` writes any project back out. A name beginning with `=` is enough.

**Decided.** Every text value is written as an explicit string cell. Formulas appear in exactly one
place, the `TieOuts` sheet, which builds its own from the layout and never from project text.
Asserted in the stored XML rather than only through openpyxl's reader: `<f>` appears in one of the
five sheets, and it is that one.


### A-35 — 2C converges onto 2A's economics and validator rather than shipping a second copy

*Raised by issue #8, merging 2A (#19) and 2B (#18). Affects: #6, #9.*

2C was written against an empty `pipeline/` and `economics/`, because 2A had not landed. When it
did, four things existed twice, and one of them was actively broken by the merge.

**The break.** 2C had pinned `interpretation.lcoe_opex_basis = "real_from_base_year"`; 2A's
`economics/lcoe.py` accepts `real_from_cod`, `real_from_base` and `nominal_from_base` and raises on
anything else. The merged tree therefore could not compute an LCOE at all. **2A owns `economics/`
and landed first, so 2C conforms** — the value is now `real_from_base`, per epic §8's rule that a
non-owner conforms and raises any disagreement separately. There is none to raise: both issues
measured the same thing and got the same answer (2A-4 and A-24), which is the best evidence either
of us had that it is right.

**The duplicates, and what happened to each.**

| was | now | why |
|---|---|---|
| `model/tieouts.py` | `pipeline/validator.py` | 2A's row, takes a `ProjectFile` directly, and names the calendar year rather than an array index. |
| `model/returns.py` | `economics/{lcoe,irr,returns,terminal}` | 2A's are vectorised over the whole pipeline and read the same two flags. The sweep now pins the **shipped** path. |
| `model/annuity.py`'s `annuity_pv_factor` | re-exports `economics/annuity.py`'s | The two agreed to the last bit. The amortisation *schedule* stays, because `economics` has no use for one. |
| `[irr]` in the assumption set | 2A's `IRR_BRACKET_LOW`/`HIGH` | See below. |

**The IRR bracket is the one place we disagreed, and 2A wins on merit rather than on ownership.**
2C put it in the assumption set, arguing that its width decides which cash flows have an IRR at all
and is therefore a modelling choice. 2A hardcodes `[-0.9999, 10.0]` with a `# structural:` escape,
having deliberately chosen a bracket *wider* than the reference's `[-0.5, 1.2]` so that the endpoint
is the arithmetic limit rather than a number anyone picked. That is the better argument: a bracket
chosen to be unreachable is not a dial, and leaving a second one in the assumption set would have
moved `assumption_set_id` for a value nothing read.

**What this does not change.** The interpretation sweep still pins both flags against
`derived_expectations.json` and still shows every alternative failing — it now does so through the
code the optimiser runs, which is strictly more useful than pinning a private copy. The two
implementations agreed before they were merged, which is why the convergence cost no coverage:
2A's IRR reproduces the oracle to 2.2e-16 and its LCOE to the last rounded euro.


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

---

## 2A — the compute spine

Loading, validation, dispersion, the mandate-dependent derivations, the screens, the objective, the
genetic algorithm and the result aggregation. Numbered `2A-n`, following the per-issue sections #4
and #2 established above rather than the single `A-` series the header describes; the numbering
question raised on the epic was never settled, and matching what the file already does seemed
better than being the third convention in it.

### 2A-1 · The mandate-independent derivations live in `pipeline`, not `economics`

Issue #6 lists min DSCR and LCOE under `economics/`. `docs/api.md` §1.4 classifies them the other
way — *"mandate-dependent: `equityIrr`, `moic`, `terminalValue_m`, `paybackYear`;
mandate-independent: `minDscr`, `lcoe`, `gearing`, `equity_m`"* — and the classification is the one
that matters structurally.

§10's DSCR-sizing plausibility band is stated in terms of the same minimum, and `pipeline` ranks
below `economics` so it cannot import it. Putting min DSCR in `economics` would have meant deriving
it twice, once for the warning and once for the reported scalar, which is precisely how two copies
of one number come to disagree. So `pipeline/derive.py` holds min DSCR and the P50 full-year
generation, `economics/lcoe.py` holds LCOE, and `economics/` holds everything that moves with the
hold period.

### 2A-2 · The IRR bracket is #6's, not the reference's, and an all-zero series has no rate

#6 specifies bisection over `[-0.9999, 10.0]` in 100 iterations; the JavaScript reference uses
`[-0.5, 1.2]` in 70. #6's is implemented. Wider is the better answer — a 150% IRR is a number, not
"undefined" — and it costs nothing against the oracle: all 192 IRR figures in
`derived_expectations.json` lie inside `[-0.148, 0.165]`, so both brackets find the same root and
neither produces a null. 100 halvings of an interval of 11 leaves 1e-29, far below the resolution
of a double.

A second, smaller departure. The reference's null rule is "no sign change across the bracket", which
an **all-zero** series passes vacuously — its NPV is zero at every rate — so the reference returns
the bottom of its bracket, a number that means nothing. #6 requires `NaN` there alongside the
all-negative case, and that is what is implemented: a project with no cash flows has no rate of
return.

The NPV is evaluated in Horner form, which puts the first element at exponent zero where the
reference puts it at one. That is a *different NPV* — by a constant positive factor — and therefore
the **same root** and the same sign changes. The bracket and the null rule are what a port has to
reproduce, not the NPV's scale.

### 2A-3 · Min DSCR screens on the raw value, not the reference's rounded one

The reference stores and screens on `round(min(minDSCR, 3.2), 2)` (1C-4). Production does neither.
`docs/api.md` §2 asks for the minimum over the debt life excluding the ramp year and nothing else,
and a 2 dp rounding decides whether a project at 1.2449 clears a 1.25 floor —
which is a modelling accident, not a rule anyone wrote down.

The divergence is asserted **as** a divergence rather than tolerated: rounding our raw figure
reproduces the reference's to 1e-9 for all 48 projects, so it stays a rounding and cannot quietly
become something else.

### 2A-4 · Neither documented LCOE basis reproduces the reference; a third does

1C-10 warned that the reference *"discounts unescalated opex from the model base year rather than
from COD, and leaves capex undiscounted, all of which a reimplementation will otherwise fix"*. The
assumption set carries `interpretation.lcoe_opex_basis` as a flag with two documented values — real
opex from COD, or nominal opex from the base year — so that 2C's sweep can pin the one that matches.

Measured against `derived_expectations.lcoeEURPerMWh`, across all 48 projects:

| Basis | Mean absolute difference | Worst | Reproduces the golden integers |
|---|---|---|---|
| `real_from_cod` (shipped) | 4.46 €/MWh | 15.72 | 10 of 48 |
| `nominal_from_base` | 3.74 €/MWh | 6.82 | 0 of 48 |
| `real_from_base` | 0.23 €/MWh | 0.49 | **48 of 48** |

The reference's basis is a **third** combination: unescalated opex discounted from the base year.
`economics/lcoe.py` supports it as `real_from_base` so 2C's sweep has the combination to select, and
so the gap between the shipped default and the reference is a configuration difference rather than
an unexplained variance. The shipped flag is **not** changed — `assumptions/` is issue 1A's
ownership row and the choice is 2C's to pin.

### 2A-5 · Merchant exposure is capex-weighted on `ppaShare`; the revenue-weighted share is separate

#6 asks for contracted revenue share *"revenue-weighted over life, not simply `1 − ppaShare`"*. That
is a different quantity from the one the objective and §7.1's tile use, and three normative sources
agree on the latter: `spec.md` §7.1 and §10.2 both say **capex-weighted**, `docs/api.md` §4 gives
`merchantShare` as capex-weighted, and every one of 1C's 206 objective cases records
`merchantShare = 1 − ppaShare` for a single-project selection.

Both are computed. `economics.returns.contracted_revenue_share` reconstructs the revenue-weighted
figure from the file's declared escalators, and it is carried on the per-project holding row; the
objective and the tile take the capex-weighted one. Across the corpus they differ substantially —
0.048–0.443 against a `ppaShare` of 0.16–0.90 — because a ten-year PPA on a thirty-year asset leaves
two thirds of the life fully merchant whatever the volume share was.

Feeding the revenue-weighted figure to the objective would move every merchant penalty and put all
206 cases out of reach. **N-1 on the epic is the open question** about whether the two should be
reconciled by storing the revenue split in the file; until it is answered, the objective follows the
specification and the extra figure is reported beside it.

Worth recording: reconstructing the price path from `priceEscalation`, `merchantEscalation`,
`ppaShare` and `ppaTenorYears` reproduces each file's own `achievedPrice` to **3.4e-16**. That is
the evidence C-2's five fields earn their place.

### 2A-6 · The €1 cap tolerance admits one of 1C's 206 cases; that is what it is for

`OBJ-205` sets available capital one ulp below the selection's equity and the reference rejects it,
because it compares on a strict `equity > capital`. Epic §6.2 amends that with a €1 tolerance
*"so a portfolio landing exactly on it is not rejected by a rounding artefact"* — and one ulp of
€278.699m is about 1e-7 euros, which is the rounding artefact. The case therefore scores as feasible
here.

It is the **only** one of the 206 this implementation disagrees with, and it is asserted as a
required divergence rather than skipped. Its sibling `OBJ-204`, exactly on the cap, agrees with the
reference. The other 205 reproduce to 1e-12, worst 2.3e-13.

### 2A-7 · Quantisation belongs to the comparison, not to the objective

Epic §5 requires fitness quantised to 6 dp before any comparison. Applying that inside the objective
put every one of 1C's cases six decimal places out of reach, because the oracle records the exact
§10.2 value.

So `objective.score` returns the unrounded score — the number the specification defines — and
`quantise` is applied by the genetic algorithm before any selection and by the result layer before
any number is reported. The invariant is unchanged; only its location moved.

### 2A-8 · Per-screen drop counts are independent, and overlap

§13 requires a warning that *names the screens to widen*. Evaluating the nine screens in order and
stopping at the first failure cannot produce one: every rejection is attributed to whichever screen
happens to run first, so "countries" absorbs the blame for a COD window that is really what is too
tight, and a user widening the named control gets nothing back.

Each screen is therefore evaluated **independently, against the whole pipeline**. The counts overlap
deliberately and summing them is meaningless; each answers "what would widening *this* control give
me back", which is the question actually being asked.

### 2A-9 · The `UK` alias applies to mandates as well as to files

`docs/pipeline-schema.md` §4.1 keeps `UK` as an alias of `GB`, and the loader normalised it on the
way in. The mandate's eligible-country chips were **not** normalised, so a mandate listing `UK` —
which the reference's own default mandate does — silently rejected every British project. It cost
one project out of the reference's 33-candidate pool and nothing else would have caught it.

`normalise_country_code` is now applied at both ends. An alias that holds in one direction only is
not an alias; it is a screen.

### 2A-10 · A pipeline with mixed-width ids fails the load

C-5 records that canonical order is a plain lexicographic sort, so `"P10" < "P9"`, and that the GA's
PRNG draws are indexed by position — meaning a pipeline mixing `P9` with `P10` changes every result
and nothing notices. C-5 and `domain/conventions.py` both say the loader is the place to reject it,
and neither had anywhere to put the check.

It is now a **pipeline-level** failure, alongside §7.9's duplicate ids and unanimous base year: all
three break the index rather than a file, and no subset of the pipeline is usable once one does.

### 2A-11 · Duplicate ids abort the load, naming both files

`docs/pipeline-schema.md` §4 and §7.9 both say a duplicate id aborts the **load**; #6 says it
"rejects both files". §7.9 is the normative one and gives the reason — *"no subset of the pipeline
is usable when one of them breaks"* — so the load fails, with the error naming every file that
claims the id. Both readings agree on what must not happen, which is silently preferring one.

### 2A-12 · §7.7 is checked in its multiplied form

`dscr = ebitda ÷ (interestPaid + debtRepayment)` is verified as `dscr × service = ebitda`.
Algebraically the same identity, but it compares €m to €m, which is the unit §7's €0.01m tolerance
is quoted in — an absolute tolerance against a bare ratio means nothing. It also never divides, so a
year with no debt service cannot raise a numpy warning that `filterwarnings = ["error"]` turns into
a test failure; such a year fails the identity on its own terms, which is correct.

### 2A-13 · The budget repair keeps a prefix, and spends on locks first

Epic §6.1's repair keeps holdings in a random priority order "until the cumulative equity no longer
fits". That is a **prefix** rule: once the running total is exceeded, everything after it is dropped,
including anything cheap enough to have squeezed in. Skipping the expensive holding and carrying on
would be a better knapsack heuristic, and that is the objection — a repair operator that quietly
optimises biases the search towards cheap projects in a way no weight in the assumption set asked
for.

Locked holdings sort first, so the budget is spent on them before anything else. Forcing locks
*after* repair would let repair drop one and the forcing put it back over budget; forcing before
repair without the priority would let repair drop it. The case where locks alone exceed the budget
never reaches the search — §5.4's `LOCKS_EXCEED_CAPITAL` is blocking.

### 2A-14 · The PRNG draw order is five calls per generation, not four

Two at initialisation — inclusion, then repair priority — and five per generation: two tournament
draws, the crossover coin, the mutation draw, the repair priority.

The two tournaments must be drawn **separately**. Drawing one `(tournament_size, children)` block
and reversing it for the second parent gives the same winner except on ties, so almost every child
would cross a chromosome with itself and the search would stop exploring. The order is asserted call
by call and shape by shape by a counting proxy, and a second test checks the call count is a
function of the generation count alone — which a per-chromosome loop anywhere would break.

The crossover coin is drawn as an integer in `{0, 1}` rather than compared against a probability.
An even Bernoulli draw is what makes uniform crossover *uniform*; it is not a tunable, and the
assumption set should not have to carry one.

### 2A-15 · `optimiser/result.py` emits euros and plain dataclasses

`domain/results.py`'s `PortfolioAggregates` and `Holding` are pydantic and denominated in €m; the
numeric core may not import pydantic, and every package that can see both ranks *above* `optimiser`.
So the assembly cannot live in 2A at all.

`result.py` emits frozen dataclasses in **euros** whose field names mirror those models one for one
minus the `_m` suffix. 3A's `runner` maps them and crosses the unit boundary; 2B persists the same
shape. `tests/unit/test_optimiser_result.py` asserts the two field sets line up in both directions,
so the hand-off is a failing test rather than a convention — it caught two fields with no wire home
the first time it ran.

For the same reason `optimiser/feasibility.py` returns `(WarningCode, numeric detail in euros)`
rather than a rendered message. Formatting "€1,200m" inside the numeric core would be a third unit
boundary in a system that permits two, and `docs/ui-contract.md` §3.5 already owns the copy.

### 2A-16 · Tiles 1 and 3 carry deviations rather than a banded verdict

`docs/ui-contract.md` §5.1 bands the capacity tile at "within 8% of target" and the technology-split
tile at "within 8 points". Those two numbers live in that document and in no configuration file, so
banding them inside the numeric core would put a display constant where the literal guard — and the
epic's "every rate, weight, floor, clamp and band lives in the assumption set" — says one may not go.

The ten tiles whose threshold is a mandate value carry a verdict. Tiles 1 and 3 carry their signed
deviation, and the layer that owns the band applies it. **Raised on #6** as the one place §7.1's
compliance states are not fully computed here.

### 2A-17 · Undefined blended IRR falls back to the hurdle

When no selected project has a defined IRR the return term contributes exactly zero, because the
blend is replaced by the mandate's own hurdle. Substituting a return of *nought* would instead
charge the portfolio the full −1.2 rail for a number nobody has.

The two feature columns that make this expressible — `equity × irr × defined` over
`equity × defined` — are a pair on purpose: splitting the weighted average into two linear
quantities is what lets an undefined project be excluded from **both** halves inside a single matrix
product, rather than coalesced to zero and then averaged in, which is what the reference does
(1C-6).

1C predicted this would diverge from the objective cases at short holds. It does not, and the reason
is worth recording: all five cases with a null per-project IRR have their returns term already
pinned to its negative rail, so excluding and coalescing give the same clipped contribution. The
difference is real; that corpus does not reach it. A handcrafted interior case does, and is tested.

### 2A-18 · Measured performance, and a closed gap

On an otherwise idle machine (Apple silicon, numpy 2.4.6):

| Candidates | Load | Fast 50×35 | Standard 90×60 | Exhaustive 160×110 |
|---|---|---|---|---|
| 48 | 49 ms | 10 ms | 18 ms | 41 ms |
| 300 | 268 ms | 29 ms | 73 ms | 216 ms |
| 500 | 354 ms | 43 ms | **107 ms** | 323 ms |
| 2,000 | 1,403 ms | 146 ms | 383 ms | **1,182 ms** |

Against epic §7: Standard at 500 candidates budgets 5 s and measures **107 ms**; a 300-file load
budgets 2 s and measures **268 ms**.

Epic §7 also records a known gap — *"Exhaustive 160×110 at 2,000 candidates measures 30 s. Closing
it is issue 4B's repair-masking work"*. It now measures **1.18 s**. The vectorised budget repair and
the single-GEMM reduction appear to have closed it; 4B should re-measure before spending effort on
repair masking.

**Timings taken under load are worthless.** An earlier measurement of the same 300-file load gave
5–15 s with a load average above 400 on this machine; the same code measures 268 ms with the machine
idle. Any performance number in this project should carry the load average it was taken under.

### 2A-19 · An empty eligible pool raises `NO_CANDIDATES` and nothing else

A pool with no candidates has zero capacity, zero equity and a zero solar share, so
every advisory test fires on figures that describe nothing: the preview reads as five
problems where there is one, and the four extra warnings all point at controls that are
not what is wrong.

`web/js/feasibility.js` guards `CAPACITY_BELOW_TARGET`, `LEVERAGE_UNREACHABLE`,
`SOLAR_MIX_UNREACHABLE` and `CAPITAL_UNDERUSED` on a non-empty pool; only
`LEVERAGE_UNREACHABLE` was guarded here. Since 4B proves the two implementations agree
and `api.md` §5 says the client is wrong if it diverges, a preview that disagrees with
its own mirror is worse than one that says less — so all four are now guarded the same
way. `LOCKS_EXCEED_CAPITAL` and `LOCKS_PRESENT` are deliberately **not** guarded: both
are about the user's own edits, not about the pool.

### 2A-20 · Locked projects still re-admit past the screens

Raised in review as a defect: a lock re-admits a project that fails a hard pre-screen —
a changed country, stage, COD window, DSCR floor or risk cap — and the optimiser may
then select it.

**Kept, because it is what the issue asks for.** #6's screens section says *"Locked
projects re-admit regardless of screens, with the re-admitted IDs surfaced — the same
principle as §13 already allowing locks to breach a concentration cap visibly."* §13
sets that principle out in two adjacent rows: locks alone exceeding capital **block**
the run, and locks alone breaching a concentration cap let the run **proceed with the
breach surfaced**. A lock is a user instruction that outranks a soft screen, and the
design answer to "this is dangerous" is to show it, not to drop it silently.

The re-admitted ids are on `ScreenResult.readmitted` and drive `LOCKS_PRESENT`, so the
override is visible rather than implicit. The one case where a lock does **not** win is
an id that is both locked and excluded: the exclusion is the more specific instruction,
and a stale lock should not resurrect a project the user has just struck out.

Worth naming the alternative, since it is a reasonable position: screening locks would
make the eligible set a pure function of the mandate, which is simpler to reason about
and to serve. If that is wanted it should change #6 and §13 together, not just this
module.

---

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

### 2B-10 — the submission names its assumption snapshot; nothing is inferred

Found in review. `open_run` looked the snapshot up by
`(assumption_set_id, assumption_set_hash)`, taking the most recently recorded
match. Both halves of that are wrong:

- Two snapshots that differ only in `[meta]` or in file name share **both**
  values by design ([2B-6](#2b-6)), so no query over them can tell the payloads
  apart.
- Re-recording an already-stored variant does not move its `recorded_at`, so
  after a newer variant is inserted, a run using the older one is attached to
  the newer payload — an audit record citing a calibration the run never read.

**Decided.** `RunSubmission` carries `assumption_snapshot_hash`, the value
`record_assumption_set` returns. The caller already holds the right answer, so
the store does not guess at it. `open_run` additionally checks that the named
snapshot's id and hash match the provenance, so a run cannot cite one
calibration while serving another; the foreign key proves the snapshot exists,
this proves it is the right one.

### 2B-11 — what "the same run" and "the same outcome" mean, exactly

Three more from the same review, all of the same kind: a check that was narrower
than the thing it claimed to guarantee.

**The whole provenance is compared, not three fields of it.** `_assert_same_run`
checked the seed, the pipeline hash and the assumption-set id — the three that
have columns. A worker returning a different `fileHashes`, `numpyVersion`,
`blasThreads`, `pythonVersion`, `platform` or `engineVersion` was stored
happily, and the run then claimed to be reproducible from inputs it never saw.
The comparison is now against the record the run was *opened* with, parsed back
out of the row, so it covers every field rather than every indexed field.

**The curve is reconciled by value.** `finish_run` compared the number of
persisted events with `len(convergence)`, which passes a worker that logged the
right number of wrong points — and the result would then show one shape live
and another on reload. Every generation and both its fitness values are
compared now. An **empty** log stays legal: that is a run executed without a
subscriber, the CLI path among others, and an absent log is not a disagreeing
one. A *partial* log is what the check catches.

**The generation log closes when the run does.** An event delivered after
`finish_run` committed still inserted, because the foreign key only asks whether
the run exists. That grew the curve of a run whose result had already been
served — a mutation of something §11 calls immutable. A `BEFORE INSERT` trigger
refuses it, at the database layer rather than in the caller, because the check
and the insert have to be one statement to be free of a race with the finish.

**A redelivery repeats the whole outcome.** The idempotent second `finish_run`
compared `result_json` alone, but the failure reason and the warnings are stored
*beside* it and are not in it — so one record with two different failure codes
was waved through as a retry. All three are compared now.

### 2B-12 — a pipeline hash does not digest what is stored beside it

Found in review. `record_pipeline_snapshot` used `ON CONFLICT DO NOTHING`,
copying the pattern from `record_assumption_set` — where it is safe, because
there the key *is* a digest of everything the row holds.

It is not safe here. `pipeline_hash` digests the project files; `base_year`,
`project_count`, `validation_status` and `validation_json` are not in it. So a
second snapshot under the same hash with a different base year was silently
discarded while the caller was handed the hash back as though it had been
stored — and a run citing it took its year labels from a base year nobody
recorded for it, on a `cashflow.csv` that carries no other year information.

**Decided.** A conflict whose stored row *agrees* is the ordinary case and
passes: every reload of an unchanged pipeline hits it. A conflict that
disagrees raises `SnapshotConflictError`, naming each field that differs.
`source_label` and `loaded_at` are excluded from the comparison — the same
pipeline read twice, or read from a copy of the directory, is the same
snapshot.

### 2B-13 — five smaller things the same review found

**Percentages are scaled inside the decimal domain.** `percent` computed
`value * 100` in binary float before converting to `Decimal`, which put back
exactly the representation error `Decimal` was chosen to remove: `0.145 * 100`
is `14.499999999999998`, so a 14.5% solar target exported as `14% solar`.
These are the values `ui-contract.md` §2 says #12's parity tests compare.

**A project name cannot become a formula.** Names come from files analysts drop
into the pipeline directory, and this export exists to be opened in Excel. A
name beginning `=`, `+`, `-`, `@` or a leading tab was imported as a live
formula; CSV quoting does not help, because Excel strips it before evaluating
what is inside. Text cells now carry a leading apostrophe when they start one
of those. **Numbers do not take that path**, so a negative cash flow stays
`-28.93` and the column still sums.

**The comment header is kept to one line per line.** It bypasses `csv.writer` —
it is not a row — so nothing else escapes it, and a newline in an interpolated
hash would have added physical lines ahead of the column header.

**The version guard guards.** `initialise` accepted any older file, re-ran DDL
that `CREATE TABLE IF NOT EXISTS` cannot apply to an existing table, and then
stamped it current. It now refuses anything that is neither 0 nor the current
version, and only stamps a file it actually provisioned. As a side effect the
schema no longer re-executes on every connection open.

**A duplicate generation inside one batch names itself.** It is caught before
the insert, while the offending generation is still known; afterwards the
transaction has rolled back and the stored log holds no trace of it.

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

---

## 3A — the API, the stream, the runner and the committee pack

Issue [#9](https://github.com/JanSchm/TerraFolio/issues/9). Epic §8 makes 3A the wire contract's
owner of record once it implements it, so the reconciliations below are decisions, announced on
[#1](https://github.com/JanSchm/TerraFolio/issues/1#issuecomment-5803582910) rather than negotiated.

### 3A-1 · A run in flight returns 200, not 202

Issue #9's endpoint table says "**202** with `{status, generation}` while in flight";
`docs/api.md` §8 says a run still in flight returns **200** with `status: "running"` and no
`aggregates`. §8 wins, because #9 itself says to match `api.md` exactly and #11 is being built
against it.

It is also the better answer on its own terms. The run *is* the resource; 202 would say the request
to **read** it had been accepted, which is not what is being reported. #9's intent — that a
non-streaming client can poll progress — is preserved additively: the in-flight body now carries
`generation` and `totalGenerations` alongside `status`. A `failed` or `cancelled` run is likewise
200, carrying its `error` block: a failed run is audit trail (§12), not a 404.

### 3A-2 · `id:` is the generation number, and terminal frames carry none

The stream needs a resume cursor, and SSE already has one. Every `generation` frame carries
`id: <generation>`, so `Last-Event-ID: 20` resumes at 21 with no bookkeeping on either side — a
browser's `EventSource` sends the header itself on reconnect.

`status`, `done` and `failed` deliberately carry **no** `id:`. An id on a terminal frame would
collide with the generation sequence, and under the EventSource specification a frame without one
leaves the client's cursor where it was — which is exactly what a reconnect after `done` needs.

The terminal failure frame is `event: failed` with `{"runId", "error": {"code", "message"}}`, as
`api.md` §7 has it, not the `event: error` in #9's sketch. Same reason as 3A-1.

### 3A-3 · `410 RUN_EXPIRED` is reserved and never emitted

§7 offered it for "the stream has closed and the result is available instead". Because the event log
is durable and is the stream's source of truth, a completed run's stream still replays every
generation and then sends `done` — which is what #9's acceptance criteria require. Nothing prunes
`run_event`, so no stream can expire. Implementing 410 would mean an unreachable branch that
contradicts the replay guarantee, so the code stays in §11's table as reserved.

### 3A-4 · An empty `countries` or `stages` is 400, not 422

`api.md` §6.1 said an empty list was accepted and answered `NO_CANDIDATES`. 1A's
`domain/mandate.py` requires at least one of each, and epic §8 makes that model the executable
definition of the schema — so 3A conforms and the document records the model's rule.

It is also the more useful distinction. "You have selected no countries" is a fault in the mandate,
which is what 400 with `detail.field` says. `NO_CANDIDATES` keeps its own meaning: the mandate is
coherent and the pipeline has nothing that fits it — a COD window the corpus does not reach, a DSCR
floor nothing clears. Collapsing the two would have told a user to widen screens that were never
narrow.

### 3A-5 · `severity` is `alert` or `info`; `runnable` carries the block

`api.md` §5 wrote `"severity": "blocking"` on the two blocking codes. 1A's `WarningSeverity` admits
`alert` and `info` only, and `FeasibilityWarning` validates that a warning's severity is the one its
**code** carries — so the model could not emit "blocking" even if the document asked for it, and 2B
stores that model.

Whether a warning stops the run is a property of the code (`WarningCode.disables_run`, true for
exactly `NO_CANDIDATES` and `LOCKS_EXCEED_CAPITAL`) and reaches a client as `runnable`. Severity
says how loudly to render a warning; `runnable` says whether the button works. `web/js/feasibility.js`
still emits `severity: 'blocking'` client-side — **#10 or #11 should reconcile it**, since the server
and the store agree with each other and not with the page.

### 3A-6 · The API draws the seed, because the store cannot hold numpy's

`resolve_seed(None)` draws from `numpy.random.SeedSequence`, whose entropy is **128 bits**;
`run.seed` is a SQLite `INTEGER`, which is signed 64-bit. So every unseeded run — the default path,
since `seed` is optional on the wire — searched correctly and then failed to store with
`OverflowError`. Found by the first test that omitted a seed.

Fixed at the API boundary rather than by changing either owner's code: the handler draws
`secrets.randbits(63)` and passes it to `resolve_seed` explicitly. `secrets` for the same reason
`resolve_seed` uses the operating system — a seed out of a seeded stream would make two "unseeded"
runs identical. `OptimisationRequest.seed` is bounded to the same range, so an oversized
client-supplied seed is a 400 on the wire rather than a 500 at the insert.

### 3A-7 · `blasThreads` records what the run had, not what was asked for

Every threading library reads its environment variable once, when its shared object loads — which
happens on the first `import numpy` in a process. A pin applied afterwards changes the environment
and nothing else. So `pin_threads` records **whether it was in time**, and `observed_threads` reports
the inherited count when it was not.

A pool worker pins before numpy loads and honestly reports 1; an in-process run on a live server
honestly reports the eight threads it had. A provenance block claiming a determinism the run did not
have would invite a reproduction attempt that cannot succeed and give no clue why.
`deterministic_reduction` on the run row is `blas_threads == 1`, and
`api/records.py` refuses a record whose worker reported a different count from the one submission
recorded.

### 3A-8 · The worker verifies the hash it was given

409 at the front door catches a client holding a stale `pipelineHash`. It cannot catch a directory
edited between acceptance and execution. The worker loads the pipeline itself — cached per process
by `(directory, hash)`, so only the first run in each worker pays the ~250 ms load — and refuses to
run if what it read does not hash to what the run was accepted against. A run whose provenance names
one snapshot while its numbers came from another is not auditable, and this is the only place that
can prove it did not happen.

### 3A-9 · The committee pack renders itself, server-side, with no JavaScript

#9 suggests inlining "the portfolio page's JS modules". Those modules — `portfolio.js`, `charts.js`,
`map.js`, `table.js` — are #11's and do not exist yet, so the pack renders its own markup instead.
That is not a workaround forced by sequencing; it is the better artefact:

* A pack opens from `file://`, where a module script and `fetch` are both CORS-blocked (A-15).
* §7.7's output is a **print**, and a page whose content appears only after a script has run prints
  differently depending on when the dialog opened.
* It is a third of the size: ~375 KB rendered, against ~850 KB with Alpine, d3 and the atlas inlined.

The map's projection is therefore reimplemented in Python — `d3.geoMercator`, centre `[12, 55]`,
scale `width × 1.15`, as `ui-contract.md` §5.3 pins it — rather than shipping 36 KB of `d3-geo`,
17 KB of `d3-array` and a 108 KB atlas in every pack. "Reimplemented" is only defensible if it is
pinned, so `tests/api/test_committee_pack.py` drives the vendored library under node and compares:
the two agree to **3.4e-13 px**. The raw formula is spelled as d3 spells it, `log(tan((π/2 + φ)/2))`,
so they agree in the last bits rather than to within a rounding.

Countries are matched to the atlas by **name**, which works because `generate/countries.json` and
the Natural Earth corpus agree on all fourteen markets. An unmatched country is simply not
highlighted; the markers, which carry the actual portfolio, are plotted from the run's own
coordinates regardless (§13).

### 3A-10 · The pack's display constants live in a data file

`pack-layout.json` holds every colour, band and geometry the pack draws with, quoting
`ui-contract.md` §1.1, §5.1, §5.2, §5.3 and §6. The same argument as
`src/terrafolio/generate/countries.json`: these are a quotation of a document rather than
calibration the engine reads, and a display constant written into a source file is one nobody can
audit against the document it came from. `tests/api/test_committee_pack.py` parses `ui-contract.md`
and asserts they still match.

The pack declares §1.1's palette as its own custom properties because the compiled Tailwind
stylesheet resolves its tokens into utility classes and emits none. That is a real hazard, and it
bit: kebab-casing the token names turned `neutral200` into `--pack-neutral-2-0-0` while the SVG
still asked for `var(--pack-neutral200)`, so every numbered token fell back to its initial value —
**black** for `fill` — and the map rendered as one solid block while every structural assertion
still passed. Two tests now guard it: one asserts every `var(--pack-*)` used is declared, the other
that the map carries two distinct country fills.

### 3A-11 · The §5.4 sentences live in Python, pinned to the document

`optimiser/feasibility.py` deliberately emits a code and its numbers in euros and never prose, but
`api.md` §5 puts `message` on the wire for non-browser clients and for #12's parity tests. So
`api/messages.py` holds §3.5's and §3.6's sentences **verbatim**, with their own placeholders, and
`tests/api/test_messages.py` parses those sections out of the markdown and asserts each template
still matches. Copy that drifts is then a failing test rather than a discrepancy found by an
investor reading a warning that no longer matches the screen.

Figures go through `export/csv.py`'s formatters — the same ones the CSV header uses — so one
rounding rule serves every server-rendered number, with the unit stripped where the sentence
supplies it rather than rounded a second time.

### 3A-12 · The snapshot's report is stamped with the load's time

`record_pipeline_snapshot` is content-addressed and compares the stored validation report against
the one offered. Building that report with `datetime.now()` at record time therefore made the second
record of an **unchanged** pipeline look like a conflicting one, and 409'd every run after the
first. The report is stamped with the load's own `loaded_at`, which is the value it describes.

### 3A-13 · Two small ones

`GET /pipeline?assumptionSetId=` is accepted only when it names the set the server is running;
anything else is 400. v1 loads one set at startup and records every run against it, so serving the
active set under another id would attach the wrong calibration to whatever the client did next.

A plausibility warning's project id is recovered from the leading `"{id}: "` of its message and
**checked against the loaded ids**, because `pipeline/validator.py`'s `PlausibilityWarning` carries
only `check` and `message` while `api.md` §3 puts an `id` on the wire. A warning that does not match
the convention reports `null` rather than a guess. Worth folding into 2A's own record if that module
is revisited.


### 3A-14 · A snapshot's validation report describes the pipeline, not the load

`pipeline_snapshot` is content-addressed, and `record_pipeline_snapshot` compares the stored
validation report against the one offered — deliberately ignoring `loaded_at` and `source_label` on
the row, because "the same pipeline read twice, or read from a copy of the directory, is the same
snapshot" (2B-12).

Serialising `GET /pipeline/status`'s body into `validation_json` smuggled both straight back past
that check: the report embeds `loadedAt` and `durationMs`, which differ between any two loads of
identical files. A server **restarted against an existing database** therefore offered a report
differing in two fields, and every run it accepted afterwards answered **409 `PIPELINE_MOVED`** for
a pipeline that had not moved. Found by a test that started a second service over one database, not
by reading the code — the first symptom is a 409 three layers away from the cause.

So the stored report carries what the load *found* — the rejections, the warnings, the dispersion —
and nothing about **when** it ran. `GET /pipeline/status` still serves `loadedAt` and `durationMs`,
which are exactly what a reader of a live server wants; they simply cannot be part of an identity.

The general rule, worth stating because it will come up again: **anything that varies between two
loads of identical content must not reach a content-addressed row.** A timestamp, an elapsed
duration and a source path all qualify.

### 3A-15 · One sentence on the wire, the whole traceback in the store and the log

`run.error_message` holds the worker's traceback, which is what an operator needs. §1.7 says
`message` is "one sentence fit to show a user", which is what a client gets. `api/messages.py` owns
both sentences and `terminal_error` derives the pair once, so the HTTP body and the SSE frame cannot
tell a client two different stories about why one run stopped.

A cancellation is not an engine failure. `error_code` is mandatory for `failed` and optional for
`cancelled`, so the missing case resolves to `RUN_CANCELLED` rather than `ENGINE_ERROR` — and
`Service._failed` records `RunStatus.CANCELLED` when the cause is a `CancelledError`, which is what
`Runner.shutdown` produces for every queued run on an orderly shutdown.

---

## 3B — accessibility and copy conformance

Issue #10 takes issue #5's page shells through [`spec.md`](spec.md) §12's accessibility
requirements and §14's content and tone requirements, against the normative detail in
[`ui-contract.md` §7](ui-contract.md#7-accessibility) and [§8](ui-contract.md#8-copy-rules).
It lands before issue #11 so that group 4 wires data onto conformant markup rather than
retrofitting it, which is what the design mockup did and what A-10 exists to undo.

### 3B-1 · The contrast audit covers the pairings in use, not the roles defined

*Raised by issue #10. Affects: #11.*

A-20 re-mapped the mockup's failing roles and pinned twenty-nine pairs in
`web/tests/contrast.test.js`, computed from `tailwind.config.js`. That checks every pair the
*system defines*. It cannot check the pair a page actually renders, which is a different
question: a role nobody uses cannot fail in front of a user, and a pair nobody wrote down
can. `accent-700` on `highlight` — the compliant tone on the §5.1 highlight tile — was in
neither list.

**Decided.** `web/tests/contrast-audit.test.js` walks all four pages after Alpine has
rendered, with the holdings rows populated and both overlays open, resolves every
`text-*`/`bg-*` pair and every enabled control's own border, and measures each. The table
below is generated by `node web/tests/tools/contrast-audit.js` and asserted to still match —
the arrangement `tools/splice-nav.mjs` and `nav.test.js` already use, so the record cannot
drift from the markup.

Three things the walk has to get right, each of which produced a wrong answer first:

- **A control's boundary is its own border, against what is behind the control.** Comparing
  an inherited border gave a panel's hairline as the RUN OPTIMISATION button's boundary;
  comparing against the control's own fill gave `accent-700` on `accent-700`, 1.00:1. The
  boundary that matters is the button against the page, 5.78:1.
- **Translucent tokens are composited.** `--color-divider` is `text` at 16% and the scrims
  are `text` at 35%; neither means anything until it is resolved against its ground.
- **Decoration is exempt, and the exemption is enumerated.** WCAG 1.4.3 exempts pure
  decoration, which here is exactly one thing: the stepper's `aria-hidden` em-dash
  separators, `neutral-400` on `bg` at 1.79:1, carrying nothing not already adjacent. A
  second test asserts that is the whole list. A third holds every **status mark** to 4.5:1
  regardless — a mark is `aria-hidden` because it duplicates the word beside it for a
  screen reader, but it is the only signal a sighted reader gets, and it must not hide
  behind the decoration exemption.

Large text is deliberately **not** classified. WCAG would allow 3:1 at 24px, but nothing in
use needs the relaxation — the weakest text pair renders at 5.32:1 — and size-and-weight
classification is where this kind of audit usually goes wrong. `contrast.test.js`'s unused
`LARGE` constant stays unused.

#### The contrast audit

| Foreground | Background | Resolved | Ratio | Needs | Where |
|---|---|---|---|---|---|
| `accent-700` | `bg` | #416180 on #f2f2f3 | **5.78:1** | 3.0:1 | control boundary |
| `edge` | `bg` | #7a7a7d on #f2f2f3 | **3.82:1** | 3.0:1 | control boundary |
| `accent-700` | `bg` | #416180 on #f2f2f3 | **5.78:1** | 4.5:1 | text |
| `accent-700` | `highlight` | #416180 on #eef6ff | **5.93:1** | 4.5:1 | text |
| `accent-900` | `wind` | #1d2d3d on #94bce3 | **7.06:1** | 4.5:1 | text |
| `bg` | `solar` | #f2f2f3 on #416180 | **5.78:1** | 4.5:1 | text |
| `breach` | `bg` | #2c455d on #f2f2f3 | **8.87:1** | 4.5:1 | text |
| `breach` | `deemph` | #2c455d on #e7e7ea | **8.04:1** | 4.5:1 | text |
| `muted` | `bg` | #5d5d60 on #f2f2f3 | **5.87:1** | 4.5:1 | text |
| `muted` | `deemph` | #5d5d60 on #e7e7ea | **5.32:1** | 4.5:1 | text |
| `muted` | `highlight` | #5d5d60 on #eef6ff | **6.02:1** | 4.5:1 | text |
| `neutral-800` | `bg` | #424244 on #f2f2f3 | **8.96:1** | 4.5:1 | text |
| `text` | `bg` | #1d1f20 on #f2f2f3 | **14.79:1** | 4.5:1 | text |
| `text` | `deemph` | #1d1f20 on #e7e7ea | **13.41:1** | 4.5:1 | text |
| `text` | `highlight` | #1d1f20 on #eef6ff | **15.18:1** | 4.5:1 | text |

Every pairing clears its floor. The two closest are `edge` on `bg` at 3.82:1, which is the
input and switch boundary A-20 moved to `neutral-600` for exactly this reason, and `muted`
on `deemph` at 5.32:1, the muted sub-line on a row the optimiser did not select.

### 3B-2 · One status vocabulary, exported, rather than three that drift

*Raised by issue #10. Affects: #11.*

[A-10](#a-10--71s-compliance-colour-and-12s-colour-only-ban-are-reconciled-by-a-second-channel)
settled that colour is an additional channel and every status also carries a mark or a word.
The marks themselves ended up in three places: the tile states in `web/js/controls.js`, the
warning marks in `web/js/feasibility.js`, and the `Blocker:` / `Warning:` / `Note:` prefixes
only as literal markup in `styleguide.html`.

**Decided.** `TerraFolio.status` in `controls.js` is the single table, and `kpiTile` reads from
it rather than keeping its own copy. `web/tests/status-vocabulary.test.js` holds each mark
against §7.1 itself rather than against a transcription, because an ASCII lookalike renders
perfectly and means nothing — `x` for `×` is the failure `wire-contract.test.js` already had
to guard for the warning marks.

Writing that test found that §7.1 names the filled square but not the hollow one: it lists
only states that carry a *colour*, and an unlocked row has no tone to be the only signal. The
pair is pinned in §5.4's lock column, which is where the assertion looks.

### 3B-3 · The live regions are derived companions, not the figures themselves

*Raised by issue #10. Affects: #11. Amends `ui-contract.md` §7.2.*

§7.2 asks for the mandate footer's figures and the search screen's round counter to be polite
live regions, "so a screen-reader user hears progress and feasibility without polling". Taken
literally this defeats itself. Feasibility recomputes on `input`
([A-18](#a-18--feasibility-recomputes-on-input-not-change)), so one drag of the capital slider
emits dozens of announcements; the engine streams a round at least every 100 ms (epic §7), so
the counter interrupts itself ten times a second. Either way the reader turns it off, and then
hears nothing at all — the outcome §7.2 exists to prevent.

**Decided.** The visible figures keep updating at full rate and are not live. Each page carries
one `sr-only` `role="status" aria-live="polite" aria-atomic="true"` region, and `liveRegion()`
composes a single sentence from those figures after a quiet window — 700 ms on the mandate,
1.5 s during a run.

It is **derived by observation** rather than written alongside the figures, which is the part
that matters for #11: the announcement cannot drift out of step with the screen, and there is
nothing to remember to update. Nothing is announced until a figure is known, because the pages
ship showing em dashes and reading a row of those aloud on arrival is noise, and an unchanged
sentence never interrupts twice.

A region is announced only once **every** figure in it has arrived, not as soon as one has.
The slots are found by `data-field`, which is what marks a figure as opposed to the words
around it: the search screen's counter reads as the single string `ROUND — / —`, which is not
the em dash and so looks like a value that has arrived when nothing has. It also learns its
total from the `202` before the first round streams, so "at least one known" would read
`ROUND — / 60. Mandate score —. Capacity —. …` aloud at the one moment the user is waiting to
hear a number. A partly-known region stays silent; the next quiet window is a fraction of a
second away.

This meets §7.2's intent and not its letter. `ui-contract.md` §7.2 is amended to say so.

### 3B-4 · The holdings table's sort keys are the wire names, in the markup

*Raised by issue #10. Affects: #11, #12.*

§5.4 says sort keys are the field names in the run's `holdings` array "not the design mockup's
internal state names (`lev`, `cf`, `gwh`, `offtake`)", because #12 asserts client and server
produce the same order and that needs one agreed name per column. #5's markup carried the
mockup's names anyway.

**Decided.** All fifteen `data-sort` and `data-column` values are now the `holdings` field
names, and `web/tests/holdings-table.test.js` parses the fifteen keys out of §5.4's own table
rather than transcribing them. Renaming before #11 starts costs one edit; after it starts it
costs three.

`aria-sort` moved with them. It was present on every sortable header and hard-coded to `none`,
with an empty span beside it waiting for an arrow — an attribute that never changes is worse
than an absent one, because it tells a screen-reader user the table is unsorted while the
arrow says otherwise. Both now derive from one piece of state in `holdingsTable()`.

`sortBy` emits `tf:change` and does **not** reorder `rows`: §12 budgets sort and filter under
50 ms at 500 rows and that is #11's, but the accessibility state is not.

### 3B-5 · The focus ring is `accent-700`, and two controls invert it on purpose

*Raised by issue #10. Amends `ui-contract.md` §7.2.*

§7.2 specified the ring as "a 2px `--color-accent` outline", a token
[A-20](#a-20--the-ported-palette-drops-the-mockups-bare-accent) removed from the ported system
as anything but a fill. `src/input.css` has always drawn `accent-700`; the document was stale.

**Decided.** §7.2 now says `--color-accent-700`, and `web/tests/focus-ring.test.js` measures
the claim rather than repeating it. Because the ring is offset, it is drawn *outside* its
control, so what it must stand out against is the ground behind the control and not the
control's own fill — 5.78:1 on the page ground, across every focusable control on all four
pages, with the holdings rows rendered and both overlays open.

Two controls draw their own, both for reasons already recorded in
[A-19](#a-19--the-split-bars-focus-ring-is-drawn-by-the-bar-not-by-its-slider): the split
bar's slider is `opacity: 0` and cannot show one, and the segmented control's is inset and
uncoloured. The second looks like an oversight and is not — it inherits `currentColor`, which
inverts with the segment (`bg` on an `accent-700` fill at 5.78:1, `text` on the page at
14.79:1), and an `accent-700` ring drawn *inside* an `accent-700` fill would be 1.00:1.

### 3B-6 · The split bar keeps its label rather than §3.1's `aria-label`

*Raised by issue #10. Amends `ui-contract.md` §3.1.*

§3.1 says the split bar's transparent range input "carries `aria-label="Solar share"`". That
was written against the mockup, which hand-rolled the control out of divs and had no label to
associate. #5 shipped a real `<label>` reading `Technology split`.

**Decided.** Keep the label. Adding the `aria-label` would override it, and an accessible name
that does not contain the visible label breaks WCAG 2.5.3 Label in Name — a speech-input user
saying "technology split" would not reach the control. The share the slider actually carries is
announced through `aria-valuetext` ("45% solar / 55% wind"), which is the value channel and the
right place for it.

### 3B-7 · The holdings row is a template; the cash-flow bars are not

*Raised by issue #10. Affects: #11.*

Four of §7.1's eight second signals live in holdings rows — the lock column's mark, `Locked`
and `Not selected` in a row's accessible name, and the Min DSCR breach — and `<tbody>` is
empty because #11 renders it. None of them could be checked, which is the whole reason this
issue is sequenced before group 4.

**Decided.** `portfolio.html` carries `<template x-for="row in rows">` with all sixteen cells,
every figure through `format.js`, and each signal in place. `rows` stays `[]`: the template is
markup, in the same category as the `<th>` elements already sitting there empty-bodied, and
seeding it with specimen figures would be the canned data the repository forbids. Tests render
it from fixtures under `web/tests/helpers/holdings.js`.

The FCFE bars are deliberately **not** templated, although §7.1 owes the negative bar a signal
too. A row is pure markup; a bar is mostly geometry — height, zero-line offset, tick alignment
— and §5.2 assigns that to #11. Templating the accessible half and leaving the geometry would
have handed #11 a data contract this issue has not validated. The required shape is instead
demonstrated on `styleguide.html`, where each bar is a real `<button>` whose readout carries
the sign, and the greyscale guard checks it there.

**#11 should clone both templates rather than hand-rolling rows.** The tests assert the
rendered contract, not the factory, so a replacement that drops a signal fails — but a
replacement that renders rows some other way leaves the live-row assertions with nothing to
inspect.

### 3B-8 · The search standfirst names a population size, and is left alone

*Raised by issue #10. Needs: #3, #11.*

`search.html`'s standfirst is §4's pinned replacement copy: *"Testing 90 candidate portfolios
at a time against the mandate, keeping the best and recombining them."* Two things are wrong
with it. **90 is the Standard effort's population size**, so the sentence is simply false for
Fast (50) and Exhaustive (160). And "recombining" is crossover in plain English — §8 bans the
term, and this is the concept under a different word.

**Not decided here.** The string is pinned verbatim in §4 and asserted by
`wire-contract.test.js`; the issue's instruction is to raise a criterion that looks wrong
rather than edit it. Recorded for #3 and #11.

Recommendation: bind the figure (`<span data-field="candidatesPerRound">`) so it follows the
effort level, and drop the final clause — *"Testing candidate portfolios against the mandate in
rounds, keeping the best."* Neither changes what the screen does.

### 3B-9 · CI now runs the front end

*Raised by issue #10. Affects: every front-end issue.*

`.github/workflows/ci.yml` installed uv, ran `ruff`, `mypy` and `pytest`, and stopped. It never
set up Node, never ran `npm ci`, and never ran the web tests — so nothing under `web/` was
verified on `main`, including #5's axe, contrast, keyboard and copy guards and every
acceptance criterion in this issue.

**Decided.** A `web` job runs `npm --prefix web ci` and `npm --prefix web test`, whose `pretest`
runs the Tailwind build that `offline.test.js` requires. The file is #2's ownership row and #2
is closed; the change is announced on issue #1 rather than left for someone to notice.

### 4B-1 · Epic §7's 30 s does not reproduce, and the masking it prescribes is not the win

*Raised by issue #12. Affects: epic §7's performance table.*

Epic §7 records a known gap — "Exhaustive 160×110 at 2,000 candidates measures 30 s" — and #12
attributes it to the budget repair's `argsort` running over the whole population every generation
"but after a few generations most chromosomes are already within budget". Both halves were
measured before anything was changed.

**The 30 s does not reproduce.** Exhaustive at 2,000 candidates measures **2.2 s** for the search
on an 8-core arm64 box under a load average of 14, and 1.4 s on an idle one. #12's criterion
"materially faster than 30 s" was already true before this issue started, which is worth saying
plainly rather than claiming a 20× win for a change that delivers under 2×.

**Most chromosomes are not within budget.** A census over a real Exhaustive run counts
**17,278 of 17,382 rows (99.4%)** over budget at the point of repair, and 95–99% at every other
width and effort. The utilisation reward actively pushes portfolios onto the equity cap, so
repair leaves almost every chromosome sitting on it and mutation and crossover push almost every
child back over. There is next to nothing for a row mask to skip.

**Decided.** The operator is narrowed on the *other* axis. No row holds more than `width`
projects, so ranked positions beyond `width` are unheld in every row: `held` is `False` there and
the scatter would write back the zeros it started from. The gather, the accumulation and the
scatter therefore stop at `width` — about 22 of 2,000 in practice — while the ranking still runs
over the full row, so sort stability is untouched and the answer is bit-identical.

The row mask ships too, because #12 asks for it and because it covers the one shape where it
would matter — a mandate whose capital dwarfs its pipeline. It is a measured net cost on every
configuration in the shipped calibration, and the numbers are here so that nobody has to take
that on trust. Dropping it is a one-line change: pass `tolerance=None` at `ga.py`'s two call
sites.

The operator alone, fastest of twenty calls, on populations drawn to be over budget at a rate
*more* favourable to the row mask than reality (60–65%, against the 95–99% the census measures):

| candidates | rows | over budget | legacy | rows only | slice only | both | speed-up |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 500 | 48 | 32/48 | 0.38 ms | 0.29 ms | 0.13 ms | 0.12 ms | 3.02× |
| 500 | 88 | 57/88 | 0.68 ms | 0.51 ms | 0.23 ms | 0.22 ms | 3.11× |
| 500 | 158 | 101/158 | 1.21 ms | 0.87 ms | 0.40 ms | 0.37 ms | 3.29× |
| 2,000 | 48 | 30/48 | 1.39 ms | 1.05 ms | 0.37 ms | 0.36 ms | 3.86× |
| 2,000 | 88 | 57/88 | 2.59 ms | 2.05 ms | 0.68 ms | 0.66 ms | 3.92× |
| 2,000 | 158 | 95/158 | 4.61 ms | 3.19 ms | 1.20 ms | 1.13 ms | 4.09× |

The whole search, fastest of nine, `identical` in every cell:

| candidates | effort | legacy | rows only | slice only | both |
|---:|---|---:|---:|---:|---:|
| 500 | fast | 0.031 s | 0.032 s | 0.023 s | 0.026 s |
| 500 | standard | 0.094 s | 0.092 s | 0.064 s | 0.067 s |
| 500 | exhaustive | 0.281 s | 0.329 s | 0.263 s | 0.296 s |
| 2,000 | fast | 0.158 s | 0.172 s | 0.112 s | 0.155 s |
| 2,000 | standard | 0.651 s | 0.694 s | 0.530 s | 0.503 s |
| 2,000 | exhaustive | 2.167 s | 2.278 s | 1.187 s | 1.538 s |

numpy 2.4.6 · BLAS threads 8 · 8 CPUs · load 14.37 · Darwin arm64. **These were taken under
load**, which this document is emphatic about elsewhere: the operator table is stable to a few
percent because it times one call, and the whole-search table wobbles by 10–20% because a search
is a fraction of a second and the load average is not a constant. The direction is consistent
across every run: `slice` beats `legacy`, and `both` is slower than `slice` alone.

Repair was 55% of an Exhaustive run at 2,000 before (argsort 0.224 s, cumsum 0.136 s,
`take_along_axis` 0.115 s, `put_along_axis` 0.112 s of 1.23 s) and is 37% after, with `argsort`
now the dominant remaining term at 0.517 s of 2.18 s. Narrowing that further means giving up
either the stable sort or the full-row ranking, and neither is worth a further 20% here.

### 4B-2 · The row mask is a margin below the budget, not a test against it

*Raised by issue #12. Affects: `optimiser/repair.py`, `optimiser/ga.py`.*

A row whose holdings total exactly the budget has a pairwise row sum of exactly the budget, so a
bare `total > budget` test skips it — while the ranked `cumsum` the operator actually uses
overshoots by a last-bit fraction and drops its final holding. Skipping such a row therefore
keeps a holding the unmasked operator drops, and the masked and unmasked forms answer
differently. Measured at 1.14e-13 on a 40-holding row totalling about 1,034.

This is not a corner case. Epic §6.2 introduced `equity_cap_tolerance_eur` precisely because
"the utilisation reward actively pushes portfolios onto that boundary", so rows sitting exactly
on the cap are the ones the search spends its time on.

**Decided.** `repair_to_budget` takes `tolerance`, a margin *below* the budget: a row is repaired
when its total exceeds `budget - tolerance`, so every row within a tolerance of the cap goes
through the full path and the answer cannot move. `ga.py` passes
`objective.equity_cap_tolerance_eur` — €1 against sums of order €10⁹, five orders of magnitude
more headroom than any reduction-order difference needs, and the same constant epic §6.2
introduced for the same artefact on the same boundary rather than a second one.

`tolerance=None` means no row mask at all, which is the pre-#12 behaviour exactly. That is the
default, so 2A's four direct callers stay green untouched, and it is also the escape hatch if the
row mask is later dropped as 4B-1 suggests it could be.

The row totals are a pairwise `sum`, deliberately not `population @ equity`: a GEMM's reduction
order depends on BLAS blocking and thread count, and the one place in this system permitted to
vary across machines is the fitness the GA quantises, not an operator that decides which holdings
survive.

### 4B-3 · `deterministic_reduction` is a run control, not an assumption

*Raised by issue #12. Affects: `optimiser/ga.py`, `optimiser/aggregate.py`, and 3A's provenance.*

§12 asks for a bit-exact guarantee, and #12 asks for a `deterministic_reduction` flag that swaps
the GEMM for `einsum(optimize=False)` so a golden can be checked across architectures. The
question is where the flag lives. Epic §5 says every rate, weight, floor, clamp, tolerance and
band belongs in the assumption set, and a naive reading puts this there too.

It cannot go there. `assumption_set_id` is the sha256 of every non-metadata section of the
calibration (`config/loader.py:320`), and `generate/draws.py:83` salts each project's PRNG with
it — so a new key in the assumption set changes the id, changes every project's draws, and
obliges 2C to regenerate all 300 committed pipeline files. That is a very large cascade for an
execution mode, and it would break `test_the_committed_pipeline_is_what_the_generator_produces`
on the way.

**Decided.** It is a field on `SearchControls`, beside `effort`, `locked` and `seed` — which that
class's own docstring already describes as "the run controls, which are not part of the mandate".
Epic §5's rule is about numeric calibration, and the existing precedent for an execution mode is
`RunnerMode`, which is a setting rather than an assumption. Default off, so no existing caller
and no stored run changes.

`optimize=False` is the mechanism and not a detail: with optimisation on, einsum may hand a
two-operand contraction to `tensordot` and so back to the GEMM the flag exists to avoid. The flag
would still be set, the tests would still pass on one machine, and the guarantee would be
silently gone — so a source-level guard asserts the keyword rather than trusting it.

Measured at 17× the GEMM at (50, 177), 36× at (90, 500) and 45× at (160, 2000) — which is
2.13 ms per generation at the largest shape, about 0.23 s added to an Exhaustive run. Cheap
enough to be a usable mode rather than a theoretical one.

**Left open, raised for 3A.** `api/service.py:196` still derives the stored
`deterministic_reduction` column from `blas_threads == 1` alone, and the wire has no way to ask
for the flag. A run submitted over HTTP therefore cannot use it, and the column's meaning is now
narrower than its name. Both are 3A's to resolve.

### 4B-4 · The reproducibility guarantee, and what it does not cover

*Raised by issue #12. Affects: §12's sign-off, epic §7's memory budget.*

§12 says "mandate + pipeline hash + assumption set + seed determines the result **exactly**", and
#12 asks for that to be written down honestly rather than asserted.

**Decided.** The guarantee has exactly two tiers, and the second one needs a flag:

- **On a given architecture, unconditionally.** Same mandate, pipeline hash, assumption set,
  seed, engine version and numpy version produce byte-identical served bytes. Asserted on what
  `GET /optimisations/{id}` actually serves, not on the optimiser in isolation, with four fields
  exempt — `runId`, `runRef`, `createdAt`, `durationMs` — and a test asserting that the exemption
  list is exactly those four.
- **Across architectures, with `deterministic_reduction` on.** The reduction order is then fixed
  by shape and dtype rather than by BLAS blocking and thread count.

**Across architectures without the flag, nothing stronger than "very likely" is claimed.** The
search's trajectory turns on `f[a] >= f[b]`; fitness is quantised to 6 dp before every comparison
and the runner pins BLAS to one thread, which makes a divergence vanishingly unlikely. It does
not make it impossible, and a §12 sign-off that claimed otherwise would be a lie the first CI
migration exposed.

Also settled, since it was ambiguous: epic §7's "core arrays under 16 MB at 2,000 candidates"
does not say whether the pipeline's own 30-year statements count. Both readings hold, so there
was no need to choose the one that passes — **3.83 MB** for the search's working set (feature
matrices at both precisions, the population, the aggregation's per-generation temporaries) and
**15.83 MB** once the statements are included, extrapolated from the shipped 300 files to 2,000.
Both are asserted. The inclusive reading has about 1% of headroom, which is the finding worth
recording: one more 30-year matrix on `StatementArrays` would breach epic §7 at 2,000 candidates.

### 4B-5 · Screen parity compares answers exactly and figures within a tolerance

*Raised by issue #12. Affects: `tests/parity/`, and any later change to either implementation.*

`web/js/feasibility.js` and `optimiser/feasibility.py` implement the same nine screens and seven
warnings, and comparing them turns out to need two different kinds of equality.

**Decided.** Compared **exactly**: the eligible count, the total, `runnable`, the warning codes in
order, the eligible ids, and each project's list of failed screens. Those are the answer, and any
difference in them is a bug.

Compared **within 1e-12 relative**: `eligibleCapacityMw`, `eligibleEquity_m`,
`eligibleSolarShare`, `eligibleGearing`, `lockedEquity_m`. Python aggregates in euros and converts
at the API boundary; the JavaScript aggregates in €m because that is what the wire carries.
`(x·10⁶)/(y·10⁶)` is not obliged to equal `x/y` to the last bit, and numpy's pairwise sum is not
obliged to equal a sequential `forEach`. 1e-12 is five orders of magnitude tighter than anything
the UI renders — €1,246.4162355799467m prints as `€1,246m` — and four wider than the ~1e-16 a
unit conversion and a summation order can introduce.

Both sides read the same projects: the payloads are generated from the golden fixtures by
`api/scalars.py`, the real `GET /pipeline` serialiser, and a test fails if they ever stop matching
what the API would serve.

**Two smaller things settled with it.** The nine screens are spelled differently on the two
sides — `country`/`countries`, `stage`/`stages`, `riskCap`/`riskScore`, `currency`/`eurRevenue`,
`notExcluded`/`exclusions` — and the risk screen sits fifth in the JavaScript order and eighth in
the Python one. `docs/api.md` §5's `screensToWiden` uses the Python names and `feasibility.js`
does not expose `screensToWiden` at all, so the JavaScript names are internal: the harness maps
them, pinned at both ends, rather than renaming them and breaking 1D's own tests. Raised for #11.

And `feasibility.js` reads `payload.assumptions.riskCaps`, which `GET /pipeline` does not serve —
the caps live behind `GET /assumptions`. Every existing JS test hand-builds them, so the page as
shipped would throw on a real payload. The generated payload carries them so the harness can
run; the gap is #11's.

### 4B-6 · Three ways the two feasibility implementations had drifted, and one left alone

*Raised by issue #12. Affects: `web/js/feasibility.js`, #11.*

The thirty parity pairs found four divergences on their first run. Three changed the eligible
count, which is the failure the parity suite exists to catch: the mandate footer promising a
candidate count the run would not deliver.

**Decided — fixed in `feasibility.js`, each with a pair that fails without it.**

- **A lock re-admits.** `screens.py` computes `eligible = survives_every_screen | locked`, so a
  locked project that fails a screen stays in the pool; `feasibility.js` filtered it out. Six of
  the thirty pairs catch this. An exclusion still beats a lock, on both sides.
- **`UK` collapses onto `GB`.** `pipeline-schema.md` §4.1 keeps `UK` as an alias and the loader
  normalises files to `GB`; `mandate.html`'s country chips emit `UK`. Python normalises both ends
  through `normalise_country_code`; the JavaScript compared raw strings, so every British project
  was screened out client-side while the server admitted it. Now normalised inside the screen, so
  it is right however the screen is called — which is what keeps 1D's direct test of
  `screens.country({countryCode: 'UK'}, …)` green.
- **Locked equity subtracts exclusions.** `feasibility.py` takes `set(locked) - set(excluded)`
  before summing; the JavaScript summed every locked id, so an excluded project was still
  committing capital in the footer.
- **Capital absorption compares a ratio** (`equity / capital < 0.9`) rather than a product
  (`equity < capital * 0.9`), matching `feasibility.py`, so a pool absorbing exactly the floor
  lands on the same side on both sides. The two forms differ only in the last bits and only
  exactly on the boundary — which is one of the four boundary pairs.

**Decided — left alone, and asserted instead.** With no eligible pool, `feasibility.js` returns
`NaN` for `eligibleSolarShare` and `eligibleGearing` and renders an em dash; `feasibility.py`
returns `0.0`. Neither is wrong. The em dash is epic §5's rule — undefined is a dash, never a
zero — and 1D asserts it directly ("there is no mix without a pool", "never 0%"). The `0.0` is
forced: `PreviewResponse` types both fields as `float` under `allow_inf_nan=False`, so the wire
cannot carry a `NaN` and the server has no way to say "undefined" here.

Closing it means either dropping 1D's em dash or making two wire fields nullable, and the wire is
a shared contract with a named owner. So each side's documented value is asserted exactly — a
JavaScript `0` and a Python `NaN` both fail, which is tighter than the tolerance it replaces —
and the contract question goes to #1: should `eligibleSolarShare` and `eligibleGearing` be
`float | None`?

### 4B-7 · Two of #12's criteria describe an engine 2A did not build

*Raised by issue #12. Affects: #12's acceptance criteria.*

Two criteria are written in terms that do not match the implementation, and both were guarded for
their intent rather than restated to match the criterion.

**"`irr_bisect` is called exactly `n_eligible + 1` times per run."** There is no `irr_bisect`;
the solver is `economics/irr.py::irr`, and it is called **twice** per run — once on an
`(n, hold)` matrix covering every loaded project in a single vectorised bisection, and once on
the portfolio's own `(1, hold)` cash flow. The criterion describes a scalar per-project solver.
2A's vectorised one is strictly better and satisfies what §10.3 actually forbids, which is IRR
solving *inside the fitness loop*.

**Decided.** The guard asserts what §10.3 forbids: exactly two bisections, the first vectorised
over more than one row, and **no solve between the first and last scoring of the run**. The
ordering assertion is the one that catches the regression — a per-project solve moved inside the
loop would still be "a few calls" by some countings, but it would not be outside the scoring
window.

It is hooked at `npv` rather than at `irr`, which is the difference between a guard and a
decoration: `irr` is imported by name into two modules, so patching those two bindings catches
only the call sites that exist today, and a solve added inside the loop through a fresh
`from … import irr` in `ga.py` would be invisible. `npv` is reachable only from inside `irr`,
which resolves it from its own module globals at call time, so one hook sees every solve however
`irr` was imported. Verified by adding exactly that call on purpose: 3,774 NPV evaluations against
the 204 that two bisections make, where the binding-level hook had reported nothing wrong.

**"One fitness call per generation."** There are two kinds. `ga.py` scores the whole population
once per generation in float32 — the hot path, and the count that must not grow — and then
re-scores the *leader alone* in float64 once per generation, once more for the winner, and once
in `build_result`, so that nothing reported inherits the hot path's precision. That is deliberate
and `ga.py`'s own docstring says so.

**Decided.** Both counts are pinned separately, and the dtypes with them. Collapsing them into
one number would let a population-scale call hide behind a one-row one, and asserting the dtypes
at the same time is epic §5's float32 invariant — one `astype` moved by a line and either the hot
path loses its speed or a reported metric inherits its precision.

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
| 2026-09-21 | #6 | 2A-1 — mandate-independent derivations live in `pipeline`; `api.md` §1.4's split is the structural one. |
| 2026-09-21 | #6 | 2A-2 — the IRR bracket is #6's `[-0.9999, 10.0]`; an all-zero series has no rate, where the reference returns one. |
| 2026-09-21 | #6 | 2A-3 — min DSCR screens raw; the reference's 2 dp rounding decides 1.2449 against a 1.25 floor. |
| 2026-09-21 | #6 | 2A-4 — neither documented LCOE basis reproduces the reference; `real_from_base` does, 48 of 48. |
| 2026-09-21 | #6 | 2A-5 — merchant exposure stays capex-weighted on `ppaShare`; the revenue-weighted share is reported beside it. N-1 still open. |
| 2026-09-21 | #6 | 2A-6 — the €1 cap tolerance admits `OBJ-205`; the other 205 objective cases reproduce to 1e-12. |
| 2026-09-21 | #6 | 2A-7 — quantisation belongs to the comparison, not to the objective. |
| 2026-09-21 | #6 | 2A-8 — per-screen drop counts are independent and overlap; §13 cannot name a screen otherwise. |
| 2026-09-21 | #6 | 2A-9 — the `UK` alias applies to mandates too; it had rejected every British project. |
| 2026-09-21 | #6 | 2A-10/2A-11 — mixed-width ids and duplicate ids fail the load, not a file. |
| 2026-09-21 | #6 | 2A-12 — §7.7 is checked multiplied, so the €0.01m tolerance means something and nothing divides. |
| 2026-09-21 | #6 | 2A-13 — budget repair keeps a prefix and spends on locks first; a smarter repair would bias the search. |
| 2026-09-21 | #6 | 2A-14 — five PRNG draws per generation; the two tournaments must be independent. |
| 2026-09-21 | #6 | 2A-15/2A-16 — the core emits euros and plain dataclasses; tiles 1 and 3 carry deviations, not `ui-contract`'s bands. |
| 2026-09-21 | #6 | 2A-17 — an undefined blend falls back to the hurdle, so the return term contributes zero rather than the rail. |
| 2026-09-21 | #6 | 2A-18 — Standard at 500 candidates measures 107 ms against a 5 s budget; §7's 30 s Exhaustive-at-2,000 gap now measures 1.18 s. |
| 2026-09-23 | #6 | 2A-19 — an empty eligible pool raises only `NO_CANDIDATES`; the four advisory checks are guarded as `feasibility.js` guards them. |
| 2026-09-23 | #6 | 2A-20 — locks still re-admit past the screens, per #6 and §13; challenged in review and kept, with the alternative recorded. |
| 2026-09-23 | #6 | Review fixes: `paybackYear` is a calendar year at the result boundary (it was a period, which `ProjectScalars` rejects); the CLI validates through the pydantic `Mandate` and refuses §13's two blocking conditions; a non-UTF-8 file is rejected per-file rather than aborting the load. |
| 2026-09-22 | #7 | 2B-1 — CSV cells carry raw numbers per `api.md` §9; §14 formatting serves the comment header, where the euro signs survive on the BOM. `ui-contract.md` §2 needs narrowing. |
| 2026-09-22 | #7 | 2B-2 — `holdings.csv` is the seventeen columns `api.md` §9.1 names, not the eighteen in #7's body. |
| 2026-09-22 | #7 | 2B-3/2B-4 — `run_reference` is the monotonic integer and the sort key; `run_ref` is its `A-4` label; both come from a `run_sequence` counter claimed under an IMMEDIATE transaction. |
| 2026-09-22 | #7 | 2B-5 — inputs are frozen at submission by trigger; a second finish raises unless the bytes are identical, which is a redelivery. |
| 2026-09-22 | #7 | 2B-6 — the assumption snapshot is keyed on its own payload digest, and `snapshot_hash` ≠ `assumption_set_hash` by construction. |
| 2026-09-22 | #7 | 2B-7 — `ValidationStatus` is the store's own four-value vocabulary; #6's report is stored opaquely beside it. |
| 2026-09-22 | #7 | 2B-8 — `base_year` lives on `pipeline_snapshot`; the run result carries none, which also blocks #11's chart labels. Raised on #1. |
| 2026-09-22 | #7 | 2B-9 — ULIDs on the standard library, no new dependency, and no `# structural:` escape spent. |
| 2026-09-23 | #7 | 2B-10 — the submission names its assumption snapshot; inferring it from `(id, hash)` attached runs to the wrong payload, since two snapshots share both by design. |
| 2026-09-23 | #7 | 2B-11 — the whole provenance is compared at finish, the curve is reconciled by value, the event log closes when the run does, and a redelivery must repeat the failure and warnings too. |
| 2026-09-23 | #7 | 2B-12 — a pipeline hash digests the files, not the base year or verdict stored beside them, so a disagreeing re-record raises instead of being silently dropped. |
| 2026-09-23 | #7 | 2B-13 — percentages scale inside `Decimal`; a project name cannot become an Excel formula; the CSV comment header is newline-safe; the schema version guard refuses un-migratable files. |
| 2026-09-22 | #8 | A-23 — the exit year's own FCFE counts alongside the terminal value; 144/144 against the oracle. |
| 2026-09-22 | #8 | A-24 — LCOE discounts unescalated opex from the base year; neither reading issue #8 names matches. |
| 2026-09-22 | #8 | A-25 — debt is sized by dividing by the annuity **payment** factor; issue #8's prose divides by the PV factor. |
| 2026-09-22 | #8 | A-26 — a draw `Range` states its span; a test `Band` states its high. |
| 2026-09-22 | #8 | A-27 — two seeding modes: index-keyed for parity, `(id, assumption_set_id)` for the shipped pipeline. |
| 2026-09-22 | #8 | A-28 — issue #8's provenance names mapped onto §8.1's closed vocabulary. |
| 2026-09-22 | #8 | A-29 — a project outside the min-DSCR band is redrawn from its own stream, bounded. |
| 2026-09-22 | #8 | A-30 — workbooks are written at full double precision; Excel's own fifteen digits are not. |
| 2026-09-22 | #8 | A-31 — the exit bridge reads `debtSchedule.closing` instead of re-amortising. |
| 2026-09-22 | #8 | The `reconciled` file contract is applied: fixtures regenerated, template and §4.6 updated. |
| 2026-09-22 | #8 | `assumption_set_id` moves to 9e5bfe51aeb2f40a as the generator and IRR bracket land. |
| 2026-09-23 | #8 | A-32 — a regenerated pipeline replaces what it generated; a seed change used to duplicate every id. |
| 2026-09-23 | #8 | A-33 — tie-out failures block ingestion; a house-model variance stays advisory. |
| 2026-09-23 | #8 | A-34 — project text is written as an explicit string cell, never as an Excel formula. |
| 2026-09-23 | #8 | A-29 extended — `pipeline generate` refuses to write when any project is outside the band. |
| 2026-09-23 | #8 | A-35 — 2C converges onto 2A's `economics/` and `pipeline/validator.py`; the LCOE basis is spelled 2A's way and the duplicate IRR bracket is gone. |
| 2026-09-23 | #8 | A-29 extended again — a site the model cannot place inside §10's band is skipped and the pool drawn deeper, rather than shipped with a warning. |
| 2026-09-24 | #9 | 3A-1 — a run in flight is 200 with `status`, not 202. |
| 2026-09-24 | #9 | 3A-2 — `id:` is the generation number; terminal frames carry none; the failure frame is `failed`. |
| 2026-09-24 | #9 | 3A-3 — `410 RUN_EXPIRED` reserved and never emitted; the durable log means no stream expires. |
| 2026-09-24 | #9 | 3A-4 — an empty `countries` or `stages` is 400 `INVALID_MANDATE`, conforming to 1A's model. |
| 2026-09-24 | #9 | 3A-5 — `severity` is `alert` or `info`; `runnable` carries the block. `feasibility.js` still says `blocking`. |
| 2026-09-24 | #9 | 3A-6 — the API draws a 63-bit seed; numpy's 128-bit entropy does not fit `run.seed`. |
| 2026-09-24 | #9 | 3A-7 — `blasThreads` records the count the run actually had, not the one requested. |
| 2026-09-24 | #9 | 3A-8 — the worker re-verifies the pipeline hash it was given. |
| 2026-09-24 | #9 | 3A-9 — the committee pack is server-rendered with no JavaScript; the projection is pinned to d3-geo at 3.4e-13 px. |
| 2026-09-24 | #9 | 3A-10 — the pack's display constants live in `pack-layout.json`, checked against `ui-contract.md`. |
| 2026-09-24 | #9 | 3A-11 — §5.4's sentences live in `api/messages.py`, pinned to `ui-contract.md` §3.5 and §3.6. |
| 2026-09-24 | #9 | 3A-12 — the snapshot's validation report is stamped with the load's own time. |
| 2026-09-24 | #9 | 3A-13 — `assumptionSetId` must name the loaded set; a warning's id is recovered and checked, never guessed. |
| 2026-09-24 | #9 | 3A-14 — a snapshot's validation report excludes `loadedAt` and `durationMs`; a restart used to 409 every run. |
| 2026-09-24 | #9 | 3A-15 — one sentence on the wire, the traceback in the store and the log; a cancellation is recorded as one. |
| 2026-09-24 | #10 | 3B-1 — contrast audit over the pairings in use, generated and guarded; fifteen pairs, all passing. |
| 2026-09-24 | #10 | 3B-2 — one exported status vocabulary; §7.1's marks held as exact code points. |
| 2026-09-24 | #10 | 3B-3 — live regions are derived sr-only companions, not the figures; `ui-contract.md` §7.2 amended. |
| 2026-09-24 | #10 | 3B-4 — the fifteen holdings sort keys are §5.4's wire names, and `aria-sort` follows the sort state. |
| 2026-09-24 | #10 | 3B-5 — the focus ring is `accent-700`, measured against every ground; `ui-contract.md` §7.2 corrected. |
| 2026-09-24 | #10 | 3B-6 — the split bar keeps its `<label>`; `ui-contract.md` §3.1's `aria-label` would break WCAG 2.5.3. |
| 2026-09-24 | #10 | 3B-7 — the holdings row ships as a template; the FCFE bars stay #11's, with the shape shown on the style guide. |
| 2026-09-24 | #10 | 3B-8 — the search standfirst's hard-coded 90 and "recombining" raised for #3, copy left as §4 pins it. |
| 2026-09-24 | #10 | 3B-9 — CI gains a web job; nothing under `web/` was verified on `main` before. |
| 2026-09-24 | #10 | `ui-contract.md` §7.3 corrected: muted is `neutral-700` at 5.87:1, not `text` at 55%. |
| 2026-09-24 | #12 | 4B-1 — epic §7's 30 s does not reproduce (2.2 s measured); 99.4% of rows are over budget, so the column slice is the win and the row mask is a measured cost. |
| 2026-09-24 | #12 | 4B-2 — the row mask is a margin *below* the budget, and it is `objective.equity_cap_tolerance_eur`; `None` keeps the pre-#12 behaviour. |
| 2026-09-24 | #12 | 4B-3 — `deterministic_reduction` is a `SearchControls` field, not an assumption: a new TOML key would change `assumption_set_id` and reprice all 300 files. |
| 2026-09-24 | #12 | 4B-4 — the §12 guarantee stated with its scope: bit-exact per architecture unconditionally, across architectures with the flag. Epic §7's 16 MB holds on both readings, 3.83 MB and 15.83 MB. |
| 2026-09-24 | #12 | 4B-5 — parity compares answers exactly and the six figures at 1e-12; the nine screen names are mapped, not renamed. `GET /pipeline` serves no `riskCaps`, raised for #11. |
| 2026-09-24 | #12 | 4B-6 — three `feasibility.js` divergences fixed (locks re-admit, `UK`→`GB`, locked equity less exclusions, absorption as a ratio); the empty-pool NaN-vs-0.0 left alone and asserted, raised for #1. |
| 2026-09-24 | #12 | 4B-7 — `irr` is called twice per run, not `n_eligible + 1`, and is guarded at `npv`; "one fitness call per generation" is one population-scale call plus three one-row re-scores. |
