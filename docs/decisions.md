# Decisions

Spec deviations, resolved ambiguities and open questions.

> **Ownership note.** `docs/*.md` belongs to issue 1B (#3). 1B had not yet created this file
> when 1C (#4) needed to record against it, and 1C's issue instructs it to "append to
> `docs/decisions.md` for any adjustment you make" — so 1C created it with this header and the
> 1C section below. 1B should restructure the document as it sees fit; the 1C entries only need
> to survive somewhere, not to keep this shape.

---

## 1C — the JavaScript reference as golden fixtures (#4)

Every decision below concerns `tools/extract_reference.mjs` and the fixtures under
`tests/golden/fixtures/`. Figures were measured by running the extracted reference.

### 1C-1 · Construction funding: capex and the debt drawdown are booked pro-rata

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

**This contradicts the recommended answer to epic open question 8.** If a non-EUR file must be
denominated in its own currency, these 14 golden files do not exemplify that rule and 2A must not
infer it from them. Raised on #1.

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
