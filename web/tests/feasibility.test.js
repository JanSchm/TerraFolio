/**
 * One test per screen predicate and one per warning, as issue #5 requires, plus the
 * preview figures and the module's loading contract.
 *
 * Written against api.md §5 (POST /mandate/preview) and ui-contract.md §3.4–3.6, which
 * are normative: api.md says the client and the server must agree field for field, and
 * that if they diverge the client is wrong. The warning order is §5.4's order of
 * severity, which is NOT the order the design mockup emits them in (decisions A-5).
 *
 * The fixtures are deliberately tiny and named: a screen test that needs a realistic
 * project to make its point is testing something other than the screen.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const F = require('../js/feasibility.js');
const fmt = require('../js/format.js');

/** A project that passes every screen, in GET /pipeline wire shape (api.md §2). */
function candidate(overrides = {}) {
  return {
    id: 'P01',
    countryCode: 'ES',
    stage: 'ready_to_build',
    technology: 'solar',
    capacityMw: 100,
    codYear: 2029,
    minDscr: 1.4,
    developmentRiskScore: 2.2,
    gridSecured: true,
    omContracted: true,
    currency: 'EUR',
    totalCapex_m: 100,
    seniorDebt_m: 70,
    equity_m: 30,
    ...overrides,
  };
}

/** A mandate that admits the project above — api.md §6.1 names, fractions throughout. */
function mandate(overrides = {}) {
  return {
    countries: ['ES', 'PT', 'DE'],
    stages: ['greenfield', 'ready_to_build', 'construction'],
    codFrom: 2027,
    codTo: 2032,
    minDscr: 1.25,
    riskAppetite: 'balanced',
    gridSecuredOnly: false,
    omContractedOnly: false,
    eurRevenueOnly: false,
    availableCapital_m: 1200,
    capacityTargetMw: 1500,
    solarShare: 0.45,
    minLeverage: 0.6,
    maxMerchantShare: 0.35,
    maxCountryShare: 0.35,
    maxProjectShare: 0.15,
    ...overrides,
  };
}

/** Locks and exclusions ride beside the mandate, not inside it (api.md §5). */
const locks = (overrides = {}) => ({ lockedIds: [], excludedIds: [], ...overrides });

/** Caps come off the payload's resolved assumption set, never out of the code. */
const ASSUMPTIONS = { riskCaps: { low: 2.6, balanced: 3.6, high: 5 } };
const payload = (projects, assumptions = ASSUMPTIONS) => ({ projects, assumptions });
const capFor = (m) => ASSUMPTIONS.riskCaps[m.riskAppetite];
const adapt = (raw) => F.projects(payload([raw]))[0];
const preview = (projects, m = mandate(), l = locks()) => F.feasibility(payload(projects), m, l);

/* ── The nine screens ───────────────────────────────────────────────────────── */

test('screen 1 — country: only projects in an eligible country pass', () => {
  const m = mandate({ countries: ['ES', 'PT'] });
  assert.equal(F.screens.country(adapt(candidate({ countryCode: 'ES' })), m), true);
  assert.equal(F.screens.country(adapt(candidate({ countryCode: 'DE' })), m), false);
  assert.equal(F.screens.country(adapt(candidate()), mandate({ countries: [] })), false,
    'an empty country list admits nothing, and previews as NO_CANDIDATES (api.md §6.1)');
});

test('screen 2 — stage: only stages in scope pass', () => {
  const m = mandate({ stages: ['ready_to_build', 'construction'] });
  assert.equal(F.screens.stage(adapt(candidate({ stage: 'ready_to_build' })), m), true);
  assert.equal(F.screens.stage(adapt(candidate({ stage: 'construction' })), m), true);
  assert.equal(F.screens.stage(adapt(candidate({ stage: 'greenfield' })), m), false);
  assert.equal(F.screens.stage(adapt(candidate({ stage: 'Ready-to-build' })), m), false,
    'a display label is not a stage; the mandate emits the enum (decisions A-16)');
});

test('screen 3 — COD window: inclusive at both ends', () => {
  const m = mandate({ codFrom: 2028, codTo: 2030 });
  assert.equal(F.screens.codWindow(adapt(candidate({ codYear: 2027 })), m), false);
  assert.equal(F.screens.codWindow(adapt(candidate({ codYear: 2028 })), m), true, 'opening year is in');
  assert.equal(F.screens.codWindow(adapt(candidate({ codYear: 2030 })), m), true, 'closing year is in');
  assert.equal(F.screens.codWindow(adapt(candidate({ codYear: 2031 })), m), false);
});

test('screen 4 — min DSCR: the floor is inclusive, and an unlevered project passes', () => {
  const m = mandate({ minDscr: 1.25 });
  assert.equal(F.screens.minDscr(adapt(candidate({ minDscr: 1.24 })), m), false);
  assert.equal(F.screens.minDscr(adapt(candidate({ minDscr: 1.25 })), m), true,
    'a project exactly on the floor is not a breach');
  // api.md §2: minDscr is null where the project carries no debt. It has no debt
  // service to fail to cover, so the screen must not reject it (decisions A-21).
  assert.equal(F.screens.minDscr(adapt(candidate({ minDscr: null })), m), true,
    'an unlevered project has no coverage ratio, not a failing one');
});

test('screen 5 — risk cap: the ceiling comes from the payload, not this file', () => {
  const m = mandate({ riskAppetite: 'balanced' });
  assert.equal(F.screens.riskCap(adapt(candidate({ developmentRiskScore: 3.6 })), m, capFor(m)), true,
    'inclusive at the ceiling');
  assert.equal(F.screens.riskCap(adapt(candidate({ developmentRiskScore: 3.7 })), m, capFor(m)), false);
  assert.equal(F.screens.riskCap(adapt(candidate({ developmentRiskScore: 3.0 })), m,
    ASSUMPTIONS.riskCaps.low), false, 'low admits less');
  assert.equal(F.screens.riskCap(adapt(candidate({ developmentRiskScore: 5 })), m,
    ASSUMPTIONS.riskCaps.high), true, 'high admits the whole pipeline');
});

test('screen 5 — an appetite the assumption set does not define raises, never passes', () => {
  assert.throws(() => F.riskCap(payload([]), 'reckless'), /no risk cap configured/);
  assert.throws(() => F.riskCap({ projects: [] }, 'balanced'), /no risk cap configured/,
    'a payload with no assumptions must not silently admit everything');
  assert.throws(() => F.riskCap(payload([]), 'Balanced'), /no risk cap configured/,
    'the caps are keyed by the enum, not by the button label');
});

test('screen 6 — grid secured: only screens when the mandate asks', () => {
  const unsecured = adapt(candidate({ gridSecured: false }));
  assert.equal(F.screens.gridSecured(unsecured, mandate({ gridSecuredOnly: false })), true);
  assert.equal(F.screens.gridSecured(unsecured, mandate({ gridSecuredOnly: true })), false);
  assert.equal(F.screens.gridSecured(adapt(candidate()), mandate({ gridSecuredOnly: true })), true);
});

test('screen 7 — O&M contracted: only screens when the mandate asks', () => {
  const uncontracted = adapt(candidate({ omContracted: false }));
  assert.equal(F.screens.omContracted(uncontracted, mandate({ omContractedOnly: false })), true);
  assert.equal(F.screens.omContracted(uncontracted, mandate({ omContractedOnly: true })), false);
  assert.equal(F.screens.omContracted(adapt(candidate()), mandate({ omContractedOnly: true })), true);
});

test('screen 8 — currency: a non-EUR project is screened out, never converted', () => {
  const zloty = adapt(candidate({ countryCode: 'PL', currency: 'PLN' }));
  assert.equal(F.screens.currency(zloty, mandate({ eurRevenueOnly: false })), true);
  assert.equal(F.screens.currency(zloty, mandate({ eurRevenueOnly: true })), false);
  assert.equal(F.screens.currency(adapt(candidate()), mandate({ eurRevenueOnly: true })), true);
});

test('screen 9 — not excluded: a hand-excluded project never returns', () => {
  const p = adapt(candidate({ id: 'P07' }));
  assert.equal(F.screens.notExcluded(p, mandate(), 3.6, []), true);
  assert.equal(F.screens.notExcluded(p, mandate(), 3.6, ['P07']), false);
  assert.equal(F.screens.notExcluded(p, mandate(), 3.6, ['P08']), true);
});

test('there are exactly nine screens, and passes() applies all of them', () => {
  assert.equal(F.SCREEN_ORDER.length, 9);
  assert.deepEqual(F.SCREEN_ORDER, [
    'country', 'stage', 'codWindow', 'minDscr', 'riskCap',
    'gridSecured', 'omContracted', 'currency', 'notExcluded',
  ]);
  for (const name of F.SCREEN_ORDER) {
    assert.equal(typeof F.screens[name], 'function', `${name} must be individually testable`);
  }
  const m = mandate();
  assert.equal(F.passes(adapt(candidate()), m, capFor(m), []), true);
  assert.equal(F.passes(adapt(candidate({ countryCode: 'XX' })), m, capFor(m), []), false);
});

test('failedScreens names every screen a project misses', () => {
  const m = mandate({ gridSecuredOnly: true, eurRevenueOnly: true });
  const bad = adapt(candidate({ countryCode: 'XX', gridSecured: false, currency: 'GBP' }));
  assert.deepEqual(F.failedScreens(bad, m, capFor(m), []), ['country', 'gridSecured', 'currency']);
  assert.deepEqual(F.failedScreens(adapt(candidate()), m, capFor(m), []), []);
});

/* ── Canonical ordering ─────────────────────────────────────────────────────── */

test('projects come back ordered by id ascending, whatever order they arrived in', () => {
  const out = F.projects(payload([
    candidate({ id: 'P10' }), candidate({ id: 'P02' }), candidate({ id: 'P01' }),
  ]));
  assert.deepEqual(out.map((p) => p.id), ['P01', 'P02', 'P10'],
    'the GA indexes PRNG draws by position, so order is a determinism requirement');
});

/* ── The preview figures (api.md §5, ui-contract.md §3.4) ───────────────────── */

test('preview: candidates passing screens, out of the whole pipeline', () => {
  const r = preview([
    candidate({ id: 'P01' }), candidate({ id: 'P02', countryCode: 'XX' }), candidate({ id: 'P03' }),
  ]);
  assert.equal(r.eligibleCount, 2);
  assert.equal(r.totalCount, 3);
  assert.equal(r.display.eligibleCount, '2');
  assert.equal(r.display.totalCount, '3');
});

test('preview: eligible capacity sums only the pool', () => {
  const r = preview([
    candidate({ id: 'P01', capacityMw: 120 }),
    candidate({ id: 'P02', capacityMw: 900, countryCode: 'XX' }),
    candidate({ id: 'P03', capacityMw: 80 }),
  ]);
  assert.equal(r.eligibleCapacityMw, 200);
  assert.equal(r.display.eligibleCapacityMw, '200 MW');
});

test('preview: equity required at full draw', () => {
  const r = preview([
    candidate({ id: 'P01', equity_m: 30 }), candidate({ id: 'P02', equity_m: 45 }),
  ]);
  assert.equal(r.eligibleEquity_m, 75);
  assert.equal(r.display.eligibleEquity_m, '\u20AC75m');
});

test("preview: the pool's own solar share and gearing", () => {
  const r = preview([
    candidate({ id: 'P01', technology: 'solar', capacityMw: 300, totalCapex_m: 100, seniorDebt_m: 70 }),
    candidate({ id: 'P02', technology: 'onshore_wind', capacityMw: 100, totalCapex_m: 100, seniorDebt_m: 50 }),
  ]);
  assert.equal(r.eligibleSolarShare, 0.75, 'capacity-weighted, as the portfolio reports it');
  assert.equal(r.eligibleGearing, 0.6, 'cost-weighted');
  assert.equal(r.display.eligibleSolarShare, '75%');
  assert.equal(r.display.eligibleGearing, '60%');
});

test('an empty pool has no share and no gearing, and says so with an em dash', () => {
  const r = preview([candidate({ countryCode: 'XX' })]);
  assert.ok(Number.isNaN(r.eligibleSolarShare), 'there is no mix without a pool');
  assert.ok(Number.isNaN(r.eligibleGearing));
  assert.equal(r.display.eligibleSolarShare, fmt.DASH, 'never 0%');
  assert.equal(r.display.eligibleGearing, fmt.DASH);
  assert.equal(r.display.eligibleCapacityMw, '0 MW', 'but a capacity of zero is a real zero');
});

/* ── The warnings (api.md §5, ui-contract.md §3.5–3.6) ──────────────────────── */

const codes = (r) => r.warnings.map((w) => w.code);
const byCode = (r, code) => r.warnings.find((w) => w.code === code);

test('warning — NO_CANDIDATES blocks the run', () => {
  const r = preview([candidate({ countryCode: 'XX' })]);
  const w = byCode(r, 'NO_CANDIDATES');
  assert.ok(w);
  assert.equal(w.severity, 'blocking');
  assert.equal(w.mark, '\u00D7');
  assert.equal(w.message,
    'No candidates pass the current screens. Widen countries, stages or the COD window.');
  assert.equal(r.runnable, false, 'this is one of exactly two conditions that disable the run');
  assert.deepEqual(codes(r), ['NO_CANDIDATES'],
    'with no pool, the pool-dependent warnings stay silent');
});

test('warning — LOCKS_EXCEED_CAPITAL blocks, and names the locks to release', () => {
  const r = F.feasibility(
    payload([candidate({ id: 'P01', equity_m: 800 }), candidate({ id: 'P02', equity_m: 620 })]),
    mandate({ availableCapital_m: 1200, capacityTargetMw: 1 }),
    locks({ lockedIds: ['P01', 'P02'] }));

  const w = byCode(r, 'LOCKS_EXCEED_CAPITAL');
  assert.ok(w, '§13 requires this one case to block rather than warn');
  assert.equal(w.severity, 'blocking');
  assert.equal(w.message,
    'Locked projects need \u20AC1,420m of equity against \u20AC1,200m available. Release a lock to run.');
  assert.equal(w.detail.excess_m, 220);
  assert.deepEqual(w.detail.lockedIds, ['P01', 'P02']);
  assert.equal(r.lockedEquity_m, 1420);
  assert.equal(r.runnable, false);

  const affordable = F.feasibility(payload([candidate({ id: 'P01', equity_m: 800 })]),
    mandate({ availableCapital_m: 1200, capacityTargetMw: 1 }), locks({ lockedIds: ['P01'] }));
  assert.equal(byCode(affordable, 'LOCKS_EXCEED_CAPITAL'), undefined);
  assert.equal(affordable.runnable, true);
});

test('warning — CAPACITY_BELOW_TARGET', () => {
  const r = preview([candidate({ capacityMw: 400 })], mandate({ capacityTargetMw: 1500 }));
  const w = byCode(r, 'CAPACITY_BELOW_TARGET');
  assert.ok(w);
  assert.equal(w.severity, 'alert');
  assert.equal(w.message, 'Eligible pipeline is 400 MW \u2014 below the 1,500 MW target.');
  assert.ok(w.message.includes('\u2014'), 'an em dash, not a hyphen');
  assert.equal(r.runnable, true, 'advisory: the user may run it and see how close the optimiser gets');

  const met = preview([candidate({ capacityMw: 1500 })], mandate({ capacityTargetMw: 1500 }));
  assert.equal(byCode(met, 'CAPACITY_BELOW_TARGET'), undefined, 'exactly on target is not below it');
});

test('warning — LEVERAGE_UNREACHABLE', () => {
  const r = preview([candidate({ capacityMw: 2000, totalCapex_m: 100, seniorDebt_m: 45 })],
    mandate({ minLeverage: 0.6, capacityTargetMw: 500, availableCapital_m: 100 }));
  const w = byCode(r, 'LEVERAGE_UNREACHABLE');
  assert.ok(w);
  assert.equal(w.severity, 'alert');
  assert.equal(w.message, 'Minimum leverage of 60% exceeds what the eligible pool supports (45%).');

  const ok = preview([candidate({ capacityMw: 2000, totalCapex_m: 100, seniorDebt_m: 60 })],
    mandate({ minLeverage: 0.6, capacityTargetMw: 500, availableCapital_m: 100 }));
  assert.equal(byCode(ok, 'LEVERAGE_UNREACHABLE'), undefined, 'exactly on the floor is not a breach');
});

test('warning — SOLAR_MIX_UNREACHABLE fires only when the pool is short of solar', () => {
  const short = preview([
    candidate({ id: 'P01', technology: 'onshore_wind', capacityMw: 900 }),
    candidate({ id: 'P02', technology: 'solar', capacityMw: 100 }),
  ], mandate({ solarShare: 0.45, capacityTargetMw: 500 }));
  const w = byCode(short, 'SOLAR_MIX_UNREACHABLE');
  assert.ok(w);
  assert.equal(w.severity, 'info');
  assert.equal(w.message, 'Solar target of 45% may be unreachable: eligible pool is 10% solar.');

  const close = preview([
    candidate({ id: 'P01', technology: 'onshore_wind', capacityMw: 700 }),
    candidate({ id: 'P02', technology: 'solar', capacityMw: 300 }),
  ], mandate({ solarShare: 0.45, capacityTargetMw: 500 }));
  assert.equal(byCode(close, 'SOLAR_MIX_UNREACHABLE'), undefined, 'within 20 points stays quiet');

  // A solar-rich pool can still reach a low target by selecting fewer solar projects,
  // so the test is one-sided on purpose (decisions A-22).
  const rich = preview([candidate({ technology: 'solar', capacityMw: 1000 })],
    mandate({ solarShare: 0.2, capacityTargetMw: 500 }));
  assert.equal(byCode(rich, 'SOLAR_MIX_UNREACHABLE'), undefined,
    'a pool with more solar than the target is not unreachable');
});

test('warning — CAPITAL_UNDERUSED fires on its own, not only when capacity is met', () => {
  const r = preview([candidate({ capacityMw: 2000, equity_m: 500 })],
    mandate({ availableCapital_m: 1200, capacityTargetMw: 1500 }));
  const w = byCode(r, 'CAPITAL_UNDERUSED');
  assert.ok(w);
  assert.equal(w.severity, 'info');
  assert.equal(w.message, 'Full pipeline absorbs only \u20AC500m of the \u20AC1,200m available.');

  // ui-contract.md §3.5's trigger is the capital test alone. The mockup additionally
  // gated this on capacity being met; the contract does not (decisions A-5).
  const alsoShort = preview([candidate({ capacityMw: 400, equity_m: 500 })],
    mandate({ availableCapital_m: 1200, capacityTargetMw: 1500 }));
  assert.ok(byCode(alsoShort, 'CAPITAL_UNDERUSED'),
    'both conditions can hold, and both are reported');
  assert.ok(byCode(alsoShort, 'CAPACITY_BELOW_TARGET'));

  const full = preview([candidate({ capacityMw: 2000, equity_m: 1100 })],
    mandate({ availableCapital_m: 1200, capacityTargetMw: 1500 }));
  assert.equal(byCode(full, 'CAPITAL_UNDERUSED'), undefined, '1,100 of 1,200 is above the 90% floor');
});

test('warning — LOCKS_PRESENT counts locks and exclusions', () => {
  const r = F.feasibility(payload([candidate({ id: 'P01' }), candidate({ id: 'P02' })]),
    mandate({ capacityTargetMw: 1, availableCapital_m: 1 }),
    locks({ lockedIds: ['P01'], excludedIds: ['P09'] }));
  const w = byCode(r, 'LOCKS_PRESENT');
  assert.ok(w);
  assert.equal(w.severity, 'info');
  assert.equal(w.mark, '\u2022');
  assert.equal(w.message, '1 project(s) locked in; 1 excluded.');

  // ui-contract.md §3.5: "any lock or exclusion set" — an exclusion alone is enough.
  const excludedOnly = F.feasibility(payload([candidate({ id: 'P01' })]),
    mandate({ capacityTargetMw: 1, availableCapital_m: 1 }), locks({ excludedIds: ['P09'] }));
  assert.ok(byCode(excludedOnly, 'LOCKS_PRESENT'));

  const none = preview([candidate()], mandate({ capacityTargetMw: 1, availableCapital_m: 1 }));
  assert.equal(byCode(none, 'LOCKS_PRESENT'), undefined, 'silent when nothing is set');
});

test('warnings arrive in the §5.4 severity order, not the mockup order', () => {
  const r = F.feasibility(payload([
    candidate({ id: 'P01', technology: 'onshore_wind', capacityMw: 40,
      totalCapex_m: 100, seniorDebt_m: 45, equity_m: 55 }),
  ]), mandate({ capacityTargetMw: 1500, availableCapital_m: 1200, solarShare: 0.45, minLeverage: 0.6 }),
  locks({ lockedIds: ['P01'] }));

  // Spec order: capacity, then leverage, then solar, then capital. The mockup emits
  // capacity, capital, solar, leverage (decisions A-5).
  assert.deepEqual(codes(r), [
    'CAPACITY_BELOW_TARGET', 'LEVERAGE_UNREACHABLE',
    'SOLAR_MIX_UNREACHABLE', 'CAPITAL_UNDERUSED', 'LOCKS_PRESENT',
  ]);
});

test('every warning carries a severity and a mark, so none is colour-only', () => {
  const r = F.feasibility(payload([
    candidate({ id: 'P01', technology: 'onshore_wind', capacityMw: 40,
      totalCapex_m: 100, seniorDebt_m: 45, equity_m: 55 }),
  ]), mandate({ capacityTargetMw: 1500 }), locks({ lockedIds: ['P01'] }));

  for (const w of r.warnings) {
    assert.ok(['blocking', 'alert', 'info'].includes(w.severity), `${w.code} severity`);
    assert.ok(['alert', 'neutral'].includes(w.tone), `${w.code} tone`);
    assert.ok(w.mark.length > 0, `${w.code} must carry a mark (epic §5, decisions A-10)`);
    assert.ok(w.message.length > 0);
  }
});

test('runnable is false if and only if a warning is blocking', () => {
  const clean = preview([candidate({ capacityMw: 2000, equity_m: 1150 })],
    mandate({ capacityTargetMw: 1500, availableCapital_m: 1200 }));
  assert.equal(clean.runnable, true);

  const advisoryOnly = preview([candidate({ capacityMw: 40 })]);
  assert.ok(advisoryOnly.warnings.length > 0, 'it has warnings');
  assert.ok(advisoryOnly.warnings.every((w) => w.severity !== 'blocking'));
  assert.equal(advisoryOnly.runnable, true, 'advisory warnings never disable the run');

  const blocked = preview([candidate({ countryCode: 'XX' })]);
  assert.equal(blocked.runnable, false);
});

/* ── The loading contract ───────────────────────────────────────────────────── */

test('feasibility.js requires nothing but format.js', () => {
  const source = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'js', 'feasibility.js'), 'utf8');
  const requires = [...source.matchAll(/require\((['"])(.*?)\1\)/g)].map((m) => m[2]);
  assert.deepEqual(requires, ['./format.js'],
    'issue #12 imports this in node to check it against the Python; keep it dependency-free');

  // Strip comments and string literals: this guard is about code, not prose.
  const code = source
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/\/\/.*$/gm, ' ')
    .replace(/'(?:\\.|[^'\\])*'/g, "''")
    .replace(/"(?:\\.|[^"\\])*"/g, '""');
  for (const forbidden of [/\bdocument\s*\./, /\bwindow\s*\./, /\bAlpine\b/, /querySelector/]) {
    assert.ok(!forbidden.test(code),
      `it must stay a pure function of (payload, mandate, locks); found ${forbidden}`);
  }
});

test('feasibility.js loads under bare node with no flags', () => {
  const { execFileSync } = require('node:child_process');
  const out = execFileSync(process.execPath, [
    '-e', "process.stdout.write(String(require('./js/feasibility.js').SCREEN_ORDER.length));",
  ], { cwd: require('node:path').join(__dirname, '..'), encoding: 'utf8' });
  assert.equal(out, '9');
});

test('the preview response carries every field api.md §5 pins', () => {
  const r = preview([candidate()]);
  for (const field of ['eligibleCount', 'totalCount', 'eligibleCapacityMw', 'eligibleEquity_m',
    'eligibleSolarShare', 'eligibleGearing', 'lockedEquity_m', 'warnings', 'runnable']) {
    assert.ok(field in r, `POST /mandate/preview returns ${field}; the client must match it`);
  }
});
