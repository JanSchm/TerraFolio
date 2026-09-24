/**
 * §12's "full keyboard operation", checked as a traversal rather than per control.
 *
 * tests/keyboard.test.js (issue #5) proves each control is a native element with an
 * accessible name and no positive tabindex, which is what makes a control reachable.
 * This asks the other question: walking the page in tab order, is every control
 * actually *on* the walk? A control can satisfy every per-element rule and still be
 * stranded — inside a `hidden` container, behind a `<tr>` that only responds to
 * clicks, or after a skip link that points at nothing.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { PAGES, loadPage } = require('./helpers/page.js');
const { ROWS } = require('./helpers/holdings.js');

const TABBABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), '
  + 'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** In document order, which is tab order once nothing carries a positive tabindex. */
function tabOrder(document) {
  return [...document.querySelectorAll(TABBABLE)]
    .filter((el) => !el.closest('[hidden]') && el.getAttribute('aria-hidden') !== 'true');
}

for (const page of PAGES) {
  test(`${page}: every control the page declares is on the tab walk`, async () => {
    const dom = await loadPage(page);
    const d = dom.window.document;

    const table = d.querySelector('[data-region="holdings"]');
    if (table) {
      dom.window.Alpine.$data(table.closest('table')).rows = ROWS;
      await new Promise((r) => dom.window.setTimeout(r, 40));
    }

    const reachable = new Set(tabOrder(d));
    assert.ok(reachable.size > 0, 'the walk found nothing');

    // Anything the page marks as doing something has to be on the walk.
    const stranded = [...d.querySelectorAll('[data-action], [data-sort], [data-filter]')]
      .filter((el) => !el.closest('[hidden]') && !reachable.has(el)
        && !reachable.has(el.querySelector(TABBABLE)))
      .map((el) => `<${el.tagName.toLowerCase()} ${[...el.attributes]
        .filter((a) => a.name.startsWith('data-')).map((a) => `${a.name}="${a.value}"`).join(' ')}>`);

    dom.window.close();
    assert.deepEqual(stranded, [],
      'these do something but no keyboard can reach them. A <tr> or <div> with a click '
      + 'handler is the usual cause; use a real button.');
  });

  test(`${page}: the skip link comes first and points somewhere real`, async () => {
    const dom = await loadPage(page);
    const d = dom.window.document;
    const [first] = tabOrder(d);

    assert.equal(first.getAttribute('href'), '#main',
      'the first stop on the walk should let a keyboard user past the nav');
    assert.ok(d.querySelector('#main'), 'and #main has to exist');
    assert.equal(d.querySelector('#main').tagName, 'MAIN');
    assert.ok(first.className.includes('sr-only'), 'invisible until it is focused');
    assert.ok(first.className.includes('focus:not-sr-only'), 'and visible once it is');
    dom.window.close();
  });
}

test('portfolio.html: a holdings row is operable without a pointer', async () => {
  const dom = await loadPage('portfolio.html');
  const d = dom.window.document;
  dom.window.Alpine.$data(d.querySelector('[data-region="holdings"]').closest('table')).rows = ROWS;
  await new Promise((r) => dom.window.setTimeout(r, 40));

  const reachable = new Set(tabOrder(d));
  for (const row of d.querySelectorAll('[data-region="holdings"] tr')) {
    const opener = row.querySelector('[data-action="open-drawer"]');
    assert.ok(opener && reachable.has(opener),
      'every row needs a focusable way into its project sheet (§5.5)');
  }

  // And the sixteen headers, whose sort is a button rather than a click on the <th>.
  for (const th of d.querySelectorAll('thead th[data-column]')) {
    assert.ok(reachable.has(th.querySelector('button')), `${th.dataset.column} must be sortable by keyboard`);
  }
  dom.window.close();
});
