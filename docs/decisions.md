# Decisions

Spec deviations, resolved ambiguities and open questions. Every issue appends the
decisions it takes; epic #1 §10 requires it.

> **Note for issue 1B (#3):** this file is yours. It was created by issue 1D (#5) only
> because #5 needed somewhere to record the decisions below and #3 had not landed yet.
> Everything here is a single section appended at the end — fold it into your structure
> rather than working around it.

---

## 1D — Web foundation (issue #5)

### D1. `web/js/*.js` are classic scripts, not ES modules

**Ambiguity.** Issue #5's scope says "the JS is plain ES modules loaded directly". Its
acceptance criteria say "All four pages open directly from disk with no server and no
network". These cannot both hold: browsers block `<script type="module">` and every
module `import` over `file://`, because the origin is opaque and module fetches require
CORS.

**Verified, not assumed.** A `file://` page loading a module script, in Chrome:

```
$ chrome --headless --dump-dom file:///…/spike.html
<h1>MODULES BLOCKED</h1>
<pre>error event: (no message — opaque CORS failure)
script onerror fired
module script never executed after 1500ms</pre>
```

**Decision.** The acceptance criterion wins; "loaded directly" is honoured in the sense
that matters, which is that nothing is bundled. `format.js`, `feasibility.js` and
`controls.js` are classic scripts attaching to one `window.TerraFolio` namespace, each
ending with a CommonJS tail so bare node can require them. `feasibility.js` takes its
one dependency as `typeof require === 'function' ? require('./format.js') :
window.TerraFolio.format`, so it still imports nothing but `format.js` and still runs
under bare node — which is what issue #12 needs to check it against #6's Python.

**Consequence for #11.** `api.js`, `mandate.js`, `search.js`, `portfolio.js`,
`table.js`, `charts.js` and `map.js` follow the same convention, or the pages stop
opening from disk.

### D2. Tailwind 3.4, not 4

Issue #5 names `tailwind.config.js` and asks to switch Tailwind's default rounding off,
both v3 config idioms. v4 moves theme tokens into CSS `@theme` and ships no config file.

### D3. The world atlas ships twice, from one source

`fetch()` is also blocked over `file://`, so `web/public/countries-110m.json` cannot be
read from disk by the map. `tools/vendor.mjs` writes the `.json` that #5 specifies and
generates `countries-110m.js` beside it — the same data as a classic script — so #11 has
a working offline path and there is still only one source of truth.

The atlas is Natural Earth 110m: `objects.countries` and `objects.land`, 177 geometries,
numeric ISO-3166 ids, `properties.name`. The reference joins on `properties.name`, so
#11 needs a name join or a numeric↔alpha-2 map.

### D4. Number grouping is pinned to `en-GB`

`format.js` passes an explicit locale rather than using the viewer's. A German locale
would render `€1.200m` and `12,4%`, so two printings of the same run would disagree.
Epic §12 wants a run reproducible; that has to include how it reads.

### D5. Numerals are Barlow Condensed 600

The mockup renders metric numerals at Condensed **400** — its inline styles set only
`font-family`, so the weight is inherited from `body`. Issue #5 says "Headings and all
numerals in Barlow Condensed 600". Followed as written; recorded because it is a visible
difference from the mockup rather than an oversight. Numerals also carry
`font-variant-numeric: tabular-nums lining-nums`, so a column of figures aligns.

### D6. Nine screens: the COD window is one, "not excluded" is the ninth

The reference `eligible()` has ten conditions; §5.2–5.4 calls it nine screens. Read as:
country, stage, **COD window** (one screen, two bounds, inclusive at both ends), min
DSCR, risk cap, grid secured, O&M partner, currency, not excluded. Each is exported
individually from `feasibility.js` and tested on its own.

### D7. The nav is duplicated markup, generated and guarded

"Sharing the sticky-nav partial" has no templating available: there is no bundler and
the pages must work from disk. `tools/splice-nav.mjs` writes the block into all four
pages from `tools/nav.part.html`, and `tests/nav.test.js` asserts the four copies are
identical apart from which step carries `aria-current`. That one attribute drives both
the announcement and, through an `aria-[current=step]` variant, the colour, so the two
cannot disagree.

### D8. The mockup's palette fails WCAG AA; the roles move, the ladder does not

Measured over the real token values:

| Role as the mockup uses it | Ratio | Re-mapped to | Ratio |
|---|---|---|---|
| bare `accent #5980a6` as text — links, ghost buttons, outline chips, kicker, focus ring | **3.71** ✗ | `accent-700` | 5.78 ✓ |
| `bg` on bare `accent` — primary button label, `SOLAR` inset label | **3.71** ✗ | `bg` on `accent-700` | 5.78 ✓ |
| micro-labels at `text@55%` — every tile, figure and stat label | **3.64** ✗ | `neutral-700` | 5.87 ✓ |
| table `th` at `text@60%` | **4.25** ✗ | `neutral-700` | 5.87 ✓ |
| input / switch border `neutral-400` (UI boundaries need 3:1) | **1.79** ✗ | `neutral-600` | 3.82 ✓ |
| `accent-400` wind fill on `bg` (graphics need 3:1) | **1.78** ✗ | fill kept, `accent-700` hairline added | 5.78 ✓ |

No new colours were invented: issue #5's own semantic mapping is the fix, since it names
`accent-700` for active data ink and `neutral-700` for muted body, and never lists the
bare `--color-accent` among the tokens to port. **The ported system therefore has no
bare `--color-accent` and no `--color-accent-2` ladder.** `tests/contrast.test.js`
asserts all 29 pairs and fails if either value reappears.

### D9. The mandate action bar carries four figures, not the reference's three

The reference shows candidates / capacity / equity, and surfaces the eligible pool's own
solar mix and supportable leverage only once they trip a warning. Issue #5 asks for four
figures. Followed: a target the user cannot reach should be visible while they are
setting it, not only after it fails.

### D10. Compliance state is never colour alone

Every KPI tile in the reference encodes its state purely in the sub-label's colour. Epic
§5 and §12 forbid that. `kpiTile` carries a state of `on-target | neutral | breach` and
renders a mark (`✓` / none / `!`) and a visually-hidden state name alongside the colour.
Chips carry `aria-pressed` and a filled or hollow square; switches carry `role="switch"`,
`aria-checked` and a knob that moves. `tests/state-not-colour.test.js` enforces the
second signal.

### D11. The brand lockup is TerraFolio

The mockup's nav reads `MERIDIAN CAPITAL`, a stand-in firm name. The product is
TerraFolio. Copy is #10's to audit.

### D12. The country chip's flag is decorative

Chips render a flag, a state square and the country name. The flag is
`aria-hidden="true"` and the country name is the accessible label: screen readers
announce regional-indicator pairs inconsistently, and a machine with no emoji font shows
two letters. `UK` maps to the `GB` flag, since `UK` is not an ISO-3166 region code.

### D13. Mandate rates are fractions, not whole percent

The reference stores `hurdle: 11`, `minLev: 60`, `maxMerchant: 35` as whole percent but
`solarShare: 0.45` as a fraction, then divides by 100 at each comparison. The mandate
object this front end emits uses **fractions throughout** for every rate and share; the
native range inputs still work in whole percent and the control converts on the way out.
Mixing the two conventions in one object is how a factor-of-100 bug gets written.

### D14. `feasibility.js` reads the risk-appetite caps from the payload

The reference hard-codes `{Low: 2.6, Balanced: 3.6, High: 5}`. Epic §5 requires every
band to live in the assumption set. **The keys are canonical lowercase — `low`, `balanced`,
`high` — not the capitalised button labels** (see D17). `/pipeline` must therefore expose the resolved caps,
and `assumptions/default-2026.toml` (#2) needs a `risk_caps` table. An appetite the
assumption set does not define raises rather than passing, because a missing cap failing
open would silently admit the whole pipeline.

### D15. The `/pipeline` candidate shape is a proposal pending `docs/api.md`

`feasibility.js` needs `id`, `country`, `stage`, `technology`, `mw`, `cod`, `min_dscr`,
`dev_risk`, `grid_secured`, `om_partner`, `currency`, `capex_m`, `senior_debt_m`,
`equity_m`, plus `assumptions.risk_caps`. Money in €m with the explicit `_m` suffix and
generation in GWh, per epic §5. snake_case assumed, to match the `_m` convention.

Posted on #1 for #3 and #9 to confirm. Read in exactly one adapter function at the top of
`feasibility.js`, so reconciling with the real contract is a single edit.

### D16. Open: the search screen needs a way to stop a run

`search.html` carries a "Stop and edit the mandate" link, because Exhaustive at 2,000
candidates measures 30 s (epic §7) and a screen with no exit is not acceptable. As a
static page it is a plain link back to the mandate; **#11 must intercept it to abort the
run and close the event stream before navigating**, or a stopped run leaks a worker.

### D17. A control emits a wire value; its label is a separate thing

Found in review, after both defects had been written.

The stage chips emitted their display labels — `Ready-to-build` — while `feasibility.js`
and every other consumer compares against the wire value `ready_to_build`. Every candidate
therefore failed the stage screen and the pool was always empty. The risk-appetite
segmented control had the identical defect: it emitted `Balanced`, which would then be
looked up in `assumptions.risk_caps`.

Neither shows up as an error. The first returns an empty portfolio, which reads as a
mandate that is simply too tight; the second raises only because D14 made a missing cap
raise rather than fail open.

**Decision.** Every control that offers a choice takes `values` (canonical, what it emits)
and `labels` (what the user reads) as separate inputs, the way the country chips already
did. `chipGroup` and `segmented` both have a `label(value)` accessor and neither renders a
raw value. `tests/mandate-contract.test.js` asserts the page emits values the screens
accept, and that a display label does **not** pass the stage screen.

**Consequence for #2:** the `risk_caps` table is keyed `low`, `balanced`, `high`.

### D18. Percentage spinners emit fractions like everything else

Also found in review. D13 says the mandate uses fractions throughout, and the two
percentage *sliders* did — but the three percentage *spinners* (`maxMerchant`,
`maxCountry`, `maxProject`) emitted `35` and `15` unchanged, because `numberField` had no
`scale` where `rangeField` did. A 35% merchant cap would have reached the objective as
3500%, which does not fail: it silently disables the constraint.

`numberField` now takes the same `scale` option, and a test asserts every percentage
control on the mandate declares `scale: 100` while the fields that are not percentages —
hold years, min DSCR, COD years — declare none.

### D19. Feasibility updates on `input`, not `change`

The controls dispatched `tf:change` only on the DOM `change` event, which does not fire
while a slider is being dragged or while a number is being typed without blurring. Epic §7
budgets mandate feedback at under 100 ms precisely so it can be live, and the footer would
have sat stale through the whole interaction. Ranges and spinners now dispatch on `input`;
radios stay on `change`, which is when a radio actually changes.

### D20. The split bar's focus ring is drawn by the bar, not the slider

The transparent `<input type="range">` laid over the split bar is `opacity: 0`, which makes
its `:focus-visible` outline transparent too. The control was keyboard-operable with no
visible focus at all. The visible bar now takes the ring through a
`has-[:focus-visible]` treatment on the wrapper.

### D21. `tools/splice-nav.mjs` is idempotent, and `npm test` builds first

Two tooling defects found in the same review.

The splice tool consumed its own `<!--NAV:step-->` marker on the first run, so every later
run silently did nothing and an edit to `nav.part.html` could never reach the pages. It now
keeps the marker and replaces any block already following it; `tests/nav.test.js` asserts a
second run changes nothing and that every page still carries its marker.

`dist/app.css` is gitignored build output, but all four pages reference it and
`tests/offline.test.js` asserts every referenced file exists. A fresh `npm ci && npm test`
therefore failed, and a fresh checkout opened every page unstyled. A `pretest` script now
builds the stylesheet before the tests run.

