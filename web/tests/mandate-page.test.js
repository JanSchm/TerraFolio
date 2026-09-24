/**
 * Screen 01 wired to the client: the store that survives a session, and the footer
 * that recomputes on every change.
 *
 * The page is loaded from disk, which is how the whole suite loads it and also the
 * state a user gets with no server: every figure an em dash, nothing thrown, the run
 * button still reachable. The rendering tests then hand it a payload directly, which
 * is exactly what api.js hands it over HTTP.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadPage } = require('./helpers/page.js');
const { servePage } = require('./helpers/served.js');
const status = require('../js/controls.js').status;

/**
 * A value brought back into this realm.
 *
 * The page's own scripts run inside the jsdom window, so an array they build has
 * that window's `Array.prototype` and `deepEqual` — which compares prototypes —
 * reports "same structure but not reference-equal". Nothing about the page is wrong;
 * the comparison simply has to cross the same boundary the value did.
 */
const plain = (value) => JSON.parse(JSON.stringify(value));

/** A GET /pipeline payload with the assumption set already folded in, as api.js composes it. */
function payload(projects) {
  return {
    pipelineHash: 'sha256:9f2c',
    assumptionSetId: 'default-2026',
    projectCount: projects.length,
    projects: projects,
    assumptions: { riskCaps: { low: 2.6, balanced: 3.6, high: 5 } },
  };
}

function candidate(overrides) {
  return Object.assign({
    id: 'P01',
    countryCode: 'ES',
    stage: 'ready_to_build',
    technology: 'solar',
    capacityMw: 400,
    codYear: 2029,
    minDscr: 1.4,
    developmentRiskScore: 2.2,
    gridSecured: true,
    omContracted: true,
    currency: 'EUR',
    totalCapex_m: 400,
    seniorDebt_m: 280,
    equity_m: 120,
  }, overrides || {});
}

/**
 * The page as a server sends it. Storage throws on a `file://` origin, so anything
 * that persists — the mandate, the locks — can only be observed here.
 */
async function page() {
  const dom = await servePage('mandate.html');
  const M = dom.window.TerraFolio.mandate;
  const form = dom.window.document.querySelector('[data-form="mandate"]');
  return { dom, w: dom.window, d: dom.window.document, M, form };
}

/** Drives the real event path a control uses, rather than calling render directly. */
function change(ctx, name, value) {
  ctx.form.dispatchEvent(new ctx.w.CustomEvent('tf:change', {
    detail: { name: name, value: value }, bubbles: true,
  }));
}

const field = (ctx, name) => ctx.d.querySelector('[data-field="' + name + '"]').textContent.trim();
const warnings = (ctx) => [...ctx.d.querySelectorAll('[data-region="feasibility-warnings"] li')];

/* ── With no server ──────────────────────────────────────────────────────────── */

test('the page opens from disk with every figure an em dash and nothing thrown', async () => {
  const dom = await loadPage('mandate.html');
  const ctx = { dom, w: dom.window, d: dom.window.document };
  for (const name of ['totalCount', 'eligibleCount', 'eligibleCapacityMw', 'eligibleEquity_m']) {
    assert.equal(field(ctx, name), '—', `${name} has no value yet, and says so`);
  }
  assert.deepEqual(warnings(ctx), []);
  ctx.dom.window.close();
});

test('the run button stays reachable with no server, because only §5.4 disables it', async () => {
  const dom = await loadPage('mandate.html');
  const ctx = { dom, d: dom.window.document };
  const run = ctx.d.querySelector('[data-action="run"]');
  assert.equal(run.disabled, false,
    'ui-contract.md §3.6: exactly two warnings disable the run, and neither is "offline"');
  ctx.dom.window.close();
});

/* ── The mandate the controls carry ──────────────────────────────────────────── */

test('the mandate is read off the controls, so its fields cannot drift from them', async () => {
  const ctx = await page();
  const mandate = ctx.M.readMandate(ctx.form);
  assert.equal(Object.keys(mandate).length, 18, 'api.md §6.1 has eighteen fields');
  assert.equal(mandate.availableCapital_m, 1200);
  assert.equal(mandate.solarShare, 0.45, 'fractions throughout, never whole percent (D13)');
  assert.equal(mandate.targetIrr, 0.11);
  assert.equal(mandate.maxMerchantShare, 0.35);
  assert.equal(mandate.riskAppetite, 'balanced');
  assert.equal(mandate.gridSecuredOnly, false);
  assert.deepEqual(plain(mandate.stages), ['greenfield', 'ready_to_build', 'construction']);
  assert.equal(mandate.countries.length, 14, 'ui-contract.md §3.2 names fourteen markets');
  ctx.dom.window.close();
});

test('a saved mandate is applied back onto the controls it came from', async () => {
  const ctx = await page();
  const saved = Object.assign(ctx.M.readMandate(ctx.form), {
    availableCapital_m: 2500, solarShare: 0.7, targetIrr: 0.135,
    maxMerchantShare: 0.2, riskAppetite: 'high', countries: ['ES', 'PT'],
    eurRevenueOnly: true, holdYears: 15,
  });
  ctx.M.applyMandate(ctx.form, saved);
  assert.deepEqual(plain(ctx.M.readMandate(ctx.form)), plain(saved),
    'every control round-trips its own scale');
  ctx.dom.window.close();
});

test('the restored value is the one the user typed, not a binary artefact of it', async () => {
  const ctx = await page();
  ctx.M.applyMandate(ctx.form, { targetIrr: 0.11, minLeverage: 0.6 });
  const back = ctx.M.readMandate(ctx.form);
  assert.equal(back.targetIrr, 0.11, '11 / 100 * 100 is 11.000000000000002 without rounding');
  assert.equal(back.minLeverage, 0.6);
  ctx.dom.window.close();
});

/* ── Storage ─────────────────────────────────────────────────────────────────── */

test('the mandate persists between sessions and the steering within one', async () => {
  const ctx = await page();
  ctx.M.saveMandate({ availableCapital_m: 3000, holdYears: 20 });
  assert.deepEqual(plain(ctx.M.savedMandate()), { availableCapital_m: 3000, holdYears: 20 });
  assert.equal(ctx.w.localStorage.getItem(ctx.M.MANDATE_KEY) !== null, true,
    'spec §5: every control persists per user between sessions');
  assert.equal(ctx.w.sessionStorage.getItem(ctx.M.MANDATE_KEY), null);
  ctx.dom.window.close();
});

test('storage that cannot be read yields an empty store rather than an exception', async () => {
  const ctx = await page();
  ctx.w.localStorage.setItem(ctx.M.MANDATE_KEY, 'not json');
  assert.equal(ctx.M.savedMandate(), null);
  ctx.w.sessionStorage.setItem(ctx.M.STEERING_KEY, '[1,2,3]');
  assert.deepEqual(plain(ctx.M.steering().lockedIds), []);
  ctx.dom.window.close();
});

/* ── Steering (§7.6) ─────────────────────────────────────────────────────────── */

test('locks and exclusions accumulate across runs', async () => {
  const ctx = await page();
  ctx.M.steer('P03', { locked: true });
  ctx.M.steer('P01', { locked: true });
  ctx.M.steer('P09', { excluded: true });
  const held = ctx.M.steering();
  assert.deepEqual(plain(held.lockedIds), ['P01', 'P03'], 'kept in id order, as every array is');
  assert.deepEqual(plain(held.excludedIds), ['P09']);
  ctx.dom.window.close();
});

test('excluding a project clears its lock, because the two contradict each other', async () => {
  const ctx = await page();
  ctx.M.steer('P01', { locked: true });
  ctx.M.steer('P01', { excluded: true });
  const held = ctx.M.steering();
  assert.deepEqual(plain(held.lockedIds), [],
    'ui-contract.md §5.5: excluding also clears the lock');
  assert.deepEqual(plain(held.excludedIds), ['P01']);
  ctx.dom.window.close();
});

test('a lock can be released and an exclusion re-admitted', async () => {
  const ctx = await page();
  ctx.M.steer('P01', { locked: true });
  ctx.M.steer('P01', { locked: false });
  ctx.M.steer('P02', { excluded: true });
  ctx.M.steer('P02', { excluded: false });
  assert.deepEqual(plain(ctx.M.steering()), {
    lockedIds: [], excludedIds: [], runId: null, runRef: null, signature: null,
    totalRounds: null,
  });
  ctx.dom.window.close();
});

test('the signature moves when the mandate or the steering does, and not otherwise', async () => {
  const ctx = await page();
  const m = ctx.M.readMandate(ctx.form);
  const none = { lockedIds: [], excludedIds: [] };
  const reordered = {};
  Object.keys(m).reverse().forEach((k) => { reordered[k] = m[k]; });
  assert.equal(ctx.M.signature(m, none), ctx.M.signature(reordered, none),
    'a store rebuilt in another order is the same mandate');
  assert.notEqual(ctx.M.signature(m, none),
    ctx.M.signature(Object.assign({}, m, { holdYears: 15 }), none));
  assert.notEqual(ctx.M.signature(m, none), ctx.M.signature(m, { lockedIds: ['P01'], excludedIds: [] }));
  ctx.dom.window.close();
});

/* ── The footer ──────────────────────────────────────────────────────────────── */

test('the three §3.4 figures and the eyebrow come from one computation', async () => {
  const ctx = await page();
  const p = ctx.M.startPage();
  ctx.M.usePipeline(p, payload([
    candidate({ id: 'P01' }),
    candidate({ id: 'P02', countryCode: 'FI', capacityMw: 100, equity_m: 50, totalCapex_m: 200, seniorDebt_m: 150 }),
  ]));
  p.mandate = Object.assign(ctx.M.readMandate(ctx.form), { countries: ['ES', 'PT'] });
  change(ctx, 'countries', ['ES', 'PT']);

  assert.equal(field(ctx, 'totalCount'), '2');
  assert.equal(field(ctx, 'totalCapacityMw'), '500 MW', 'the eyebrow counts the whole directory');
  assert.equal(field(ctx, 'eligibleCount'), '1', 'the Finnish project is off the country list');
  assert.equal(field(ctx, 'eligibleCapacityMw'), '400 MW');
  assert.equal(field(ctx, 'eligibleEquity_m'), '€120m');
  ctx.dom.window.close();
});

test('a warning carries its mark, its severity in words and a tone that is never alone', async () => {
  const ctx = await page();
  const p = ctx.M.startPage();
  ctx.M.usePipeline(p, payload([candidate({ id: 'P01', capacityMw: 100 })]));
  p.mandate = Object.assign(ctx.M.readMandate(ctx.form), { capacityTargetMw: 4000 });
  change(ctx, 'capacityTargetMw', 4000);

  const shown = warnings(ctx);
  const below = shown.find((li) => li.dataset.code === 'CAPACITY_BELOW_TARGET');
  assert.ok(below, 'the eligible pipeline is far below the target');
  assert.equal(below.querySelector('[aria-hidden="true"]').textContent, status.MARK.alert);
  assert.equal(below.querySelector('.sr-only').textContent, status.WORD.alert);
  assert.match(below.className, /text-breach/);
  assert.match(below.textContent, /below the 4,000 MW target\./);
  ctx.dom.window.close();
});

test('warnings arrive in the §5.4 order of severity, alert before note', async () => {
  const ctx = await page();
  const p = ctx.M.startPage();
  ctx.M.usePipeline(p, payload([candidate({ id: 'P01', capacityMw: 100, equity_m: 20, technology: 'onshore_wind' })]));
  p.mandate = Object.assign(ctx.M.readMandate(ctx.form), { capacityTargetMw: 4000, solarShare: 0.9 });
  change(ctx, 'solarShare', 0.9);
  const codes = warnings(ctx).map((li) => li.dataset.code);
  assert.deepEqual(codes.indexOf('CAPACITY_BELOW_TARGET'), 0);
  assert.ok(codes.indexOf('SOLAR_MIX_UNREACHABLE') > 0, 'an info warning never precedes an alert');
  ctx.dom.window.close();
});

test('only "no candidate passes" disables the run', async () => {
  const ctx = await page();
  const p = ctx.M.startPage();
  const run = ctx.d.querySelector('[data-action="run"]');
  ctx.M.usePipeline(p, payload([candidate({ id: 'P01', capacityMw: 100 })]));

  p.mandate = Object.assign(ctx.M.readMandate(ctx.form), { capacityTargetMw: 4000 });
  change(ctx, 'capacityTargetMw', 4000);
  assert.equal(run.disabled, false,
    'spec §5.4: the user may run an infeasible-looking mandate and see how close it gets');

  p.mandate = Object.assign(ctx.M.readMandate(ctx.form), { codFrom: 2033, codTo: 2033 });
  change(ctx, 'codTo', 2033);
  assert.equal(warnings(ctx)[0].dataset.code, 'NO_CANDIDATES');
  assert.equal(run.disabled, true);
  ctx.dom.window.close();
});

test('the footer reflects a lock that re-admits a project past the screens', async () => {
  const ctx = await page();
  const p = ctx.M.startPage();
  ctx.M.usePipeline(p, payload([candidate({ id: 'P01' }), candidate({ id: 'P02', codYear: 2040 })]));
  p.mandate = ctx.M.readMandate(ctx.form);
  change(ctx, 'minDscr', 1.25);
  assert.equal(field(ctx, 'eligibleCount'), '1');

  ctx.M.steer('P02', { locked: true });
  change(ctx, 'minDscr', 1.25);
  assert.equal(field(ctx, 'eligibleCount'), '2');
  assert.ok(warnings(ctx).some((li) => li.dataset.code === 'LOCKS_PRESENT'),
    'the override is surfaced rather than implicit');
  ctx.dom.window.close();
});

/* ── The validation banner ───────────────────────────────────────────────────── */

test('a pipeline with nothing rejected shows no banner', async () => {
  const ctx = await page();
  ctx.M.renderHealth({ fileCount: 300, loadedCount: 300, rejected: [] });
  assert.equal(ctx.d.querySelector('[data-region="pipeline-health"]').hidden, true);
  ctx.dom.window.close();
});

test('a rejected file is counted, and its reason is one click away', async () => {
  const ctx = await page();
  ctx.M.renderHealth({
    fileCount: 300,
    loadedCount: 298,
    rejected: [
      { file: 'P217.json', check: 'debt.closing[last] = 0', message: 'P217: closing balance remains.' },
      { file: 'P044.json', check: 'funding', message: 'P044: sources and uses differ.' },
    ],
  });
  assert.equal(ctx.d.querySelector('[data-region="pipeline-health"]').hidden, false);
  assert.equal(field(ctx, 'rejectedCount'), '2 files excluded by validation');

  const button = ctx.d.querySelector('[data-action="show-rejected"]');
  const list = ctx.d.querySelector('[data-region="rejected-files"]');
  assert.equal(list.hidden, true);
  assert.equal(button.getAttribute('aria-expanded'), 'false');
  ctx.M.toggleRejected(button);
  assert.equal(list.hidden, false);
  assert.equal(button.getAttribute('aria-expanded'), 'true');
  assert.equal(list.querySelectorAll('li').length, 2);
  assert.match(list.textContent, /P217\.json/);
  assert.match(list.textContent, /sources and uses differ/);
  ctx.dom.window.close();
});

test('one rejected file is counted in the singular', async () => {
  const ctx = await page();
  ctx.M.renderHealth({ rejected: [{ file: 'P217.json', message: 'P217: closing balance remains.' }] });
  assert.equal(field(ctx, 'rejectedCount'), '1 file excluded by validation');
  ctx.dom.window.close();
});

test('a page opened from disk cannot persist, and states a mandate anyway', async () => {
  const dom = await loadPage('mandate.html');
  const M = dom.window.TerraFolio.mandate;
  // Both storage areas throw SecurityError on an opaque origin. A page that cannot
  // remember a mandate must still let the user state one.
  assert.equal(M.savedMandate(), null);
  assert.doesNotThrow(() => M.saveMandate({ holdYears: 12 }));
  assert.deepEqual(plain(M.steering().lockedIds), []);
  assert.equal(Object.keys(M.readMandate(dom.window.document.querySelector('[data-form="mandate"]'))).length, 18);
  dom.window.close();
});

/* ── The two steering instructions are symmetric ─────────────────────────────── */

test('locking a project the user had excluded clears the exclusion', async () => {
  const ctx = await page();
  ctx.M.steer('P01', { excluded: true });
  ctx.M.steer('P01', { locked: true });
  const held = ctx.M.steering();
  assert.deepEqual(plain(held.lockedIds), ['P01']);
  assert.deepEqual(plain(held.excludedIds), [],
    'the two instructions contradict each other; the second one given is the one meant');
  ctx.dom.window.close();
});

test('neither set can hold the same project as the other', async () => {
  const ctx = await page();
  for (const order of [['excluded', 'locked'], ['locked', 'excluded']]) {
    ctx.M.saveSteering({ lockedIds: [], excludedIds: [] });
    ctx.M.steer('P07', { [order[0]]: true });
    ctx.M.steer('P07', { [order[1]]: true });
    const held = ctx.M.steering();
    const both = held.lockedIds.filter((id) => held.excludedIds.indexOf(id) !== -1);
    assert.deepEqual(plain(both), [], `applying ${order.join(' then ')} left it in both`);
  }
  ctx.dom.window.close();
});

/* ── The run's length survives the store ─────────────────────────────────────── */

test('totalRounds round-trips, so the search screen can know the total in advance', async () => {
  const ctx = await page();
  const held = ctx.M.steering();
  held.runId = '01JB2Q';
  held.totalRounds = 60;
  ctx.M.saveSteering(held);
  assert.equal(ctx.M.steering().totalRounds, 60,
    'decisions 3B-3: an unknown total keeps the whole live region silent');
  ctx.dom.window.close();
});

test('a later save built from steering() does not erase the total', async () => {
  const ctx = await page();
  ctx.M.saveSteering(Object.assign(ctx.M.steering(), { runId: 'x', totalRounds: 110 }));
  ctx.M.steer('P01', { locked: true });
  assert.equal(ctx.M.steering().totalRounds, 110,
    'steer() rebuilds the object from steering(); a dropped field is lost there');
  ctx.dom.window.close();
});

/* ── Persistence trails the drag rather than riding it ───────────────────────── */

test('the mandate is written once the user stops moving, not once per tick', async () => {
  const ctx = await page();
  // Storage is a Proxy whose `set` trap stores a key, so assigning `setItem` on the
  // instance writes an entry called "setItem" instead of replacing the method. The
  // prototype is the only place a spy sticks.
  const writes = [];
  const proto = Object.getPrototypeOf(ctx.w.localStorage);
  const real = proto.setItem;
  proto.setItem = function (key, value) { writes.push(key); return real.call(this, key, value); };

  for (let mw = 200; mw <= 1000; mw += 50) change(ctx, 'capacityTargetMw', mw);
  assert.deepEqual(writes, [], 'A-18 fires on input: one drag is dozens of events');

  ctx.M.flushMandate();
  assert.equal(writes.filter((k) => k === ctx.M.MANDATE_KEY).length, 1);
  assert.equal(ctx.M.savedMandate().capacityTargetMw, 1000, 'and the last value is the one kept');
  proto.setItem = real;
  ctx.dom.window.close();
});

test('a field no control on this form owns never reaches the mandate', async () => {
  const ctx = await page();
  const p = ctx.M.startPage();
  const before = Object.keys(ctx.M.readMandate(ctx.form)).length;
  change(ctx, 'somethingElse', 42);
  assert.equal(Object.keys(p.mandate).length, before,
    'Mandate forbids extra keys, so a stray name would be a 400 rather than ignored');
  assert.equal(p.mandate.somethingElse, undefined);
  ctx.dom.window.close();
});

/* ── §13: naming the screens to widen ────────────────────────────────────────── */

test('the screens named are the ones the user can see, not the wire\'s', async () => {
  const ctx = await page();
  const labels = ctx.M.SCREEN_LABELS;
  assert.equal(labels.eurRevenue, 'EUR-denominated revenue only',
    'a user cannot widen a thing called eurRevenue');
  assert.equal(labels.riskScore, 'Development risk appetite');
  assert.equal(labels.minDscr, 'Min DSCR');
  assert.deepEqual(Object.keys(labels).sort(),
    Object.keys(require('../js/feasibility.js').WIRE_SCREEN_NAMES)
      .map((k) => require('../js/feasibility.js').WIRE_SCREEN_NAMES[k]).sort(),
    'every screen the preview can name has a label here');
  ctx.dom.window.close();
});

test('the sentence reads for one screen, for several, and stops at three', async () => {
  const ctx = await page();
  const widen = ctx.M.widenSentence;
  assert.equal(widen(['minDscr']), 'Every candidate is dropped by Min DSCR.');
  assert.equal(widen(['minDscr', 'riskScore']),
    'Most are dropped by Min DSCR and Development risk appetite.');
  assert.match(widen(['minDscr', 'codWindow', 'eurRevenue', 'countries']),
    /^Most are dropped by Min DSCR, the COD window and EUR-denominated revenue only\.$/);
  assert.equal(widen([]), '', 'nothing to widen is nothing to say');
  ctx.dom.window.close();
});

test('an empty pool names what actually emptied it, beside the pinned sentence', async () => {
  const ctx = await page();
  const p = ctx.M.startPage();
  // Nothing passes, and the reason is the DSCR floor rather than the three screens
  // ui-contract §3.5's string happens to name.
  ctx.M.usePipeline(p, payload([candidate({ id: 'P01', minDscr: 1.1 })]));
  p.mandate = Object.assign(ctx.M.readMandate(ctx.form), { minDscr: 2 });
  change(ctx, 'minDscr', 2);

  const item = ctx.d.querySelector('[data-region="feasibility-warnings"] li');
  assert.equal(item.dataset.code, 'NO_CANDIDATES');
  assert.match(item.textContent,
    /No candidates pass the current screens\. Widen countries, stages or the COD window\./,
    'the pinned sentence is the server\'s message and stays exactly as it is');
  assert.match(item.textContent, /Every candidate is dropped by Min DSCR\./,
    'spec §13: an explicit warning naming the screens to widen');
  assert.equal(ctx.d.querySelector('[data-action="run"]').disabled, true);
  ctx.dom.window.close();
});

test('only the blocking warning carries the extra line', async () => {
  const ctx = await page();
  const p = ctx.M.startPage();
  ctx.M.usePipeline(p, payload([candidate({ id: 'P01', capacityMw: 10 })]));
  p.mandate = Object.assign(ctx.M.readMandate(ctx.form), { capacityTargetMw: 4000 });
  change(ctx, 'capacityTargetMw', 4000);
  const below = [...ctx.d.querySelectorAll('[data-region="feasibility-warnings"] li')]
    .find((li) => li.dataset.code === 'CAPACITY_BELOW_TARGET');
  assert.equal(/dropped by/.test(below.textContent), false,
    'a pipeline smaller than the target is not a screen anyone can widen');
  ctx.dom.window.close();
});
