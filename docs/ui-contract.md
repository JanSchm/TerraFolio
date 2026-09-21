# UI contract

Every control, tile, table column, chart and warning string, with its format. Extracted from the
design mockup (`Portfolio Optimiser (standalone).html`) rather than invented, and reconciled against
[`spec.md`](spec.md) where the two disagree.

**Who implements what.** Issue #5 builds the design system, the controls and the page shells; #10
takes it through accessibility and copy conformance; #11 wires it to the live API. This document is
the contract all three work to. Where it departs from the mockup it says so and why.

Section numbers cited as §n refer to [`spec.md`](spec.md); decisions as A-n to
[`decisions.md`](decisions.md).

---

## 1. Design tokens

Declared once on `:root` and used by name. No component hard-codes a colour.

### 1.1 Colour

| Token | Value | Use |
|---|---|---|
| `--color-bg` | `#f2f2f3` | Page ground. Also the knob and text on a filled accent surface. |
| `--color-surface` | `#e9e9ea` | Raised panels. |
| `--color-text` | `#1d1f20` | Body text. |
| `--color-accent` | `#5980a6` | Primary action, active chip, solar in the split bar, positive FCFE bars. |
| `--color-accent-2` | `#728fab` | Secondary series. |
| `--color-divider` | `#1d1f20` at 16% | Every hairline: panel borders, table rules, chart axes. |

Two tonal ramps, `--color-neutral-100 … 900` and `--color-accent-100 … 900`, generated in OKLCH on
one shared lightness scale so the same step of either role matches the other in visual value. A
third, `--color-accent-2-100 … 900`, exists for a second series.

| Step | Neutral | Accent | Accent 2 |
|---|---|---|---|
| 100 | `#f5f5f8` | `#eef6ff` | `#eef6ff` |
| 200 | `#e7e7ea` | `#d6ebff` | `#d6ebff` |
| 300 | `#d4d4d7` | `#b5d9fd` | `#bdd8f2` |
| 400 | `#b7b7ba` | `#94bce3` | `#9ebbd8` |
| 500 | `#98989b` | `#749dc4` | `#7e9cb8` |
| 600 | `#7a7a7d` | `#597ea3` | `#627d98` |
| 700 | `#5d5d60` | `#416180` | `#486077` |
| 800 | `#424244` | `#2c455d` | `#314457` |
| 900 | `#2b2b2d` | `#1d2d3d` | `#1f2d3a` |

Semantic roles, which are what components reference:

| Role | Token | Meaning |
|---|---|---|
| Compliant | `--color-accent-700` | A metric meets its mandate target or cap. |
| Neutral | `--color-neutral-700` | A metric has no target, or the target does not apply. |
| Alert | `--color-accent-800` | A metric breaches its mandate target or cap. **Never on its own — see [§7](#7-accessibility).** |
| Muted text | `--color-text` at 55% | Labels, sub-labels, captions. |

There is no red in the palette. "Red below the mandate floor" (§7.4) is rendered in the alert tone
plus a mark.

### 1.2 Type

| Token | Value |
|---|---|
| `--font-heading` | `"Barlow Condensed", system-ui, sans-serif` at weight 600 |
| `--font-body` | `"Barlow", system-ui, sans-serif` |

Body is 15px / 1.55 / weight 400. Headings are 42 / 32 / 25 / 20 / 16 / 13px for h1…h6, line-height
1.12, letter-spacing −0.015em. `h6` is the section eyebrow: uppercase, letter-spacing 0.08em.

Every **number** is set in `--font-heading` — tiles, live figures, table IRR cells, the split-bar
readout. That is what makes a figure scannable down a column.

### 1.3 Space, radius, elevation

`--space-1 … 8`: 3.4, 6.8, 10.2, 13.6, 20.4, 27.2px. `--radius-sm/md/lg`: 2, 4, 7px.
`--shadow-sm/md/lg`: `0 1px 2px`, `0 3px 10px`, `0 12px 32px` of `#2b2b2d` at 14 / 16 / 22%.

The `.blueprint` wrapper — a 1px divider border with four registration corner marks, drawn 6px
outside the box — frames every panel, figure and dialog. Corners are 11px, `--color-text` at 55%.

---

## 2. Number formats

**Implemented once, in `web/js/format.js`. No component formats a number itself.** §14 and epic §5.

| Kind | Format | Example |
|---|---|---|
| Money | Euros in millions, thousands separators, **no decimals**, `€` prefix and `m` suffix | `€1,200m` |
| Capacity | Integer with thousands separators, `MW` suffix | `1,661 MW` |
| Generation | Integer with thousands separators, `GWh` suffix | `3,284 GWh` |
| IRR and any return | **One** decimal, `%` suffix | `12.4%` |
| Share, leverage, concentration | **Zero** decimals, `%` suffix | `72%` |
| Capacity factor | **One** decimal, `%` suffix | `23.0%` |
| DSCR, MOIC, exit multiple | **Two** decimals, multiplication sign `×` | `1.38×` |
| Price | Integer, `€` prefix, unit suffix | `€39/MWh` |
| Risk score | One decimal | `2.7` |
| Year | Four digits, no separator | `2028` |
| Inline separator | Middle dot with spaces | `A · B` |
| **Undefined** | **Em dash** | `—` |

Locale is `en-GB` for grouping. Rounding is half-up at the stated precision.

**The em dash rule is load-bearing.** An undefined IRR arrives as JSON `null` and renders `—`. It is
never `0.0`, never `0.0%`, never blank, and it is excluded from every weighted average (§13, A-6).
This holds at each hand-off: engine `NaN` → API `null` → UI `—`.

---

## 3. Screen 01 — Mandate

Nav, sticky: `MERIDIAN CAPITAL` · `Renewables portfolio optimiser`, and the step indicator
`01 Mandate — 02 Search — 03 Portfolio` with the current step in the accent tone.

H1 `Define the mandate`. Standfirst: *The optimiser searches the live candidate pipeline for the
subset of projects that best satisfies the objective while respecting every hard constraint below.*
Right-aligned eyebrow: `Pipeline: {n} screened candidates · {mw} MW`.

Three panels side by side, each a `.blueprint`.

### 3.1 Panel — Objective

*What the portfolio is asked to achieve.*

| Control | Type | Range | Step | Default | Feeds |
|---|---|---|---|---|---|
| Available equity capital | Slider, €m | 200–4,000 | 50 | 1,200 | Hard cap. The §10.2 rejection test and the capital-utilisation reward. |
| Capacity target | Slider, MW | 200–4,000 | 50 | 1,500 | The capacity-match term, normalised by the target. |
| Technology split | Split bar, % solar | 0–100 | 5 | 45 | The technology-split term. Offshore wind counts as wind. |
| Target equity IRR (hurdle) | Slider, % | 6–18 | 0.5 | 11 | The return-versus-hurdle term. Never a hard reject. |
| Hold period | Number, years | 5–30 | 1 | 10 | The exit year for every IRR and MOIC on the page. |
| Exit year | Text, **read-only** | — | — | `baseYear + hold` | Display only. |

The split bar is a 30px track: solar fills from the left in `--color-accent` labelled `SOLAR`, wind
fills the remainder in `--color-accent-300` labelled `WIND`, with a 9px `--color-text` handle at the
boundary. A transparent range input overlays it and carries `aria-label="Solar share"`. Readout:
`{s}% solar / {w}% wind`.

### 3.2 Panel — Hard constraints

*Breaches are rejected, not penalised.*

| Control | Type | Range | Step | Default | Feeds |
|---|---|---|---|---|---|
| Eligible countries | Multi-select chips, 14 markets | — | — | all on | Pre-screen. Empty selection disables the run. |
| Min portfolio leverage | Slider, % | 0–85 | 1 | 60 | The leverage-shortfall penalty. |
| Min DSCR (P50) | Number, × | 1.00–2.00 | 0.05 | 1.25 | Project-level pre-screen, and the holdings-table floor. |
| Max merchant % | Number, % | 0–100 | 5 | 35 | The merchant-overshoot penalty. |
| Max / country % | Number, % | 10–100 | 5 | 35 | The country-concentration penalty. |
| Max / project % | Number, % | 5–100 | 5 | 15 | The single-project-concentration penalty. |
| COD from | Number, year | 2027–2033 | 1 | 2027 | Pre-screen. |
| COD to | Number, year | 2027–2033 | 1 | 2032 | Pre-screen. `from > to` is a validation error. |

Countries are `ES PT IT GR FR DE PL RO NL DK IE SE FI UK`, each chip a flag plus the country name. A
ghost button toggles `Select all` / `Clear all`.

### 3.3 Panel — Risk & execution

*Screens applied before the search begins.*

| Control | Type | Options | Default |
|---|---|---|---|
| Development risk appetite | Segmented | Low / Balanced / High | Balanced |
| Stages in scope | Multi-select chips | Greenfield / Ready-to-build / Construction | all on |
| Grid connection secured only | Toggle | — | Off |
| EUR-denominated revenue only | Toggle | — | Off |
| O&M partner contracted | Toggle | — | Off |

Risk appetite sets **two** caps, both from the assumption set: a per-project pre-screen at 2.6 / 3.6
/ 5.0, and a capex-weighted portfolio-average penalty at 2.4 / 3.2 / 4.2 (§5.3). Its note changes
with the selection:

- Low — *Excludes anything above risk score 2.6 — mostly RTB and construction assets.*
- Balanced — *Admits development assets up to risk score 3.6.*
- High — *Full pipeline, including early-stage greenfield.*

Toggle notes, verbatim:

- Grid connection secured only — *Drops projects without a firm connection agreement*
- EUR-denominated revenue only — *Excludes PLN, RON, DKK, SEK and GBP exposure*
- O&M partner contracted — *Requires a signed long-term service agreement*

### 3.4 Footer — live feasibility

Fixed to the bottom, recomputed on **every** change, **client-side**, budget under 100 ms (§12).
Three figures and the run button:

| Label | Value |
|---|---|
| `Candidates passing screens` | `{eligible}` with `of {total}` in muted text |
| `Eligible capacity` | `{mw} MW` |
| `Equity required at full draw` | `€{equity}m` |

Primary action `RUN OPTIMISATION`, disabled when no candidate passes.

### 3.5 Warning strings

Verbatim from the mockup, **in the §5.4 order of severity**, which is not the order the mockup emits
them in (A-5). Each carries a mark as well as a tone, so none is colour-only.

| # | Mark | Tone | String |
|---|---|---|---|
| 1 | `×` | alert | `No candidates pass the current screens. Widen countries, stages or the COD window.` |
| 2 | `!` | alert | `Eligible pipeline is {mw} MW — below the {target} MW target.` |
| 3 | `!` | alert | `Minimum leverage of {minLev}% exceeds what the eligible pool supports ({poolLev}%).` |
| 4 | `!` | neutral | `Solar target of {solar}% may be unreachable: eligible pool is {poolSolar}% solar.` |
| 5 | `!` | neutral | `Full pipeline absorbs only €{equity}m of the €{capital}m available.` |
| 6 | `•` | neutral | `{n} project(s) locked in; {m} excluded.` |

Trigger conditions, in the same order: no candidate passes (and **only** this one disables the run);
eligible capacity below the capacity target; pool leverage below the minimum; eligible solar share
more than 20 points from the target; eligible equity below 90% of available capital; any lock or
exclusion set.

All but the first are advisory. The user is allowed to run an infeasible-looking mandate and see how
close the optimiser gets (§5.4).

### 3.6 Blocking error

§13 requires one case to block rather than warn: **locked projects alone exceed available capital**.
The run button is disabled and the message names which locks to release. The API returns `422` for
the same condition (see [`api.md`](api.md)).

---

## 4. Screen 02 — Search

H1 `Searching the pipeline`, with a spinner. Standfirst states what is happening in investor
language.

**The mockup's copy on this screen does not conform to §14** and must not be carried over. §14
removes algorithm-internal vocabulary — population, crossover, mutation, fitness, generation — from
user-facing copy and confines it to technical documentation and the run record. Replacements:

| Mockup copy | Replacement |
|---|---|
| `Searching the solution space` | `Searching the pipeline` |
| `Population of 90 candidate portfolios, tournament selection, uniform crossover, 2.5% mutation. Elite carried forward each generation.` | `Testing 90 candidate portfolios at a time against the mandate, keeping the best and recombining them.` |
| `Fitness convergence` | `Progress towards the mandate` |
| `GENERATION 07 / 60` | `ROUND 07 / 60` |
| `Best fitness` (legend) | `Best portfolio` |
| `Population mean` (legend) | `Average of all candidates` |
| `Best fitness` (live figure) | `Mandate score` |
| `seeding population · evaluating constraint feasibility` | `building the first candidates · checking them against the constraints` |
| `crossover and mutation · pruning infeasible portfolios` | `recombining the best candidates · discarding those that breach a constraint` |
| `converging on elite solution · computing levered returns` | `settling on the best portfolio · computing levered returns` |

Elements:

| Element | Spec |
|---|---|
| Round counter | `ROUND {n} / {total}`, zero-padded to two digits, heading font, letter-spacing 0.1em |
| Progress bar | 4px, `--color-neutral-200` track, `--color-accent` fill at `round ÷ total` |
| Convergence chart | `viewBox="0 0 600 150"`, `preserveAspectRatio="none"`, 170px tall, axes on the bottom and left in `--color-divider` |
| Best series | Polyline, `--color-accent-700`, 2px, `vector-effect="non-scaling-stroke"` |
| Mean series | Polyline, `--color-accent-300`, 1.5px, same |
| Status line | Blinking, heading font, 12.5px, `--color-neutral-700` |

Five live figures from the running best portfolio: **Mandate score** (3 dp), **Capacity** (`MW`),
**Projects** (integer), **Equity drawn** (`€…m`), **Blended IRR** (one decimal). Each `—` until the
first round arrives.

The curves are driven by **real per-generation data streamed from the engine**, never a simulated
animation (§6). If the run completes in under 1.5 s, hold the screen for that minimum so the
transition is readable.

---

## 5. Screen 03 — Portfolio

H1 `Suggested portfolio`. Sub-line:
`{mw} MW across {n} projects in {c} countries · {hold}-year hold · run {ref}`.

Actions, right: `Edit mandate` (secondary) · `Export` (secondary) · `Re-run` (primary), relabelling
to `Re-run with changes` whenever the mandate, locks or exclusions have moved since the run.

### 5.1 The twelve headline tiles

One strip, in this order. Each tile is label (uppercase eyebrow, 11.5px), value (heading font,
33px), sub-label (13px). **Compliance tone**, defined in [§7](#7-accessibility), is `compliant` when
the rule holds, `alert` when it breaches, and `neutral` where no mandate target applies.

| # | Label | Value | Sub-label | Compliance rule |
|---|---|---|---|---|
| 1 | Installed capacity | `{mw} MW` | `target {target} MW` | within 8% of target |
| 2 | Projects | `{n}` | `{s} solar · {w} wind` | neutral |
| 3 | Technology split | `{pct}% solar` | `target {t}% solar` | within 8 points of target |
| 4 | Equity required | `€{equity}m` | `of €{capital}m · {pct}% deployed` | neutral |
| 5 | Total project cost | `€{capex}m` | `€{debt}m senior debt` | neutral |
| 6 | Equity IRR ({hold}y) | `{irr}%` or `—` | `hurdle {h}% · {moic}× MOIC` | IRR ≥ hurdle. **Highlighted panel**, `--color-accent-100` |
| 7 | Leverage | `{pct}%` | `min {minLev}% · DSCR floor {worst}×` | leverage ≥ the minimum |
| 8 | Weighted LCOE | `€{lcoe}` | `per MWh, 6% real` | neutral |
| 9 | Annual generation | `{gwh} GWh` | `{kt} kt CO₂ avoided p.a.` | neutral |
| 10 | 30-year FCFE | `€{sum}m` | `undiscounted, post debt` | neutral |
| 11 | Merchant exposure | `{pct}%` | `cap {cap}%` | exposure ≤ cap |
| 12 | Largest country | `{pct}%` | `cap {cap}% · risk score {r}` | share ≤ cap |

Tile 6 renders `—` when portfolio IRR is undefined, and the MOIC clause is dropped rather than shown
as `0.00×`. Tile 9's CO₂ factor (0.32 t/MWh) comes from the assumption set, never a literal.

Tile 1's sub-label shows the **shortfall against target** when the capacity target is unreachable
(§13); the run still returns the best feasible portfolio.

### 5.2 Cash-flow chart

Thirty bars, one per year from the base year, of **`cashflow30Y_m`** — the series that carries **no**
terminal value (A-6). Drawing it from the hold-truncated series is the single most likely silent bug
in the feature.

| Element | Spec |
|---|---|
| Title | `Free cash flow to equity — 30 years` |
| Sub-title | `€m, nominal, post debt service and tax · {hover}` |
| Plot height | 290px, zero line positioned at `max ÷ (max − min)` |
| Positive bar | `--color-accent`, `--color-accent-800` on hover |
| Negative bar | `--color-accent-300`, below the zero line |
| Axis labels | `€{max}m` / `0` / `€{min}m`, left, 11.5px |
| Ticks | Every fifth year, centred under its bar |
| Hover readout | `{year}: €{value}m`, one decimal; `hover a bar` when idle |
| Caption | `Negative years are equity draw-down during construction. Cumulative undiscounted FCFE over 30 years: €{sum}m.` |

Each bar is a focusable control carrying its year and amount, so the series is reachable by keyboard
and by a screen reader, not only by pointer hover.

### 5.3 Site map

| Element | Spec |
|---|---|
| Title | `Selected sites` |
| Projection | `d3.geoMercator`, centre `[12, 55]`, scale `width × 1.15`, 300px tall |
| Geometry | Vendored Natural Earth 110m countries, `web/public/countries-110m.json`. Never a hand-drawn outline (§7.3). |
| Country fill | `--color-accent-200` where the portfolio holds an asset, `--color-neutral-200` otherwise, `--color-divider` stroke at 0.6px |
| Marker | Circle, `r = max(3, √MW × 0.42)`, fill-opacity 0.82, 1px `--color-bg` stroke |
| Solar marker | `--color-accent-700` |
| Wind marker | `--color-accent-400` |
| Legend | `Solar` · `Wind` · `Marker area ∝ MW` |
| Failure | Panel degrades to the notice `Map data unavailable.` The rest of the page is unaffected (§13). |

### 5.4 Holdings table

Header row: `Holdings`, `{shown} of {total}` and the controls — a free-text `Search project` box,
three selects (`All countries` / `All technologies` / `All stages`) and a
`Show all candidates` / `Showing all candidates` toggle chip. Sort and filter must complete in under
50 ms at 500 rows (§12), so both are client-side over data already in hand.

Sixteen columns:

| # | Column | Align | Format | Sort key |
|---|---|---|---|---|
| 1 | *(lock)* | left | `■` locked / `□` unlocked | not sortable |
| 2 | Project | left | Name in heading font, `{country} · {id}` beneath | `name` |
| 3 | Technology | left | `Solar` / `Wind` / `Offshore wind` | `tech` |
| 4 | Stage | left | `Greenfield` / `Ready-to-build` / `Construction` | `stage` |
| 5 | MW | right | integer | `mw` |
| 6 | COD | right | year | `cod` |
| 7 | Capex €m | right | integer | `capex` |
| 8 | Equity €m | right | integer | `equity` |
| 9 | Lev | right | `{n}%`, 0 dp | `lev` |
| 10 | Cap. factor | right | `{n}%`, 1 dp | `cf` |
| 11 | P50 GWh/y | right | integer | `gwh` |
| 12 | LCOE €/MWh | right | integer | `lcoe` |
| 13 | Contracted | right | `{n}%`, 0 dp | `offtake` |
| 14 | Equity IRR | right | `{n}%`, 1 dp, **heading font**, `—` when undefined | `irr` |
| 15 | Min DSCR | right | `{n}×`, 2 dp, **alert plus a mark below the mandate floor** | `dscr` |
| 16 | Risk | right | 1 dp | `risk` |

Sorting: clicking a header sorts descending, clicking again reverses; the active header carries `↑`
or `↓`. Default sort is `irr` descending.

Row states — each needs a non-colour affordance as well as its tone:

| State | Tone | Affordance |
|---|---|---|
| Selected | none | — |
| Locked | `--color-accent-100` | `■` in the lock column |
| Not selected (only visible with *Show all candidates*) | `--color-neutral-200` | `Not selected` in the row's accessible name |

Footer: *Click a row for the full project sheet. Lock a project to force it into the next run;
unlock-and-exclude from the drawer to rule one out.*

### 5.5 Project detail sheet

A drawer, `min(440px, 94vw)`, opened from any row and dismissible by `Esc`, the backdrop, or
`Close`. Focus moves into it on open and returns to the originating row on close.

Header: eyebrow `{technology} · {stage}`, H2 name, sub-line
`{country} · {lat}°, {lon}° · {id}` at two decimals.

Four headline figures: **Capacity** `{mw} MW` · **Equity IRR ({hold}y)** `{irr}%` · **Equity**
`€{n}m` · **MOIC** `{n}×`.

| Group | Rows |
|---|---|
| Technical | Net capacity factor `{n}%` 1 dp · P50 generation `{n} GWh/yr` · Commercial operation `{year}` · Grid connection `Secured` / `Application pending` · O&M `Long-term service agreement signed` / `Not contracted` |
| Capital structure | Total project cost `€{n}m (€{n}/kW)` · Senior debt `€{n}m at {pct}, {rate}, {tenor}y` · Minimum DSCR `{n}×` · Equity payback `{year}` or `beyond {lastYear}` · Currency `{ccy}` with ` — hedge required` when not EUR |
| Revenue | Contracted share `{pct}` · PPA price `€{n}/MWh for {t} yrs` or `merchant only` · Capture price `€{n}/MWh (baseload €{n})` · LCOE `€{n}/MWh` · Opex `€{n}/kW/yr` |
| Risk | Development risk score `{n} / 5` · Status in portfolio `Selected` / `Not selected` / `Excluded` · Locked `Yes — forced into next run` / `No` |

Two actions: `Lock into portfolio` / `Unlock`, and `Exclude from search` / `Re-admit candidate`.
Excluding a project also clears its lock.

**Provenance.** Where a figure's `provenance.estimateBasis` is softer than `contracted`, the row
carries it as text — `engineering estimate`, `benchmark`, `placeholder` — so a committee can see
which numbers are contracts. See [`pipeline-schema.md` §8](pipeline-schema.md#8-provenance).

A project whose COD falls after the hold period contributes only construction outflows and an exit
value, and the drawer says so (§13).

### 5.6 Export dialog

Title `Export portfolio`, body the run sub-line, then three actions:

| Action | Detail |
|---|---|
| `Holdings table (CSV)` | `{n} rows` — all sixteen columns, selection only |
| `30-year cash-flow schedule (CSV)` | `30 rows` — `cashflow30Y_m`, no terminal value |
| `Investment committee sheet (PDF)` | `print` |

All three carry the run reference and the mandate (§7.7). CSVs are generated **server-side** so they
match the stored result exactly (§11).

---

## 6. Charts, in one place

Four, all hand-built from divs and inline SVG. No chart library.

| Chart | Where | Geometry |
|---|---|---|
| Convergence polylines | Screen 02 | `600 × 150` viewBox, two polylines, non-scaling stroke |
| 30-bar FCFE | Screen 03 | 30 flex columns, 2px gap, zero line at `max ÷ (max − min)`, bars sized in per cent |
| Site map | Screen 03 | `d3.geoMercator`, centre `[12, 55]`, scale `width × 1.15` |
| Technology split bar | Screen 01 | Single 30px track, two fills, one handle |

---

## 7. Accessibility

§12: full keyboard operation, visible focus, 4.5:1 contrast for body text, **no colour-only status
encoding**.

### 7.1 Colour is never the only signal

§7.1 asks for tiles "coloured to indicate compliance" and §12 forbids colour-only status. Both hold
if colour is an *additional* channel (A-10). Every status carries a mark or a word:

| Place | Colour | Required second signal |
|---|---|---|
| Tile sub-label | compliant / alert tone | A leading `✓` when compliant, `!` when breaching. Neutral tiles carry neither. |
| Holdings `Min DSCR` below the floor | alert tone | A trailing `!` and `below the {n}× floor` in the cell's accessible name |
| Holdings non-selected row | neutral-200 ground | `Not selected` in the row's accessible name |
| Holdings locked row | accent-100 ground | `■` in the lock column, `Locked` in its accessible name |
| Map solar vs wind marker | accent-700 / accent-400 | The legend, plus technology in each marker's accessible name |
| FCFE negative bar | accent-300 | Position below the zero line, and the sign in the hover and accessible readout |
| Feasibility warning | alert / neutral tone | The `×`, `!` or `•` mark already in each string |
| Step indicator | accent on the current step | `aria-current="step"` |

### 7.2 Keyboard and focus

- Every control is reachable and operable by keyboard, including the split bar (it is a real range
  input) and each FCFE bar.
- `:focus-visible` is a 2px `--color-accent` outline at 2px offset. It is never removed.
- The drawer and the export dialog trap focus, close on `Esc`, and restore focus to what opened
  them.
- Table headers are `<th>` with `aria-sort`; the sort arrow is decorative and mirrored in
  `aria-sort`.
- Live regions: the search screen's round counter and the mandate footer's figures are polite live
  regions, so a screen-reader user hears progress and feasibility without polling.

### 7.3 Contrast

Body text is `--color-text` on `--color-bg`, which clears 4.5:1. Muted text at 55% does **not**, so
it is used only for labels that repeat information available elsewhere — never for a value, a
warning or a status. `--color-accent-700` and `--color-accent-800` on `--color-bg` clear 4.5:1 and
are what compliance tones use; `--color-accent` itself is reserved for fills, not text.

---

## 8. Copy rules

§14: the interface speaks the language of an investment professional, not of an optimisation
engineer.

- Screens refer to **the optimiser** and to **searching the pipeline**.
- Banned from user-facing copy: *population, chromosome, gene, crossover, mutation, fitness,
  generation, elite, tournament, convergence, solution space, objective function*. They belong in
  this repository's documentation and in the run record. [§4](#4-screen-02--search) lists the
  replacements for every instance in the mockup.
- Permitted and preferred: *candidate portfolio, round, mandate score, best portfolio, screens,
  constraint, breach, shortfall*.
- Numbers go through [§2](#2-number-formats). An em dash means undefined, never zero.
