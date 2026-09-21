/**
 * The front end against the documents that pin it: api.md and ui-contract.md.
 *
 * These landed in #3 after this work started, and reconciling against them changed
 * every field name in feasibility.js. The point of this file is that the next such
 * change is caught here rather than by whoever wires the API and finds an empty pool.
 *
 * Read from the documents themselves, not from a copy, so the test fails when the
 * contract moves rather than when someone forgets to update a second list.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const F = require('../js/feasibility.js');
const { WEB, loadPage } = require('./helpers/page.js');

const DOCS = path.resolve(WEB, '..', 'docs');
const read = (f) => fs.readFileSync(path.join(DOCS, f), 'utf8');

const hasDocs = fs.existsSync(path.join(DOCS, 'api.md'));

test('api.md and ui-contract.md are present', () => {
  assert.ok(hasDocs, 'issue #3 owns these; this suite reconciles against them');
});

/** The mandate object's field names, parsed out of api.md §6.1's table. */
function mandateFields() {
  const api = read('api.md');
  const section = api.slice(api.indexOf('### 6.1 The mandate object'), api.indexOf('### 6.2'));
  const names = [];
  for (const row of section.split('\n')) {
    const cell = /^\|\s*(`[^|]+`)\s*\|/.exec(row);
    if (cell) for (const m of cell[1].matchAll(/`([A-Za-z_]+)`/g)) names.push(m[1]);
  }
  return names;
}

test('every control on the mandate emits a field api.md §6.1 defines', async () => {
  const fields = new Set(mandateFields());
  assert.ok(fields.size >= 18, `parsed ${fields.size} mandate fields; expected at least 18`);

  const dom = await loadPage('mandate.html');
  const html = dom.window.document.documentElement.outerHTML;
  dom.window.close();

  const emitted = [...html.matchAll(/name: '([A-Za-z_]+)'/g)].map((m) => m[1]);
  assert.ok(emitted.length >= 15, `found ${emitted.length} named controls`);

  for (const name of new Set(emitted)) {
    assert.ok(fields.has(name),
      `the page emits "${name}", which is not a field in api.md §6.1's mandate object`);
  }
});

test('the mandate page covers every field api.md §6.1 defines', async () => {
  const dom = await loadPage('mandate.html');
  const html = dom.window.document.documentElement.outerHTML;
  dom.window.close();
  const emitted = new Set([...html.matchAll(/name: '([A-Za-z_]+)'/g)].map((m) => m[1]));

  for (const field of mandateFields()) {
    assert.ok(emitted.has(field),
      `api.md §6.1 defines "${field}" but no control on the mandate emits it`);
  }
});

/* ── Warnings: codes, severities and order ─────────────────────────────────── */

/** The code/severity table from api.md §5. */
function warningCodes() {
  const api = read('api.md');
  const rows = [...api.matchAll(/^\| `([A-Z_]+)` \| (blocking|alert|info) \|/gm)];
  return rows.map((m) => ({ code: m[1], severity: m[2] }));
}

test('feasibility.js emits exactly the warning codes api.md §5 pins, in that order', () => {
  const pinned = warningCodes();
  assert.equal(pinned.length, 7, `api.md pins ${pinned.length} warning codes`);

  // A pipeline and mandate that trip every advisory warning at once, with locks that
  // alone exceed capital, so all seven can be observed in one result.
  const project = (over) => ({ id: 'P01', countryCode: 'ES', stage: 'ready_to_build',
    technology: 'onshore_wind', capacityMw: 40, codYear: 2029, minDscr: 1.4,
    developmentRiskScore: 2, gridSecured: true, omContracted: true, currency: 'EUR',
    totalCapex_m: 100, seniorDebt_m: 45, equity_m: 55, ...over });
  const mandate = { countries: ['ES'], stages: ['ready_to_build'], codFrom: 2027, codTo: 2032,
    minDscr: 1.25, riskAppetite: 'balanced', gridSecuredOnly: false, omContractedOnly: false,
    eurRevenueOnly: false, availableCapital_m: 1200, capacityTargetMw: 1500,
    solarShare: 0.45, minLeverage: 0.6 };
  const assumptions = { riskCaps: { low: 2.6, balanced: 3.6, high: 5 } };

  const advisory = F.feasibility({ projects: [project()], assumptions }, mandate,
    { lockedIds: ['P01'], excludedIds: [] });
  const blocked = F.feasibility({ projects: [project({ countryCode: 'XX' })], assumptions },
    mandate, { lockedIds: [], excludedIds: [] });
  const overLocked = F.feasibility({ projects: [project({ equity_m: 5000 })], assumptions },
    mandate, { lockedIds: ['P01'], excludedIds: [] });

  const seen = new Map();
  for (const result of [blocked, overLocked, advisory]) {
    for (const w of result.warnings) seen.set(w.code, w.severity);
  }

  for (const { code, severity } of pinned) {
    assert.ok(seen.has(code), `api.md pins ${code} but feasibility.js never emits it`);
    assert.equal(seen.get(code), severity,
      `${code} must be ${severity}; runnable depends on it`);
  }
  for (const code of seen.keys()) {
    assert.ok(pinned.some((p) => p.code === code), `${code} is not a code api.md defines`);
  }

  // Emission order follows api.md's table, which is §5.4's order of severity (A-5).
  const order = pinned.map((p) => p.code);
  const emitted = advisory.warnings.map((w) => w.code);
  const positions = emitted.map((c) => order.indexOf(c));
  assert.deepEqual(positions, [...positions].sort((a, b) => a - b),
    `warnings came out as ${emitted.join(', ')}, which is not the §5.4 severity order`);
});

test('the warning strings match ui-contract.md §3.5 and §3.6 verbatim', () => {
  const ui = read('ui-contract.md');
  const templates = [
    'No candidates pass the current screens. Widen countries, stages or the COD window.',
    'Eligible pipeline is {mw} MW — below the {target} MW target.',
    'Minimum leverage of {minLev}% exceeds what the eligible pool supports ({poolLev}%).',
    'Solar target of {solar}% may be unreachable: eligible pool is {poolSolar}% solar.',
    'Full pipeline absorbs only €{equity}m of the €{capital}m available.',
    '{n} project(s) locked in; {m} excluded.',
    'Locked projects need €{locked}m of equity against €{capital}m available. Release a lock to run.',
  ];
  for (const template of templates) {
    assert.ok(ui.includes(template),
      `ui-contract.md no longer carries "${template.slice(0, 48)}…" — reconcile feasibility.js`);
  }

  // And the shapes our messages take, with the numbers substituted.
  const source = fs.readFileSync(path.join(WEB, 'js', 'feasibility.js'), 'utf8');
  for (const fragment of [
    'No candidates pass the current screens. ',
    'Widen countries, stages or the COD window.',
    ' \u2014 below the ',
    ' exceeds what the eligible pool supports (',
    ' may be unreachable: eligible pool is ',
    'Full pipeline absorbs only ',
    ' project(s) locked in; ',
    'Release a lock to run.',
  ]) {
    assert.ok(source.includes(fragment), `feasibility.js lost the pinned fragment "${fragment}"`);
  }
});

test('the marks are the exact code points, not ASCII lookalikes', () => {
  const project = { id: 'P01', countryCode: 'ES', stage: 'ready_to_build', technology: 'solar',
    capacityMw: 1, codYear: 2029, minDscr: 1.4, developmentRiskScore: 2, gridSecured: true,
    omContracted: true, currency: 'EUR', totalCapex_m: 1, seniorDebt_m: 0.6, equity_m: 0.4 };
  const assumptions = { riskCaps: { low: 2.6, balanced: 3.6, high: 5 } };
  const base = { countries: ['ES'], stages: ['ready_to_build'], codFrom: 2027, codTo: 2032,
    minDscr: 1.25, riskAppetite: 'balanced', availableCapital_m: 1200, capacityTargetMw: 1500,
    solarShare: 0.45, minLeverage: 0.6 };

  const blocked = F.feasibility({ projects: [{ ...project, countryCode: 'XX' }], assumptions },
    base, { lockedIds: [], excludedIds: [] });
  const advisory = F.feasibility({ projects: [project], assumptions }, base,
    { lockedIds: ['P01'], excludedIds: [] });

  const mark = (r, code) => r.warnings.find((w) => w.code === code).mark;
  // × is U+00D7, not the letter x. • is U+2022, not an asterisk.
  assert.equal(mark(blocked, 'NO_CANDIDATES').codePointAt(0), 0x00d7);
  assert.equal(mark(advisory, 'CAPACITY_BELOW_TARGET'), '!');
  assert.equal(mark(advisory, 'LOCKS_PRESENT').codePointAt(0), 0x2022);

  // The em dash inside the capacity message is U+2014, not a hyphen.
  const message = advisory.warnings.find((w) => w.code === 'CAPACITY_BELOW_TARGET').message;
  assert.ok(message.includes('\u2014'), 'em dash U+2014');
  assert.ok(!message.includes(' - '), 'never an ASCII hyphen standing in for it');
});

/* ── The two cash-flow series ──────────────────────────────────────────────── */

test('the portfolio page names the two cash-flow series apart, as api.md §1.3 does', async () => {
  const dom = await loadPage('portfolio.html');
  const html = dom.window.document.documentElement.outerHTML;
  dom.window.close();

  assert.ok(html.includes('data-series="cashflow30Y_m"'),
    'the chart is the 30-year series, which carries no terminal value');
  assert.ok(html.includes('data-series="cashflowHold_m"'),
    'IRR and MOIC come from the hold-truncated series, which does');
  assert.ok(!/data-series="(fcfe30|hold-irr)"/.test(html),
    'the old local names must not survive; A-6 says the two never share a vocabulary');
});

/* ── Search-screen copy ────────────────────────────────────────────────────── */

test('the search screen uses ui-contract.md §4 copy, not the mockup\'s', async () => {
  const dom = await loadPage('search.html');
  const text = dom.window.document.body.textContent.replace(/\s+/g, ' ');
  dom.window.close();

  for (const required of ['Searching the pipeline', 'Progress towards the mandate',
    'Best portfolio', 'Average of all candidates', 'Mandate score', 'ROUND']) {
    assert.ok(text.includes(required), `§4 pins "${required}"`);
  }
  for (const banned of ['Searching the solution space', 'Fitness convergence',
    'Best fitness', 'Population mean', 'GENERATION']) {
    assert.ok(!text.includes(banned), `"${banned}" is mockup copy §14 removes (A-11)`);
  }
});

/* ── Footer figures ────────────────────────────────────────────────────────── */

test('the action bar shows the three figures ui-contract.md §3.4 pins', async () => {
  const dom = await loadPage('mandate.html');
  const d = dom.window.document;
  const labels = [...d.querySelectorAll('[data-region="feasibility-figures"] dt')]
    .map((el) => el.textContent.trim());
  dom.window.close();

  assert.deepEqual(labels, [
    'Candidates passing screens', 'Eligible capacity', 'Equity required at full draw',
  ], 'three figures, in this order');
});

test("the pool's solar share and gearing are still computed, they are just not figures", () => {
  const result = F.feasibility({
    projects: [{ id: 'P01', countryCode: 'ES', stage: 'ready_to_build', technology: 'solar',
      capacityMw: 100, codYear: 2029, minDscr: 1.4, developmentRiskScore: 2, gridSecured: true,
      omContracted: true, currency: 'EUR', totalCapex_m: 100, seniorDebt_m: 60, equity_m: 40 }],
    assumptions: { riskCaps: { low: 2.6, balanced: 3.6, high: 5 } },
  }, { countries: ['ES'], stages: ['ready_to_build'], codFrom: 2027, codTo: 2032, minDscr: 1.25,
    riskAppetite: 'balanced', availableCapital_m: 1200, capacityTargetMw: 1, solarShare: 0.45,
    minLeverage: 0.6 }, { lockedIds: [], excludedIds: [] });

  // They are part of POST /mandate/preview's response, and warnings 3 and 4 need them.
  assert.equal(result.eligibleSolarShare, 1);
  assert.equal(result.eligibleGearing, 0.6);
});
