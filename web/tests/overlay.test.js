/**
 * The drawer and the export dialog hold focus, close on Esc, and give focus back.
 *
 * ui-contract.md §7.2 and §5.5 require all three. Both overlays already carried
 * `role="dialog"` and `aria-modal="true"` — which is the *claim* that focus is
 * trapped — and nothing was trapping it: a keyboard user tabbed straight out of an
 * open drawer into the page behind, and on closing landed at the top of the
 * document rather than back at the row they came from.
 *
 * There is a second hole this closes. axe skips `hidden` subtrees, so until now
 * neither overlay was audited at all — every axe run in this repository has been
 * over a page with both of them shut. The last test here opens each one and runs
 * axe again.
 *
 * jsdom does not move focus on Tab, which is fine: a trap has to intercept Tab and
 * move focus itself in a real browser too, so what is asserted here is what ships.
 * `nextFocus` is checked directly as well, because it is the part that decides.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const axe = require('axe-core');
const { JSDOM } = require('jsdom');
const { loadPage } = require('./helpers/page.js');
const { overlay } = require('../js/controls.js');
const { ROWS } = require('./helpers/holdings.js');

const settled = (dom) => new Promise((r) => dom.window.setTimeout(r, 40));

function press(window, element, key, shiftKey = false) {
  const event = new window.KeyboardEvent('keydown', { key, shiftKey, bubbles: true, cancelable: true });
  element.dispatchEvent(event);
  return event;
}

/* ── nextFocus, with no page and no focus semantics involved ────────────────── */

test('the trap wraps at both ends and ignores what cannot be focused', () => {
  const dom = new JSDOM(`<div id="panel">
    <button data-k="close">Close</button>
    <button data-k="off" disabled>Disabled</button>
    <div hidden><button data-k="hidden">Hidden</button></div>
    <a href="#x" data-k="link">Link</a>
    <input data-k="field">
    <button data-k="last" aria-hidden="true">Decorative</button>
  </div>`);
  const panel = dom.window.document.getElementById('panel');
  const o = overlay({ name: 'test' });
  o.attach(panel, null);

  const keys = o.focusables().map((e) => e.dataset.k);
  assert.deepEqual(keys, ['close', 'link', 'field'],
    'disabled, hidden and aria-hidden controls are not in the tab order');

  const [first, , last] = o.focusables();
  assert.equal(o.nextFocus(last, false), first, 'Tab off the end wraps to the start');
  assert.equal(o.nextFocus(first, true), last, 'Shift+Tab off the start wraps to the end');
  assert.equal(o.nextFocus(null, false), first, 'focus outside the panel comes back to the start');
  assert.equal(o.nextFocus(null, true), last, 'and backwards, to the end');
  dom.window.close();
});

test('an empty panel keeps focus on itself rather than losing it to the page', () => {
  const dom = new JSDOM('<div id="panel" tabindex="-1"></div>');
  const panel = dom.window.document.getElementById('panel');
  const o = overlay({ name: 'test' });
  o.attach(panel, null);
  assert.equal(o.nextFocus(null, false), panel);
  dom.window.close();
});

/* ── Both overlays, on the real page ────────────────────────────────────────── */

const OVERLAYS = [
  { what: 'the export dialog', trigger: '[data-action="export"]', scrim: '[data-region="export-scrim"]' },
  { what: 'the project drawer', trigger: '[data-region="holdings"] [data-action="open-drawer"]', scrim: '[data-region="drawer-scrim"]' },
];

/** The drawer is opened from a row, and rows are rendered by #11 — so supply some. */
async function portfolio() {
  const dom = await loadPage('portfolio.html');
  const table = dom.window.document.querySelector('[data-region="holdings"]').closest('table');
  dom.window.Alpine.$data(table).rows = ROWS;
  await settled(dom);
  return dom;
}

for (const { what, trigger, scrim } of OVERLAYS) {
  test(`${what} opens, and focus moves inside it`, async () => {
    const dom = await portfolio();
    const d = dom.window.document;
    assert.ok(d.querySelector(scrim).hasAttribute('hidden'), 'it starts closed');

    d.querySelector(trigger).click();
    await settled(dom);

    assert.equal(d.querySelector(scrim).hasAttribute('hidden'), false);
    assert.ok(d.activeElement.closest('[role="dialog"]'),
      'focus must land inside the dialog, not stay on the page behind it');
    dom.window.close();
  });

  test(`${what} holds Tab and Shift+Tab inside itself`, async () => {
    const dom = await portfolio();
    const d = dom.window.document;
    d.querySelector(trigger).click();
    await settled(dom);

    const panel = d.activeElement.closest('[role="dialog"]');
    const inside = [...panel.querySelectorAll('a[href], button:not([disabled]), input:not([disabled])')];
    assert.ok(inside.length > 1, 'a trap over one control proves nothing');

    inside[inside.length - 1].focus();
    const forward = press(dom.window, d.activeElement, 'Tab');
    assert.equal(forward.defaultPrevented, true, 'the browser must not do its own Tab');
    assert.equal(d.activeElement, inside[0], 'Tab off the last control wraps to the first');

    const back = press(dom.window, d.activeElement, 'Tab', true);
    assert.equal(back.defaultPrevented, true);
    assert.equal(d.activeElement, inside[inside.length - 1], 'and Shift+Tab wraps the other way');
    dom.window.close();
  });

  test(`${what} closes on Escape and hands focus back to what opened it`, async () => {
    const dom = await portfolio();
    const d = dom.window.document;
    const opener = d.querySelector(trigger);
    opener.focus();
    opener.click();
    await settled(dom);

    press(dom.window, d.activeElement, 'Escape');
    await settled(dom);

    assert.ok(d.querySelector(scrim).hasAttribute('hidden'), 'Escape closes it (§5.5)');
    assert.equal(d.activeElement, opener,
      'focus returns to the control that opened it, not to the top of the document');
    dom.window.close();
  });

  test(`${what} closes on its backdrop`, async () => {
    const dom = await portfolio();
    const d = dom.window.document;
    d.querySelector(trigger).click();
    await settled(dom);
    d.querySelector(scrim).dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
    await settled(dom);
    assert.ok(d.querySelector(scrim).hasAttribute('hidden'), '§5.5: dismissible by the backdrop');
    dom.window.close();
  });
}

test('the export trigger reports what it opens and whether it is open', async () => {
  const dom = await portfolio();
  const d = dom.window.document;
  const trigger = d.querySelector('[data-action="export"]');
  assert.equal(trigger.getAttribute('aria-haspopup'), 'dialog');
  assert.equal(trigger.getAttribute('aria-expanded'), 'false');

  trigger.click();
  await settled(dom);
  assert.equal(trigger.getAttribute('aria-expanded'), 'true');

  d.querySelector('[data-action="close-export"]').click();
  await settled(dom);
  assert.equal(trigger.getAttribute('aria-expanded'), 'false', 'and it is put back on close');
  dom.window.close();
});

test('a row opener says it opens a dialog without claiming an expanded state', async () => {
  const dom = await portfolio();
  const openers = [...dom.window.document.querySelectorAll('[data-region="holdings"] [data-action="open-drawer"]')];
  assert.equal(openers.length, 3);
  for (const opener of openers) {
    assert.equal(opener.getAttribute('aria-haspopup'), 'dialog');
    assert.equal(opener.hasAttribute('aria-expanded'), false,
      'three hundred rows each announcing a collapsed state is noise, not information');
  }
  dom.window.close();
});

/* ── axe, over DOM no axe run in this repository has ever reached ───────────── */

/** Matches tests/a11y.test.js: jsdom has no layout, so these four cannot run. */
const NEEDS_LAYOUT = ['color-contrast', 'color-contrast-enhanced', 'target-size',
  'scrollable-region-focusable'];

for (const { what, trigger } of OVERLAYS) {
  test(`axe finds no violation in ${what} once it is open`, async () => {
    const dom = await portfolio();
    dom.window.document.querySelector(trigger).click();
    await settled(dom);

    dom.window.eval(axe.source);
    const results = await dom.window.axe.run(dom.window.document, {
      resultTypes: ['violations'],
      rules: Object.fromEntries(NEEDS_LAYOUT.map((id) => [id, { enabled: false }])),
    });
    dom.window.close();

    assert.equal(results.violations.length, 0, results.violations.map((v) =>
      `\n  [${v.impact}] ${v.id}: ${v.help}\n    ${v.nodes.slice(0, 3)
        .map((n) => n.html.replace(/\s+/g, ' ').slice(0, 140)).join('\n    ')}`).join(''));
  });
}
