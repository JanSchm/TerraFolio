/**
 * Spec §14 and ui-contract.md §8: no algorithm vocabulary in user-facing copy.
 *
 * tests/copy.test.js already greps the *rendered* text of each page. This one greps
 * the *source* of `web/*.html` and `web/js/`, which catches three things rendering
 * cannot: copy that lives in an attribute (`aria-label`, `placeholder`, `alt`),
 * copy inside a `<template>` — template content is not in `body.textContent`, so a
 * row rendered later is audited by nothing otherwise — and copy sitting in a page
 * script's string literals before anything has put it on screen.
 *
 * The hard part is telling copy from code. Three rules, in order:
 *
 *   1. A comment is documentation, and §8 says this vocabulary *belongs* in the
 *      repository's documentation. format.js's "Annual generation: 3,412 GWh" must
 *      not fail, and neither must an honest comment about the GA seam.
 *   2. An identifier is code. `bestFitness` as a property name is never shown to
 *      anyone; api.md §7 is explicit that the wire keeps the technical vocabulary
 *      ("the stream is not user-facing, and §14's ban applies to copy, not to the
 *      protocol"). So only string and template literals are scanned.
 *   3. A literal that is exactly a sanctioned wire name is a protocol constant, not
 *      a sentence. The whole literal must match, so `'generation'` (an SSE event
 *      name) passes while `'generation 7 of 60'` does not.
 *
 * And "generation" is what a wind farm does. Both directions are checked: the bare
 * word is excused in an electricity context, and the GA senses are flagged whatever
 * the context, because none of them can occur in the electricity sense.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const WEB = path.resolve(__dirname, '..');

/** ui-contract.md §8's banned list, plus the spellings 1D's rendered-text guard also carries. */
const BANNED = [
  'population', 'chromosome', 'genome', 'allele', 'crossover', 'mutation', 'mutate',
  'fitness', 'elite', 'elitism', 'tournament', 'convergence', 'converged',
  'solution space', 'objective function', 'genetic algorithm',
];

/**
 * "Generation" is the single most common figure in the product, in GWh. The bare
 * word is only copy-conformant when it is plainly the electricity sense.
 */
const GENERATION_IS_DOMAIN = /(annual|p50|power|net|of)\s+generation\b|generation[\s,]+(in\s+)?gwh|generation\s+(per\s+year|p\.a\.)/;

/** GA senses of "generation" that no electricity context can produce. */
const GENERATION_IS_ALGORITHM = [
  /\bgeneration\s+\d/, /\bgenerations\b/, /\b(per|each|every)\s+generation\b/,
  /\bgeneration\s*\/\s*\d/, /\bgeneration\s+counter\b/,
];

/**
 * Literals that are protocol constants rather than sentences: api.md §7 names the
 * SSE fields and §8.1 the result fields, and both keep the technical vocabulary on
 * purpose. A literal qualifies only by matching one of these in full.
 */
const WIRE_NAMES = new Set([
  'generation', 'totalGenerations', 'bestFitness', 'meanFitness', 'fitness', 'convergence',
]);

function offences(text) {
  const lower = text.toLowerCase();
  const found = BANNED.filter((term) => new RegExp(`\\b${term}\\b`).test(lower));
  if (GENERATION_IS_ALGORITHM.some((re) => re.test(lower))) found.push('generation');
  else if (/\bgeneration\b/.test(lower) && !GENERATION_IS_DOMAIN.test(lower)) found.push('generation');
  return found;
}

/* ── web/*.html: the surfaces a user actually reads ────────────────────────── */

/** Attributes that are spoken or shown. `data-*`, `id` and `class` are internal names. */
const SPOKEN = ['aria-label', 'aria-description', 'aria-valuetext', 'aria-placeholder',
  'alt', 'title', 'placeholder', 'label'];

function userFacingStrings(file) {
  const dom = new JSDOM(fs.readFileSync(path.join(WEB, file), 'utf8'));
  const d = dom.window.document;
  const out = [];
  const add = (where, value) => { if (value && value.trim()) out.push([where, value.trim()]); };

  add('<title>', d.title);

  // Every root that renders: the document, plus each <template>'s content, which
  // is a separate fragment and invisible to any walk of the document body.
  const roots = [d.body, ...[...d.querySelectorAll('template')].map((t) => t.content)];
  for (const root of roots) {
    const walker = d.createTreeWalker(root, dom.window.NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (n.parentElement && /^(SCRIPT|STYLE)$/.test(n.parentElement.tagName)) continue;
      add('text', n.nodeValue);
    }
    for (const el of root.querySelectorAll('*')) {
      for (const attr of SPOKEN) add(`[${attr}]`, el.getAttribute(attr));
    }
  }
  dom.window.close();
  return out;
}

for (const file of fs.readdirSync(WEB).filter((f) => f.endsWith('.html'))) {
  test(`${file}: no algorithm vocabulary in any string a user reads`, () => {
    const bad = [];
    for (const [where, value] of userFacingStrings(file)) {
      const found = offences(value);
      if (found.length) bad.push(`${file} ${where}: "${value.slice(0, 70)}" → ${found.join(', ')}`);
    }
    assert.deepEqual(bad, [],
      'Describe what is happening to the user\'s portfolios. ui-contract.md §4 carries a '
      + 'replacement for every instance in the mockup.');
  });
}

/* ── web/js/: string literals only, comments and identifiers excluded ──────── */

/**
 * Walks a source file once, tracking whether it is inside a comment, a string, a
 * template literal or a regex, and returns the contents of every string-ish literal.
 * A hand-rolled scanner rather than a regex because the two things that must not be
 * confused — a comment and a string — can each contain the other's delimiters.
 */
function stringLiterals(source) {
  const out = [];
  let i = 0;
  let previous = '';
  while (i < source.length) {
    const c = source[i];
    const next = source[i + 1];
    if (c === '/' && next === '/') { while (i < source.length && source[i] !== '\n') i += 1; continue; }
    if (c === '/' && next === '*') { i = source.indexOf('*/', i + 2); i = i < 0 ? source.length : i + 2; continue; }
    // A '/' that opens a regex rather than dividing: decided by what precedes it.
    if (c === '/' && /[(,=:[!&|?{};+\-*%~^]/.test(previous)) {
      i += 1;
      while (i < source.length && source[i] !== '/') { if (source[i] === '\\') i += 1; i += 1; }
      i += 1; previous = '/'; continue;
    }
    if (c === '"' || c === "'" || c === '`') {
      const quote = c;
      let value = '';
      i += 1;
      while (i < source.length && source[i] !== quote) {
        if (source[i] === '\\') { i += 2; value += ' '; continue; }
        value += source[i]; i += 1;
      }
      i += 1; previous = quote; out.push(value); continue;
    }
    if (!/\s/.test(c)) previous = c;
    i += 1;
  }
  return out;
}

function jsFiles(dir, prefix = 'js') {
  return fs.readdirSync(path.join(WEB, dir), { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? jsFiles(path.join(dir, e.name), `${prefix}/${e.name}`)
      : e.name.endsWith('.js') ? [path.join(dir, e.name)] : []);
}

for (const file of jsFiles('js')) {
  test(`${file}: no algorithm vocabulary in a string literal`, () => {
    const bad = [];
    for (const value of stringLiterals(fs.readFileSync(path.join(WEB, file), 'utf8'))) {
      if (WIRE_NAMES.has(value)) continue;
      const found = offences(value);
      if (found.length) bad.push(`${file}: "${value.slice(0, 70)}" → ${found.join(', ')}`);
    }
    assert.deepEqual(bad, [],
      'A wire field name may be a literal on its own (api.md §7); a sentence may not.');
  });
}

/* ── The scanner's own behaviour, so a silent false negative cannot hide ───── */

test('the scanner reads literals and ignores comments and identifiers', () => {
  const source = [
    '// fitness lives in the documentation, which is where §8 puts it',
    '/* and in a block comment about the population, too */',
    'var bestFitness = 1;            // an identifier is not copy',
    "var copy = 'Mandate score';",
    'var leak = "ROUND 7, generation 7 of 60";',
  ].join('\n');
  assert.deepEqual(stringLiterals(source), ['Mandate score', 'ROUND 7, generation 7 of 60']);
});

test('the domain sense of generation passes and the algorithm sense does not', () => {
  for (const ok of ['Annual generation', '3,412 GWh of generation', 'P50 generation in GWh']) {
    assert.deepEqual(offences(ok), [], `"${ok}" is what a wind farm does`);
  }
  for (const bad of ['generation 7 of 60', 'per generation', 'GENERATION 12 / 60', 'generations']) {
    assert.deepEqual(offences(bad), ['generation'], `"${bad}" is algorithm vocabulary`);
  }
});

test('a sanctioned wire name is exempt only as a whole literal', () => {
  assert.ok(WIRE_NAMES.has('bestFitness'));
  assert.deepEqual(offences('Best fitness'), ['fitness'],
    'the exemption is by exact match, so prose containing a wire name is still caught');
});
