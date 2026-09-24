/**
 * table.js — the backing array behind §7.4's sixteen columns.
 *
 * Kept away from the DOM on purpose: the ordering rules are where this can go
 * quietly wrong, and #12 asserts that the client and the server produce the same
 * order, which needs the comparison to be checkable on its own.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const table = require('../js/table.js');

function row(overrides) {
  return Object.assign({
    id: 'P01', name: 'Almonte Solar', country: 'Spain', countryCode: 'ES',
    technology: 'solar', stage: 'ready_to_build', capacityMw: 180, codYear: 2028,
    equityIrr: 0.124, minDscr: 1.38, selected: true, locked: false,
  }, overrides || {});
}

const of = (rows) => table.holdings(table.view(), rows);
const ids = (state) => table.resolve(state).map((r) => r.id);

/* ── Ordering ────────────────────────────────────────────────────────────────── */

test('the default sort is equity IRR, descending', () => {
  const state = of([row({ id: 'P01', equityIrr: 0.1 }), row({ id: 'P02', equityIrr: 0.2 })]);
  assert.equal(state.field, 'equityIrr');
  assert.equal(state.direction, 'descending');
  assert.deepEqual(ids(state), ['P02', 'P01']);
});

test('a null figure sorts last in BOTH directions', () => {
  const rows = [row({ id: 'P01', equityIrr: null }), row({ id: 'P02', equityIrr: 0.05 }),
    row({ id: 'P03', equityIrr: 0.2 })];
  const state = of(rows);
  assert.deepEqual(ids(state), ['P03', 'P02', 'P01']);
  state.direction = 'ascending';
  assert.deepEqual(ids(state), ['P02', 'P03', 'P01'],
    'an em dash is absent, not small: §5.4 puts it last whichever way the column points');
});

test('an unlevered project is last on Min DSCR, not first', () => {
  const state = of([row({ id: 'P01', minDscr: null }), row({ id: 'P02', minDscr: 1.2 })]);
  state.field = 'minDscr';
  state.direction = 'ascending';
  assert.deepEqual(ids(state), ['P02', 'P01'],
    'a project with no debt is not the worst-covered one in the portfolio');
});

test('ties break on the id, so the order is total and a re-sort cannot shuffle', () => {
  const rows = [row({ id: 'P03', capacityMw: 100 }), row({ id: 'P01', capacityMw: 100 }),
    row({ id: 'P02', capacityMw: 100 })];
  const state = of(rows);
  state.field = 'capacityMw';
  assert.deepEqual(ids(state), ['P01', 'P02', 'P03']);
  state.direction = 'ascending';
  assert.deepEqual(ids(state), ['P01', 'P02', 'P03'], 'and the tie-break does not flip with it');
});

test('text columns sort on the wire value the column names', () => {
  const rows = [row({ id: 'P01', technology: 'solar' }), row({ id: 'P02', technology: 'offshore_wind' }),
    row({ id: 'P03', technology: 'onshore_wind' })];
  const state = of(rows);
  state.field = 'technology';
  state.direction = 'ascending';
  assert.deepEqual(ids(state), ['P02', 'P03', 'P01'],
    '§5.4: the sort key is the holdings field, not the label the cell renders');
});

test('a name sorts as a name', () => {
  const state = of([row({ id: 'P01', name: 'Zaragoza Wind' }), row({ id: 'P02', name: 'Almonte Solar' })]);
  state.field = 'name';
  state.direction = 'ascending';
  assert.deepEqual(ids(state), ['P02', 'P01']);
});

/* ── Filtering ───────────────────────────────────────────────────────────────── */

test('the selected view hides what the optimiser rejected, and the toggle shows it', () => {
  const state = of([row({ id: 'P01' }), row({ id: 'P02', selected: false })]);
  assert.deepEqual(ids(state), ['P01']);
  assert.equal(state.total, 1, 'and the count is of this view, not of the whole run');
  state.selectedOnly = false;
  assert.deepEqual(ids(state).sort(), ['P01', 'P02']);
  assert.equal(state.total, 2);
});

test('the free-text search reads the name, the id and the country', () => {
  const rows = [row({ id: 'P01', name: 'Almonte Solar', country: 'Spain' }),
    row({ id: 'P77', name: 'Kujawy Wind', country: 'Poland' })];
  const state = of(rows);
  const find = (q) => { state.query = q; return ids(state); };
  assert.deepEqual(find('almonte'), ['P01'], 'and it is case-insensitive');
  assert.deepEqual(find('poland'), ['P77']);
  assert.deepEqual(find('p77'), ['P77']);
  assert.deepEqual(find(''), ['P01', 'P77']);
});

test('the three selects filter on the wire value, and combine', () => {
  const rows = [
    row({ id: 'P01', countryCode: 'ES', technology: 'solar', stage: 'greenfield' }),
    row({ id: 'P02', countryCode: 'ES', technology: 'onshore_wind', stage: 'greenfield' }),
    row({ id: 'P03', countryCode: 'PL', technology: 'solar', stage: 'construction' }),
  ];
  const state = of(rows);
  state.country = 'ES';
  assert.deepEqual(ids(state).sort(), ['P01', 'P02']);
  state.technology = 'solar';
  assert.deepEqual(ids(state), ['P01']);
  state.stage = 'construction';
  assert.deepEqual(ids(state), [], 'an empty result is a result, not a reset');
});

test('shown and total both report the view the user is looking at', () => {
  const rows = [row({ id: 'P01' }), row({ id: 'P02', countryCode: 'PL' }),
    row({ id: 'P03', selected: false })];
  const state = of(rows);
  state.country = 'ES';
  table.resolve(state);
  assert.equal(state.shown, 1);
  assert.equal(state.total, 2, 'two are selected; the filter lets one through');
});
