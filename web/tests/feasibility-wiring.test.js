/**
 * The three places issue #11 found `feasibility.js` disagreeing with the server.
 *
 * api.md §5 is explicit that the client and POST /mandate/preview must agree field
 * for field, and that where they diverge the client is wrong. These are unit tests
 * of the three fixes; the whole-pipeline comparison that found them is reported in
 * the pull request, and #12 owns the standing parity test.
 *
 * Kept in their own file rather than folded into feasibility.test.js so that issue
 * #5's own suite stays exactly as it shipped.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const F = require('../js/feasibility.js');

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

const ASSUMPTIONS = { riskCaps: { low: 2.6, balanced: 3.6, high: 5 } };
const payload = (projects) => ({ projects, assumptions: ASSUMPTIONS });
const preview = (projects, m = mandate(), l = {}) =>
  F.feasibility(payload(projects), m, { lockedIds: [], excludedIds: [], ...l });
const CAP = ASSUMPTIONS.riskCaps.balanced;

/* ── 1. UK is an alias for GB, at both ends ──────────────────────────────────── */

test('isoCountry collapses the UK alias and leaves every other code alone', () => {
  assert.equal(F.isoCountry('UK'), 'GB');
  assert.equal(F.isoCountry('GB'), 'GB');
  assert.equal(F.isoCountry('ES'), 'ES');
});

test('a GB project passes a mandate that spells the market UK', () => {
  const british = candidate({ countryCode: 'GB' });
  assert.equal(F.screens.country(british, mandate({ countries: ['UK'] })), true,
    'ui-contract.md §3.2 names the chip UK; every loaded file carries ISO GB');
  assert.equal(F.screens.country(british, mandate({ countries: ['GB'] })), true);
});

test('a UK-declared project passes a mandate that spells the market GB', () => {
  const british = candidate({ countryCode: 'UK' });
  assert.equal(F.screens.country(british, mandate({ countries: ['GB'] })), true,
    'pipeline-schema.md §4.1 accepts UK as an alias, so the file end needs it too');
});

test('the alias does not admit a country the mandate never selected', () => {
  assert.equal(F.screens.country(candidate({ countryCode: 'GB' }),
    mandate({ countries: ['ES', 'PT'] })), false);
});

test('both spellings of one mandate give the same eligible pool', () => {
  const projects = [candidate({ id: 'P01', countryCode: 'GB' }),
    candidate({ id: 'P02', countryCode: 'ES' })];
  const byUk = preview(projects, mandate({ countries: ['UK', 'ES'] }));
  const byGb = preview(projects, mandate({ countries: ['GB', 'ES'] }));
  assert.equal(byUk.eligibleCount, 2);
  assert.equal(byGb.eligibleCount, 2);
});

/* ── 2. A lock re-admits past the screens ────────────────────────────────────── */

test('a locked project that fails a screen is still in the eligible pool', () => {
  const projects = [
    candidate({ id: 'P01' }),
    candidate({ id: 'P02', codYear: 2040 }),
  ];
  assert.equal(preview(projects).eligibleCount, 1, 'the COD window drops it unaided');
  const held = preview(projects, mandate(), { lockedIds: ['P02'] });
  assert.equal(held.eligibleCount, 2,
    'decisions.md 2A-20: a lock is a user instruction that outranks a soft screen');
  assert.deepEqual(held.pool.map((p) => p.id), ['P01', 'P02'], 'and stays in id order');
});

test('a project that is both locked and excluded stays excluded', () => {
  const projects = [candidate({ id: 'P01' }), candidate({ id: 'P02', codYear: 2040 })];
  const both = preview(projects, mandate(), { lockedIds: ['P02'], excludedIds: ['P02'] });
  assert.equal(both.eligibleCount, 1,
    'the exclusion is the more specific instruction; a stale lock must not resurrect it');
});

test('re-admitting a lock moves the pool aggregates with it', () => {
  const projects = [candidate({ id: 'P01' }), candidate({ id: 'P02', codYear: 2040, capacityMw: 250 })];
  assert.equal(preview(projects).eligibleCapacityMw, 100);
  assert.equal(preview(projects, mandate(), { lockedIds: ['P02'] }).eligibleCapacityMw, 350);
});

/* ── 3. screensToWiden ───────────────────────────────────────────────────────── */

test('the preview names the screens to widen, in the wire vocabulary', () => {
  const result = preview([candidate({ codYear: 2040 })]);
  assert.deepEqual(result.screensToWiden, ['codWindow'],
    'optimiser/screens.py spells them countries/stages/codWindow/minDscr/…');
});

test('every local screen name maps to one the server also uses', () => {
  assert.deepEqual(Object.keys(F.WIRE_SCREEN_NAMES).sort(), [...F.SCREEN_ORDER].sort());
  assert.deepEqual(F.WIRE_SCREEN_NAMES.country, 'countries');
  assert.deepEqual(F.WIRE_SCREEN_NAMES.stage, 'stages');
  assert.deepEqual(F.WIRE_SCREEN_NAMES.riskCap, 'riskScore');
  assert.deepEqual(F.WIRE_SCREEN_NAMES.currency, 'eurRevenue');
  assert.deepEqual(F.WIRE_SCREEN_NAMES.notExcluded, 'exclusions');
});

test('the worst offender comes first, and ties break on the name', () => {
  const projects = [
    candidate({ id: 'P01', codYear: 2040 }),
    candidate({ id: 'P02', codYear: 2040 }),
    candidate({ id: 'P03', countryCode: 'FI' }),
  ];
  assert.deepEqual(F.screensToWiden(F.projects(payload(projects)), mandate(), CAP, []),
    ['codWindow', 'countries'], 'two COD rejections outrank one country rejection');

  const even = [candidate({ id: 'P01', codYear: 2040 }), candidate({ id: 'P02', countryCode: 'FI' })];
  assert.deepEqual(F.screensToWiden(F.projects(payload(even)), mandate(), CAP, []),
    ['codWindow', 'countries'], 'one each, so the wire name decides');
});

test('a screen is counted once per project it rejects, independently of the others', () => {
  const doomed = [candidate({ id: 'P01', codYear: 2040, countryCode: 'FI' })];
  assert.deepEqual(F.screensToWiden(F.projects(payload(doomed)), mandate(), CAP, []),
    ['codWindow', 'countries'],
    'widening either one is a thing the user can do, so both are named');
});

test('nothing to widen when every project passes', () => {
  assert.deepEqual(preview([candidate()]).screensToWiden, []);
});

test('an exclusion is itself a screen the user can widen', () => {
  const result = preview([candidate({ id: 'P01' })], mandate(), { excludedIds: ['P01'] });
  assert.deepEqual(result.screensToWiden, ['exclusions']);
});
