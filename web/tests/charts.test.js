/**
 * charts.js — the geometry behind the two hand-built charts, checked at the edges
 * that a running system rarely reaches: one round, a series that never moves, a
 * cash flow that is negative throughout.
 *
 * These are pure functions precisely so that those cases can be checked without a
 * page and without a run.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const charts = require('../js/charts.js');

const at = (points, i) => points.split(' ')[i].split(',').map(Number);

function rounds(values) {
  return values.map((pair, i) => ({
    generation: i + 1, bestFitness: pair[0], meanFitness: pair[1],
  }));
}

/* ── The search screen's curves ──────────────────────────────────────────────── */

test('x is scaled to the total, so the curve grows across a fixed axis', () => {
  assert.equal(charts.x(1, 60), 0, 'the first round is the left edge');
  assert.equal(charts.x(60, 60), charts.VIEW_WIDTH, 'the last is the right');
  assert.equal(charts.x(30, 60), charts.VIEW_WIDTH * (29 / 59));
  assert.equal(charts.x(1, 1), 0, 'a single-round run does not divide by zero');
});

test('both series share one domain, so the gap between them is the story', () => {
  const out = charts.curves(rounds([[5, -2], [5, 0], [6, 2]]), 3);
  assert.equal(out.low, -2);
  assert.equal(out.high, 6);
  assert.equal(at(out.mean, 0)[1], charts.VIEW_HEIGHT - charts.INSET, 'the lowest point sits at the floor');
  assert.equal(at(out.best, 2)[1], charts.INSET, 'the highest at the ceiling');
});

test('a flat series is drawn down the middle rather than dividing by zero', () => {
  const out = charts.curves(rounds([[2, 2], [2, 2]]), 60);
  assert.equal(at(out.best, 0)[1], charts.VIEW_HEIGHT / 2);
  assert.equal(at(out.mean, 1)[1], charts.VIEW_HEIGHT / 2);
});

test('no rounds yet is an empty attribute, not a stray point at the origin', () => {
  const out = charts.curves([], 60);
  assert.equal(out.best, '');
  assert.equal(out.mean, '');
  assert.equal(out.low, null);
});

test('a score that is not a number is skipped, never drawn as zero', () => {
  const out = charts.curves([
    { generation: 1, bestFitness: 4, meanFitness: 1 },
    { generation: 2, bestFitness: null, meanFitness: 2 },
    { generation: 3, bestFitness: 5, meanFitness: 3 },
  ], 3);
  assert.equal(out.best.split(' ').length, 2, 'two points, not three');
  assert.equal(out.mean.split(' ').length, 3);
  assert.equal(out.low, 1, 'and the missing value does not drag the domain to zero');
});

test('the inset keeps a stroke at its extreme inside the box', () => {
  const out = charts.curves(rounds([[1, 0], [9, 0]]), 2);
  const highest = at(out.best, 1)[1];
  assert.ok(highest >= 0 && highest <= charts.VIEW_HEIGHT);
  assert.equal(highest, charts.INSET, 'a 2px stroke on y=0 would be half outside the viewBox');
});

/* ── The cash-flow bars ──────────────────────────────────────────────────────── */

test('the zero line sits where a negative bar is as long as an equal positive one', () => {
  const out = charts.bars([-50, 50], 2027);
  assert.equal(out.zeroPercent, 50);
  assert.equal(out.bars[0].heightPercent, 50);
  assert.equal(out.bars[1].heightPercent, 50);
});

test('the zero line moves with the asymmetry of the series', () => {
  const out = charts.bars([-30, 25], 2027);
  assert.equal(Math.round(out.zeroPercent * 10) / 10, 45.5, 'max / (max - min)');
  assert.equal(out.max, 25);
  assert.equal(out.min, -30);
});

test('a series that never goes negative puts the line on the floor', () => {
  const out = charts.bars([10, 20, 30], 2027);
  assert.equal(out.zeroPercent, 100);
  assert.equal(out.bars.every((b) => !b.negative), true);
});

test('a series that never goes positive puts the line on the ceiling', () => {
  const out = charts.bars([-10, -20], 2027);
  assert.equal(out.zeroPercent, 0);
  assert.equal(out.bars.every((b) => b.negative), true);
});

test('each bar knows its own year and sign', () => {
  const out = charts.bars([-28.93, 14.2], 2027);
  assert.equal(out.bars[0].year, 2027);
  assert.equal(out.bars[0].negative, true);
  assert.equal(out.bars[1].year, 2028);
  assert.equal(out.bars[1].negative, false);
  assert.equal(Math.round(out.total * 100) / 100, -14.73, 'the undiscounted sum, for the caption');
});

test('an all-zero series has a line and no bars, rather than a division by zero', () => {
  const out = charts.bars([0, 0, 0], 2027);
  assert.equal(out.zeroPercent, 100);
  assert.deepEqual(out.bars.map((b) => b.heightPercent), [0, 0, 0]);
});

test('ticks fall on every fifth year from the base year', () => {
  const marks = charts.ticks(30, 2027);
  assert.equal(marks.length, 30);
  assert.equal(marks[0], 2027);
  assert.equal(marks[1], null);
  assert.equal(marks[5], 2032);
  assert.equal(marks[25], 2052);
  assert.equal(marks.filter(Boolean).length, 6);
});
