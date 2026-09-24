/**
 * No file in the working tree carries an unresolved merge marker.
 *
 * This exists because one shipped. A conflict in `mandate.html` was resolved in
 * one of its two regions, `git add` staged the file with the second still marked,
 * and every other guard passed: jsdom parses `<<<<<<< HEAD` as ordinary text, so
 * the page rendered it to the user between the heading and the panels while the
 * accessibility, contrast, nav and copy tests all walked past it.
 *
 * It is a cheap check for a failure that is invisible to every expensive one.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.resolve(__dirname, '..');
const ROOT = path.resolve(WEB, '..');

/**
 * Git's own markers, exactly as it writes them: the two side markers carry a label
 * after a space, and the separator is seven equals alone on the line. Matching a
 * looser shape would flag a setext heading underline and an ASCII rule.
 */
const MARKERS = /^(?:<{7}|>{7})[ \t]|^={7}$/;

const SKIP = new Set(['node_modules', '.git', '.venv', 'dist', '__pycache__', '.hypothesis',
  '.mypy_cache', '.pytest_cache', '.ruff_cache', 'vendor', 'fonts', 'public', 'pipeline']);
const TEXT = /\.(js|mjs|json|html|css|md|py|toml|yml|yaml)$/;

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    if (SKIP.has(entry.name)) return [];
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return walk(full);
    return TEXT.test(entry.name) ? [full] : [];
  });
}

test('no source file carries an unresolved merge marker', () => {
  const offenders = [];
  for (const file of walk(ROOT)) {
    // This file names the markers in its own prose, which is the one exemption.
    if (path.resolve(file) === path.resolve(__filename)) continue;
    const lines = fs.readFileSync(file, 'utf8').split('\n');
    lines.forEach((line, i) => {
      if (MARKERS.test(line)) offenders.push(`${path.relative(ROOT, file)}:${i + 1}: ${line.slice(0, 40)}`);
    });
  }
  assert.deepEqual(offenders, [],
    'a half-resolved conflict renders to the user as text and passes every other guard');
});

test('the check can actually see a marker', () => {
  assert.equal(MARKERS.test('<<<<<<< HEAD'), true);
  assert.equal(MARKERS.test('======='), true);
  assert.equal(MARKERS.test('>>>>>>> origin/main'), true);
  assert.equal(MARKERS.test('======= not a marker, a rule'), false, 'seven equals then text is prose');
  assert.equal(MARKERS.test('========'), false, 'and a longer rule is a rule');
  assert.equal(MARKERS.test('Heading'), false);
  assert.equal(MARKERS.test('const x = 1;'), false);
  assert.equal(MARKERS.test('  <<<<<<< indented'), false, 'git writes them at the line start');
});
