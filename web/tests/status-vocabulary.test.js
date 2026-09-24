/**
 * The status vocabulary in js/controls.js is ui-contract.md §7.1, and stays that way.
 *
 * §7.1 is the reconciliation of §7.1's "coloured to indicate compliance" with §12's
 * "no colour-only status encoding" (decisions A-10): colour is allowed as an
 * *additional* channel, so every status owes a mark or a word as well. That table is
 * the contract; STATUS is its executable form.
 *
 * Two failure modes this guards, both silent:
 *
 *   - A mark replaced by an ASCII lookalike. `x` for `×`, `-` for `—`, a hollow
 *     square swapped for a letter. It renders, it just stops meaning anything, and
 *     tests/wire-contract.js already had to learn this lesson for the warning marks.
 *   - A state that exists in the vocabulary but is rendered nowhere. Then
 *     tests/greyscale.test.js passes vacuously, because it can only inspect states
 *     something actually draws. styleguide.html is where they are all drawn.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { loadPage } = require('./helpers/page.js');
const { status } = require('../js/controls.js');

const DOCS = path.resolve(__dirname, '..', '..', 'docs');

/** A section of the contract, sliced out rather than transcribed. */
function contract(from, to) {
  const ui = fs.readFileSync(path.join(DOCS, 'ui-contract.md'), 'utf8');
  const start = ui.indexOf(from);
  const end = ui.indexOf(to, start);
  assert.ok(start > 0 && end > start,
    `ui-contract.md ${from} has moved; this test reads the contract by heading`);
  return ui.slice(start, end);
}

const colourIsNeverTheOnlySignal = () => contract('### 7.1', '### 7.2');
const holdingsTableContract = () => contract('### 5.4', '### 5.5');

test('every mark §7.1 requires is the exact code point, not a lookalike', () => {
  const section = colourIsNeverTheOnlySignal();

  // [what §7.1 calls it, the code point STATUS must carry]
  const required = [
    ['a leading check on a compliant tile', status.MARK.onTarget, '✓'],
    ['an exclamation on a breaching tile', status.MARK.breach, '!'],
    ['a filled square in the lock column', status.MARK.locked, '■'],
    ['the blocking warning mark', status.MARK.blocking, '×'],
    ['the advisory warning mark', status.MARK.alert, '!'],
    ['the informational warning mark', status.MARK.info, '•'],
  ];

  for (const [what, actual, expected] of required) {
    assert.equal(actual, expected,
      `${what} must be U+${expected.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')}`);
    assert.ok(section.includes(expected),
      `ui-contract.md §7.1 no longer asks for ${what} — reconcile STATUS with the contract`);
  }

  // §7.1 lists only the states that carry a *colour*, so it names the filled square
  // and not the hollow one — an unlocked row has no tone to be the only signal. The
  // pair is pinned in §5.4's lock column instead, and both halves have to exist, or
  // a filled square is indistinguishable from a mark that never rendered.
  assert.equal(status.MARK.unlocked, '□', 'the hollow square must be U+25A1');
  assert.ok(holdingsTableContract().includes('`■` locked / `□` unlocked'),
    'ui-contract.md §5.4 pins the lock column as the filled/hollow square pair');
});

test('every word §7.1 requires is carried verbatim', () => {
  const section = colourIsNeverTheOnlySignal();
  for (const word of [status.WORD.notSelected, status.WORD.locked]) {
    assert.ok(section.includes(word), `§7.1 pins "${word}" as a row's second signal`);
  }
  // §7.1: "A trailing `!` and `below the {n}× floor` in the cell's accessible name".
  assert.equal(status.dscrFloor(1.25), 'below the 1.25× floor');
  assert.ok(section.includes('below the {n}× floor'),
    '§7.1 pins the DSCR breach phrasing; dscrFloor() renders it through format.js');
});

test('a severity maps to a tone, so a warning is never colour alone', () => {
  // api.md §5's three severities, ui-contract §3.5's two tones.
  assert.deepEqual(Object.keys(status.TONE).sort(), ['alert', 'blocking', 'info']);
  assert.equal(status.TONE.blocking, 'text-breach');
  assert.equal(status.TONE.alert, 'text-breach');
  assert.equal(status.TONE.info, 'text-muted');
  for (const severity of ['blocking', 'alert', 'info']) {
    assert.ok(status.WORD[severity].endsWith(': '),
      `a ${severity} warning owes a spoken prefix, so the mark is not the only signal`);
  }
});

test('the tile states read from the one vocabulary', () => {
  const { kpiTile } = require('../js/controls.js');
  assert.equal(kpiTile({ state: 'on-target' }).mark, status.MARK.onTarget);
  assert.equal(kpiTile({ state: 'breach' }).mark, status.MARK.breach);
  assert.equal(kpiTile({ state: 'breach' }).stateLabel, status.WORD.breach);
  assert.equal(kpiTile({ state: 'neutral' }).mark, '');
  assert.equal(kpiTile({ state: 'neutral' }).stateLabel, '',
    'a neutral tile carries neither mark nor word (§7.1)');
});

test('the style guide renders every state, so the greyscale guard is not vacuous', async () => {
  const dom = await loadPage('styleguide.html');
  const text = dom.window.document.body.textContent.replace(/\s+/g, ' ');
  dom.window.close();

  const drawn = [
    status.MARK.onTarget, status.MARK.locked, status.MARK.unlocked,
    status.MARK.blocking, status.MARK.info,
    status.WORD.onTarget, status.WORD.breach, status.WORD.locked,
    status.WORD.unlocked, status.WORD.notSelected,
    status.WORD.blocking.trim(), status.WORD.alert.trim(), status.WORD.info.trim(),
    status.dscrFloor(1.25),
  ];
  const missing = drawn.filter((s) => !text.includes(s));
  assert.deepEqual(missing, [],
    'styleguide.html is the specimen sheet for §7.1. A state drawn nowhere cannot be '
    + 'checked for surviving in greyscale, so it must appear here.');
});
