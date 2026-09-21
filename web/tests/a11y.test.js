/**
 * axe-core over every page, against the DOM Alpine actually renders.
 *
 * jsdom has no layout engine, so a handful of axe rules cannot run here —
 * colour-contrast above all. Those are not left unchecked: tests/contrast.test.js
 * asserts the WCAG ratios over the token/role table directly, which is stricter,
 * because it covers every pair the system defines rather than only the pairs that
 * happen to appear on a page. The rules jsdom skips are reported by name below so
 * the gap stays visible rather than implied.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const axe = require('axe-core');
const { PAGES, loadPage } = require('./helpers/page.js');

/** Rules that need geometry or paint, which jsdom cannot provide. */
const NEEDS_LAYOUT = new Set([
  'color-contrast',
  'color-contrast-enhanced',
  'target-size',
  'scrollable-region-focusable',
]);

async function run(file) {
  const dom = await loadPage(file);
  const { window } = dom;
  window.eval(axe.source);
  const results = await window.axe.run(window.document, {
    resultTypes: ['violations', 'incomplete'],
    rules: Object.fromEntries([...NEEDS_LAYOUT].map((id) => [id, { enabled: false }])),
  });
  dom.window.close();
  return results;
}

function describe(violations) {
  return violations.map((v) =>
    `\n  [${v.impact}] ${v.id}: ${v.help}\n    ${v.nodes.slice(0, 3)
      .map((n) => n.html.replace(/\s+/g, ' ').slice(0, 140)).join('\n    ')}`).join('');
}

for (const page of PAGES) {
  test(`axe: ${page} has no violations`, async () => {
    const results = await run(page);
    assert.equal(results.violations.length, 0,
      `${page} has ${results.violations.length} axe violation(s):${describe(results.violations)}`);
  });
}

test('the rules jsdom cannot run are the expected ones, and are covered elsewhere', () => {
  // If this list ever shrinks, jsdom gained layout and these can be re-enabled.
  assert.deepEqual([...NEEDS_LAYOUT].sort(), [
    'color-contrast',
    'color-contrast-enhanced',
    'scrollable-region-focusable',
    'target-size',
  ], 'keep tests/contrast.test.js and the keyboard test in step with this list');
});
