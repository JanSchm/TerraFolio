/**
 * One test per screen predicate and one per warning, as issue #5 requires, plus the
 * footer figures and the module's loading contract.
 *
 * The fixtures are deliberately tiny and named: a screen test that needs a realistic
 * project to make its point is testing something other than the screen.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const F = require('../js/feasibility.js');
const fmt = require('../js/format.js');

/** A candidate that passes every screen, in /pipeline wire shape. */
function candidate(overrides = {}) {
  return {
    id: 'P01',
    country: 'ES',
    stage: 'ready_to_build',
    technology: 'solar',
    mw: 100,
    cod: 2029,
    min_dscr: 1.4,
    dev_risk: 2.2,
    grid_secured: true,
    om_partner: true,
    currency: 'EUR',
    capex_m: 100,
    senior_debt_m: 70,
    equity_m: 30,
    ...overrides,
  };
}

/** A mandate that admits the candidate above. Rates are fractions, not whole percent. */
function mandate(overrides = {}) {
  return {
    countries: ['ES', 'PT', 'DE'],
    stages: ['greenfield', 'ready_to_build', 'construction'],
    codFrom: 2027,
    codTo: 2032,
    minDscr: 1.25,
    risk: 'Balanced',
    gridOnly: false,
    omOnly: false,
    hedged: false,
    excluded: [],
    locked: [],
    capital: 1200,
    target: 1500,
    solarShare: 0.45,
    minLev: 0.6,
    ...overrides,
  };
}

/** Risk caps come off the payload, never out of this file — epic §5. */
const ASSUMPTIONS = { risk_caps: { Low: 2.6, Balanced: 3.6, High: 5 } };

function payload(candidates, assumptions = ASSUMPTIONS) {
  return { candidates, assumptions };
}

const capFor = (m) => ASSUMPTIONS.risk_caps[m.risk];
const adapt = (raw) => F.candidates(payload([raw]))[0];

/* ── The nine screens ───────────────────────────────────────────────────────── */

test('screen 1 — country: only projects in an eligible country pass', () => {
  const m = mandate({ countries: ['ES', 'PT'] });
  assert.equal(F.screens.country(adapt(candidate({ country: 'ES' })), m), true);
  assert.equal(F.screens.country(adapt(candidate({ country: 'DE' })), m), false);
  assert.equal(F.screens.country(adapt(candidate()), mandate({ countries: [] })), false,
    'an empty country list admits nothing, rather than everything');
});

test('screen 2 — stage: only stages in scope pass', () => {
  const m = mandate({ stages: ['ready_to_build', 'construction'] });
  assert.equal(F.screens.stage(adapt(candidate({ stage: 'ready_to_build' })), m), true);
  assert.equal(F.screens.stage(adapt(candidate({ stage: 'construction' })), m), true);
  assert.equal(F.screens.stage(adapt(candidate({ stage: 'greenfield' })), m), false);
});

test('screen 3 — COD window: inclusive at both ends', () => {
  const m = mandate({ codFrom: 2028, codTo: 2030 });
  assert.equal(F.screens.codWindow(adapt(candidate({ cod: 2027 })), m), false, 'before the window');
  assert.equal(F.screens.codWindow(adapt(candidate({ cod: 2028 })), m), true, 'the opening year is in');
  assert.equal(F.screens.codWindow(adapt(candidate({ cod: 2029 })), m), true);
  assert.equal(F.screens.codWindow(adapt(candidate({ cod: 2030 })), m), true, 'the closing year is in');
  assert.equal(F.screens.codWindow(adapt(candidate({ cod: 2031 })), m), false, 'after the window');
});

test('screen 4 — min DSCR: the floor is inclusive', () => {
  const m = mandate({ minDscr: 1.25 });
  assert.equal(F.screens.minDscr(adapt(candidate({ min_dscr: 1.24 })), m), false);
  assert.equal(F.screens.minDscr(adapt(candidate({ min_dscr: 1.25 })), m), true,
    'a project exactly on the floor is not a breach');
  assert.equal(F.screens.minDscr(adapt(candidate({ min_dscr: 1.4 })), m), true);
});

test('screen 5 — risk cap: the ceiling comes from the payload, not this file', () => {
  const m = mandate({ risk: 'Balanced' });
  const cap = capFor(m);
  assert.equal(F.screens.riskCap(adapt(candidate({ dev_risk: 3.6 })), m, cap), true, 'inclusive');
  assert.equal(F.screens.riskCap(adapt(candidate({ dev_risk: 3.7 })), m, cap), false);
  // Low admits less, High admits everything — and both come off the assumption set.
  assert.equal(F.screens.riskCap(adapt(candidate({ dev_risk: 3.0 })), m, ASSUMPTIONS.risk_caps.Low), false);
  assert.equal(F.screens.riskCap(adapt(candidate({ dev_risk: 5 })), m, ASSUMPTIONS.risk_caps.High), true);
});

test('screen 5 — an appetite the assumption set does not define is an error, not a pass', () => {
  assert.throws(() => F.riskCap(payload([]), 'Reckless'), /no risk cap configured/);
  assert.throws(() => F.riskCap({ candidates: [] }, 'Balanced'), /no risk cap configured/,
    'a payload with no assumptions block must not silently admit everything');
});

test('screen 6 — grid secured: only screens when the mandate asks', () => {
  const unsecured = adapt(candidate({ grid_secured: false }));
  assert.equal(F.screens.gridSecured(unsecured, mandate({ gridOnly: false })), true, 'off by default');
  assert.equal(F.screens.gridSecured(unsecured, mandate({ gridOnly: true })), false);
  assert.equal(F.screens.gridSecured(adapt(candidate({ grid_secured: true })),
    mandate({ gridOnly: true })), true);
});

test('screen 7 — O&M partner: only screens when the mandate asks', () => {
  const uncontracted = adapt(candidate({ om_partner: false }));
  assert.equal(F.screens.omPartner(uncontracted, mandate({ omOnly: false })), true);
  assert.equal(F.screens.omPartner(uncontracted, mandate({ omOnly: true })), false);
  assert.equal(F.screens.omPartner(adapt(candidate({ om_partner: true })),
    mandate({ omOnly: true })), true);
});

test('screen 8 — currency: a non-EUR project is screened out, never converted', () => {
  const zloty = adapt(candidate({ country: 'PL', currency: 'PLN' }));
  assert.equal(F.screens.currency(zloty, mandate({ hedged: false })), true, 'off by default');
  assert.equal(F.screens.currency(zloty, mandate({ hedged: true })), false);
  assert.equal(F.screens.currency(adapt(candidate()), mandate({ hedged: true })), true);
});

test('screen 9 — not excluded: a hand-excluded project never returns', () => {
  assert.equal(F.screens.notExcluded(adapt(candidate({ id: 'P07' })),
    mandate({ excluded: [] })), true);
  assert.equal(F.screens.notExcluded(adapt(candidate({ id: 'P07' })),
    mandate({ excluded: ['P07'] })), false);
  assert.equal(F.screens.notExcluded(adapt(candidate({ id: 'P07' })),
    mandate({ excluded: ['P08'] })), true);
});

test('there are exactly nine screens, and passes() applies all of them', () => {
  assert.equal(F.SCREEN_ORDER.length, 9);
  assert.deepEqual(F.SCREEN_ORDER, [
    'country', 'stage', 'codWindow', 'minDscr', 'riskCap',
    'gridSecured', 'omPartner', 'currency', 'notExcluded',
  ]);
  for (const name of F.SCREEN_ORDER) {
    assert.equal(typeof F.screens[name], 'function', `${name} must be individually testable`);
  }
  const m = mandate();
  assert.equal(F.passes(adapt(candidate()), m, capFor(m)), true);
  assert.equal(F.passes(adapt(candidate({ country: 'XX' })), m, capFor(m)), false,
    'failing one screen fails the candidate');
});

test('failedScreens names every screen a candidate misses', () => {
  const m = mandate({ gridOnly: true, hedged: true });
  const bad = adapt(candidate({ country: 'XX', grid_secured: false, currency: 'GBP' }));
  assert.deepEqual(F.failedScreens(bad, m, capFor(m)), ['country', 'gridSecured', 'currency']);
  assert.deepEqual(F.failedScreens(adapt(candidate()), m, capFor(m)), [],
    'a passing candidate fails nothing');
});

/* ── Canonical ordering ─────────────────────────────────────────────────────── */

test('candidates come back ordered by id ascending, whatever order they arrived in', () => {
  const out = F.candidates(payload([
    candidate({ id: 'P10' }), candidate({ id: 'P02' }), candidate({ id: 'P01' }),
  ]));
  assert.deepEqual(out.map((c) => c.id), ['P01', 'P02', 'P10'],
    'the GA indexes PRNG draws by position, so order is a determinism requirement');
});

/* ── The four footer figures ────────────────────────────────────────────────── */

test('footer figures: candidates passing screens, out of the whole pipeline', () => {
  const r = F.feasibility(payload([
    candidate({ id: 'P01' }),
    candidate({ id: 'P02', country: 'XX' }),
    candidate({ id: 'P03' }),
  ]), mandate());
  assert.equal(r.figures.candidates.value, 2);
  assert.equal(r.figures.candidates.of, 3);
  assert.equal(r.figures.candidates.display, '2');
  assert.equal(r.figures.candidates.ofDisplay, '3');
});

test('footer figures: eligible capacity sums only the pool', () => {
  const r = F.feasibility(payload([
    candidate({ id: 'P01', mw: 120 }),
    candidate({ id: 'P02', mw: 900, country: 'XX' }),
    candidate({ id: 'P03', mw: 80 }),
  ]), mandate());
  assert.equal(r.figures.capacity.value, 200);
  assert.equal(r.figures.capacity.display, '200 MW');
});

test('footer figures: equity required at full draw', () => {
  const r = F.feasibility(payload([
    candidate({ id: 'P01', equity_m: 30 }),
    candidate({ id: 'P02', equity_m: 45 }),
  ]), mandate());
  assert.equal(r.figures.equity.value, 75);
  assert.equal(r.figures.equity.display, '€75m');
});

test('footer figures: the pool\'s own solar mix and supportable leverage', () => {
  const r = F.feasibility(payload([
    candidate({ id: 'P01', technology: 'solar', mw: 300, capex_m: 100, senior_debt_m: 70 }),
    candidate({ id: 'P02', technology: 'onshore_wind', mw: 100, capex_m: 100, senior_debt_m: 50 }),
  ]), mandate());
  assert.equal(r.figures.mixAndLeverage.solarMix, 0.75, 'capacity-weighted, as the portfolio reports it');
  assert.equal(r.figures.mixAndLeverage.leverage, 0.6, 'cost-weighted');
  assert.equal(r.figures.mixAndLeverage.solarDisplay, '75%');
  assert.equal(r.figures.mixAndLeverage.leverageDisplay, '60%');
});

test('an empty pool has no mix and no leverage, and says so with an em dash', () => {
  const r = F.feasibility(payload([candidate({ country: 'XX' })]), mandate());
  assert.ok(Number.isNaN(r.aggregate.solarMix), 'there is no mix without a pool');
  assert.ok(Number.isNaN(r.aggregate.leverage));
  assert.equal(r.figures.mixAndLeverage.solarDisplay, fmt.DASH, 'never 0%');
  assert.equal(r.figures.mixAndLeverage.leverageDisplay, fmt.DASH);
  assert.equal(r.figures.capacity.display, '0 MW', 'but a capacity of zero is a real zero');
});

/* ── The six warnings ───────────────────────────────────────────────────────── */

const ids = (r) => r.warnings.map((w) => w.id);
const byId = (r, id) => r.warnings.find((w) => w.id === id);

test('warning 1 — no candidates pass the screens', () => {
  const r = F.feasibility(payload([candidate({ country: 'XX' })]), mandate());
  const w = byId(r, 'no-candidates');
  assert.ok(w, 'an empty pool must be reported');
  assert.equal(w.severity, 'breach');
  assert.equal(w.mark, '×');
  assert.equal(w.text,
    'No candidates pass the current screens. Widen countries, stages or the COD window.');
  assert.deepEqual(ids(r), ['no-candidates'],
    'with no pool, the pool-dependent warnings must stay silent');
});

test('warning 2 — eligible pipeline is below the capacity target', () => {
  const r = F.feasibility(payload([candidate({ mw: 400 })]), mandate({ target: 1500 }));
  const w = byId(r, 'below-target');
  assert.ok(w);
  assert.equal(w.severity, 'breach');
  assert.equal(w.mark, '!');
  assert.equal(w.text, 'Eligible pipeline is 400 MW — below the 1,500 MW target.');
  assert.ok(w.text.includes('—'), 'an em dash, not a hyphen');

  const met = F.feasibility(payload([candidate({ mw: 1500 })]), mandate({ target: 1500 }));
  assert.equal(byId(met, 'below-target'), undefined, 'exactly on target is not below it');
});

test('warning 3 — the full pipeline absorbs less than 90% of the capital', () => {
  const r = F.feasibility(payload([candidate({ mw: 2000, equity_m: 500 })]),
    mandate({ capital: 1200, target: 1500 }));
  const w = byId(r, 'under-absorbed');
  assert.ok(w);
  assert.equal(w.severity, 'note');
  assert.equal(w.text, 'Full pipeline absorbs only €500m of the €1,200m available.');

  // It is suppressed while capacity is short: that is the more urgent message.
  const short = F.feasibility(payload([candidate({ mw: 400, equity_m: 500 })]),
    mandate({ capital: 1200, target: 1500 }));
  assert.equal(byId(short, 'under-absorbed'), undefined);
  assert.ok(byId(short, 'below-target'), 'the capacity shortfall is reported instead');

  const full = F.feasibility(payload([candidate({ mw: 2000, equity_m: 1100 })]),
    mandate({ capital: 1200, target: 1500 }));
  assert.equal(byId(full, 'under-absorbed'), undefined, '1,100 of 1,200 is above the 90% floor');
});

test('warning 4 — the solar target may be out of reach for this pool', () => {
  const r = F.feasibility(payload([
    candidate({ id: 'P01', technology: 'onshore_wind', mw: 900 }),
    candidate({ id: 'P02', technology: 'solar', mw: 100 }),
  ]), mandate({ solarShare: 0.45, target: 500 }));
  const w = byId(r, 'solar-unreachable');
  assert.ok(w);
  assert.equal(w.severity, 'note');
  assert.equal(w.text, 'Solar target of 45% may be unreachable: eligible pool is 10% solar.');

  // Within 20 points is close enough to stay quiet.
  const close = F.feasibility(payload([
    candidate({ id: 'P01', technology: 'onshore_wind', mw: 700 }),
    candidate({ id: 'P02', technology: 'solar', mw: 300 }),
  ]), mandate({ solarShare: 0.45, target: 500 }));
  assert.equal(byId(close, 'solar-unreachable'), undefined);
});

test('warning 5 — minimum leverage exceeds what the pool supports', () => {
  const r = F.feasibility(payload([candidate({ mw: 2000, capex_m: 100, senior_debt_m: 45 })]),
    mandate({ minLev: 0.6, target: 500, capital: 100 }));
  const w = byId(r, 'leverage-unsupported');
  assert.ok(w);
  assert.equal(w.severity, 'breach');
  assert.equal(w.text,
    'Minimum leverage of 60% exceeds what the eligible pool supports (45%).');

  const ok = F.feasibility(payload([candidate({ mw: 2000, capex_m: 100, senior_debt_m: 60 })]),
    mandate({ minLev: 0.6, target: 500, capital: 100 }));
  assert.equal(byId(ok, 'leverage-unsupported'), undefined, 'exactly on the floor is not a breach');
});

test('warning 6 — locked and excluded projects are reported as a running note', () => {
  const r = F.feasibility(payload([candidate({ id: 'P01' }), candidate({ id: 'P02' })]),
    mandate({ locked: ['P01'], excluded: ['P09'], target: 1, capital: 1 }));
  const w = byId(r, 'locked-excluded');
  assert.ok(w);
  assert.equal(w.severity, 'note');
  assert.equal(w.mark, '•');
  assert.equal(w.text, '1 project(s) locked in; 1 excluded.');

  const none = F.feasibility(payload([candidate()]), mandate({ target: 1, capital: 1 }));
  assert.equal(byId(none, 'locked-excluded'), undefined, 'silent when nothing is locked');
});

test('warnings arrive in severity order and never rely on colour alone', () => {
  const r = F.feasibility(payload([
    candidate({ id: 'P01', technology: 'onshore_wind', mw: 40, capex_m: 100, senior_debt_m: 45 }),
  ]), mandate({ target: 1500, capital: 1200, solarShare: 0.45, minLev: 0.6, locked: ['P01'] }));

  assert.deepEqual(ids(r),
    ['below-target', 'solar-unreachable', 'leverage-unsupported', 'locked-excluded']);

  for (const w of r.warnings) {
    assert.ok(['breach', 'note'].includes(w.severity), `${w.id} must carry a severity`);
    assert.ok(w.mark.length > 0, `${w.id} must carry a mark, so colour is never the only signal`);
    assert.ok(w.text.length > 0);
  }
});

/* ── The loading contract ───────────────────────────────────────────────────── */

test('feasibility.js requires nothing but format.js', () => {
  const source = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'js', 'feasibility.js'), 'utf8');
  const requires = [...source.matchAll(/require\((['"])(.*?)\1\)/g)].map((m) => m[2]);
  assert.deepEqual(requires, ['./format.js'],
    'issue #12 imports this in node to check it against the Python; keep it free of dependencies');
  // Strip comments and string literals first: this guard is about code, not prose.
  // Without it, the warning text "...or the COD window." reads as a DOM access.
  const code = source
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/\/\/.*$/gm, ' ')
    .replace(/'(?:\\.|[^'\\])*'/g, "''")
    .replace(/"(?:\\.|[^"\\])*"/g, '""');
  for (const forbidden of [/\bdocument\s*\./, /\bwindow\s*\./, /\bAlpine\b/, /querySelector/]) {
    assert.ok(!forbidden.test(code),
      `it must stay a pure function of (payload, mandate); found ${forbidden}`);
  }
});

test('feasibility.js loads under bare node with no flags', () => {
  const { execFileSync } = require('node:child_process');
  const out = execFileSync(process.execPath, [
    '-e',
    "const f = require('./js/feasibility.js');" +
    "process.stdout.write(String(f.SCREEN_ORDER.length));",
  ], { cwd: require('node:path').join(__dirname, '..'), encoding: 'utf8' });
  assert.equal(out, '9');
});
