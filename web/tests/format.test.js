/**
 * One test per exported function of js/format.js, each covering the em-dash case
 * for an undefined value, because that is the invariant most easily lost at a
 * hand-off (epic §5: undefined IRR is NaN -> null -> em dash, never 0).
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fmt = require('../js/format.js');

const DASH = '—';
const TIMES = '×';
const DOT = '·';

/** Every value that must render as an em dash, everywhere. */
const UNDEFINED_VALUES = [null, undefined, NaN, Infinity, -Infinity, 'x', {}];

/** Asserts a formatter dashes every undefined form rather than inventing a zero. */
function assertDashesUndefined(fn, name) {
  for (const value of UNDEFINED_VALUES) {
    assert.equal(fn(value), DASH, `${name}(${String(value)}) must be an em dash`);
  }
  assert.notEqual(fn(null), '0', `${name}(null) must never be zero`);
}

test('constants are the exact code points the spec names', () => {
  assert.equal(fmt.DASH, '—', 'em dash U+2014');
  assert.equal(fmt.SEPARATOR, '·', 'middle dot U+00B7');
  assert.equal(fmt.TIMES, '×', 'multiplication sign U+00D7, not the letter x');
});

test('defined() accepts real numbers and rejects every undefined form', () => {
  assert.equal(fmt.defined(0), true, 'zero is a real value');
  assert.equal(fmt.defined(-1.5), true);
  for (const value of UNDEFINED_VALUES) {
    assert.equal(fmt.defined(value), false, `${String(value)} is not defined`);
  }
});

test('eurM — euros in millions, thousands separated, no decimals', () => {
  assert.equal(fmt.eurM(1200), '€1,200m');
  assert.equal(fmt.eurM(1154.4), '€1,154m', 'rounds to whole millions');
  assert.equal(fmt.eurM(0), '€0m', 'zero is a figure, not an absence');
  assert.equal(fmt.eurM(-320), '-€320m'.replace('-€', '€-'), 'negative keeps the sign');
  assert.equal(fmt.eurM(10524), '€10,524m');
  assertDashesUndefined(fmt.eurM, 'eurM');
});

test('eurMwh — LCOE in whole euros per MWh', () => {
  assert.equal(fmt.eurMwh(41), '€41/MWh');
  assert.equal(fmt.eurMwh(41.6), '€42/MWh');
  assert.equal(fmt.eurMwh(1200), '€1,200/MWh');
  assertDashesUndefined(fmt.eurMwh, 'eurMwh');
});

test('mw — capacity in whole megawatts', () => {
  assert.equal(fmt.mw(1540), '1,540 MW');
  assert.equal(fmt.mw(21514), '21,514 MW');
  assert.equal(fmt.mw(0), '0 MW');
  assertDashesUndefined(fmt.mw, 'mw');
});

test('gwh — annual generation in whole gigawatt hours', () => {
  assert.equal(fmt.gwh(3412), '3,412 GWh');
  assert.equal(fmt.gwh(3411.7), '3,412 GWh');
  assertDashesUndefined(fmt.gwh, 'gwh');
});

test('irr — one decimal, taken as a fraction, em dash when undefined', () => {
  assert.equal(fmt.irr(0.124), '12.4%');
  assert.equal(fmt.irr(0.1), '10.0%', 'always one decimal, never bare 10%');
  assert.equal(fmt.irr(-0.031), '-3.1%', 'a negative IRR is a real result');
  assert.equal(fmt.irr(0), '0.0%', 'a zero IRR is not an undefined IRR');
  // The case epic §5 singles out: no IRR at all.
  assert.equal(fmt.irr(NaN), DASH, 'NaN is how an undefined IRR arrives');
  assertDashesUndefined(fmt.irr, 'irr');
});

test('percent — whole-percent share from a fraction', () => {
  assert.equal(fmt.percent(0.68), '68%');
  assert.equal(fmt.percent(0.685), '69%', 'rounds');
  assert.equal(fmt.percent(1), '100%');
  assertDashesUndefined(fmt.percent, 'percent');
});

test('percent1 — one-decimal percent, for capacity factor', () => {
  assert.equal(fmt.percent1(0.246), '24.6%');
  assert.equal(fmt.percent1(0.4), '40.0%');
  assertDashesUndefined(fmt.percent1, 'percent1');
});

test('dscr — two decimals with a multiplication sign', () => {
  assert.equal(fmt.dscr(1.38), '1.38' + TIMES);
  assert.equal(fmt.dscr(1.4), '1.40' + TIMES, 'pads to two decimals');
  assert.equal(fmt.dscr(3.2), '3.20' + TIMES);
  assert.ok(!fmt.dscr(1.38).includes('x'), 'never the letter x');
  assertDashesUndefined(fmt.dscr, 'dscr');
});

test('moic — two decimals with a multiplication sign', () => {
  assert.equal(fmt.moic(1.94), '1.94' + TIMES);
  assert.equal(fmt.moic(2), '2.00' + TIMES);
  assertDashesUndefined(fmt.moic, 'moic');
});

test('count — a plain grouped integer', () => {
  assert.equal(fmt.count(48), '48');
  assert.equal(fmt.count(1200), '1,200');
  assert.equal(fmt.count(0), '0');
  assertDashesUndefined(fmt.count, 'count');
});

test('year — never grouped, because 2,031 is not a year', () => {
  assert.equal(fmt.year(2031), '2031');
  assert.equal(fmt.year(2027), '2027');
  assert.ok(!fmt.year(2031).includes(','), 'a year carries no thousands separator');
  assertDashesUndefined(fmt.year, 'year');
});

test('score — development risk to one decimal', () => {
  assert.equal(fmt.score(3.2), '3.2');
  assert.equal(fmt.score(5), '5.0');
  assertDashesUndefined(fmt.score, 'score');
});

test('mandateScore is three decimals, because that is where a run moves', () => {
  assert.equal(fmt.mandateScore(5.018856), '5.019');
  assert.equal(fmt.mandateScore(-2.216503), '-2.217');
  assert.equal(fmt.mandateScore(0), '0.000', 'a score of zero is a score, not a blank');
  assert.equal(fmt.mandateScore(1234.5), '1,234.500', 'grouped like every other figure');
  for (const bad of [null, undefined, NaN, Infinity, -Infinity, 'x', {}]) {
    assert.equal(fmt.mandateScore(bad), fmt.DASH);
  }
});

test('join — middle-dot separator, dropping absent parts', () => {
  assert.equal(fmt.join('a', 'b'), `a ${DOT} b`);
  assert.equal(fmt.join(['a', 'b', 'c']), `a ${DOT} b ${DOT} c`, 'accepts an array');
  assert.equal(fmt.join('a', null, 'b'), `a ${DOT} b`, 'drops null parts');
  assert.equal(fmt.join('a', '', undefined, 'b'), `a ${DOT} b`);
  assert.equal(fmt.join('only'), 'only', 'a single part gets no separator');
  assert.equal(fmt.join(), '');
  // The em dash is a value, not an absence, so join must keep it.
  assert.equal(fmt.join(DASH, 'x'), `${DASH} ${DOT} x`);
});

test('grouping is pinned to en-GB regardless of the ambient locale', () => {
  // A German locale would render 1.200 and 12,4% — a committee pack must not move.
  const previous = process.env.LANG;
  process.env.LANG = 'de_DE.UTF-8';
  try {
    assert.equal(fmt.eurM(1200), '€1,200m', 'comma groups, full stop decimals');
    assert.equal(fmt.irr(0.124), '12.4%');
  } finally {
    if (previous === undefined) delete process.env.LANG; else process.env.LANG = previous;
  }
});

test('every exported function is covered by a test in this file', () => {
  const self = require('node:fs').readFileSync(__filename, 'utf8');
  const functions = Object.keys(fmt).filter((k) => typeof fmt[k] === 'function');
  for (const name of functions) {
    assert.ok(
      new RegExp(`fmt\\.${name}\\(`).test(self),
      `js/format.js exports ${name}() but no test in this file calls it`,
    );
  }
  assert.ok(functions.length >= 14, `expected the full format surface, saw ${functions.length}`);
});
