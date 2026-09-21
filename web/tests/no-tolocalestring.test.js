/**
 * js/format.js is the only place allowed to format a number (spec §14).
 *
 * This guard exists because the rule is trivially broken by one convenient
 * toLocaleString in a new page script, and the damage — a figure that reads
 * differently depending on the viewer's locale — is invisible until someone
 * prints a committee pack abroad.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.resolve(__dirname, '..');

/** Every file the browser loads that we author: page scripts and pages. */
function sourceFiles() {
  const js = fs.readdirSync(path.join(WEB, 'js'))
    .filter((f) => f.endsWith('.js'))
    .map((f) => path.join('js', f));
  const html = fs.readdirSync(WEB)
    .filter((f) => f.endsWith('.html'))
    .map((f) => f);
  return [...js, ...html];
}

test('toLocaleString appears only inside js/format.js', () => {
  const offenders = [];
  for (const rel of sourceFiles()) {
    if (rel === path.join('js', 'format.js')) continue;
    const source = fs.readFileSync(path.join(WEB, rel), 'utf8');
    source.split('\n').forEach((line, i) => {
      if (line.includes('toLocaleString')) offenders.push(`${rel}:${i + 1}`);
    });
  }
  assert.deepEqual(offenders, [],
    'these must call js/format.js instead of formatting numbers themselves');
});

test('js/format.js confines toLocaleString to its one grouping helper', () => {
  const source = fs.readFileSync(path.join(WEB, 'js', 'format.js'), 'utf8');
  // The call form, so the file's own prose about the rule does not count as breaking it.
  const calls = source.match(/\.toLocaleString\(/g) || [];
  assert.equal(calls.length, 1,
    'grouping must funnel through a single call, so the locale is pinned in one place');
  assert.match(source, /function group\([\s\S]*?toLocaleString\(LOCALE/,
    'the call must live in group() and use the pinned LOCALE constant');
});

test('no page or script hard-codes a thousands separator or a currency symbol format', () => {
  const offenders = [];
  for (const rel of sourceFiles()) {
    if (rel === path.join('js', 'format.js')) continue;
    const source = fs.readFileSync(path.join(WEB, rel), 'utf8');
    source.split('\n').forEach((line, i) => {
      // A literal grouped figure in source means a number bypassed format.js.
      if (/\d{1,3}(,\d{3})+/.test(line) && !line.trim().startsWith('*')) {
        offenders.push(`${rel}:${i + 1}: ${line.trim().slice(0, 70)}`);
      }
    });
  }
  assert.deepEqual(offenders, [], 'grouped numerals must be produced by format.js at runtime');
});
