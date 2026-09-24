/**
 * §12 asks for "visible focus". This measures it rather than asserting it.
 *
 * src/input.css draws one ring for the whole product — 2px `accent-700` at 2px
 * offset — and suppresses plain `:focus` so a mouse click never rings. An offset
 * ring is drawn *outside* its control, so what it has to stand out against is not
 * the control's own fill but whatever is behind the control. That distinction is
 * the whole question here: `accent-700` on `accent-700` is 1.00:1, and a ring on
 * the RUN OPTIMISATION button would be invisible if it were drawn inside it.
 *
 * Two controls draw their own ring instead, and both have a reason recorded in
 * A-19: a transparent input cannot show one, so the thing it covers shows it. The
 * segmented control's is inset and uncoloured, which looks like an oversight and is
 * not — it inherits `currentColor`, which inverts with the segment, and an
 * `accent-700` ring inset on an `accent-700` fill would be the 1.00:1 case above.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { PAGES, loadPage } = require('./helpers/page.js');
const { ratio, inherited, parse, over, C } = require('./helpers/contrast.js');
const { ROWS } = require('./helpers/holdings.js');

const WEB = path.resolve(__dirname, '..');
/** WCAG 1.4.11: a focus indicator is a non-text contrast, so 3:1. */
const NEED = 3;

test('the product draws one ring, and never removes it', () => {
  const css = fs.readFileSync(path.join(WEB, 'src', 'input.css'), 'utf8');
  assert.match(css, /:focus\s*\{\s*outline:\s*none;/, 'plain :focus is suppressed for mouse users');
  assert.match(css, /:focus-visible\s*\{[\s\S]*?outline-color:\s*theme\('colors\.accent\.700'\)/,
    'and :focus-visible draws accent-700 — the ring the audit below measures');
  assert.equal(/:focus-visible[\s\S]{0,120}outline:\s*none/.test(css), false,
    '§7.2: the ring is never removed');
});

test('the ring stands out against every ground it is drawn on', async () => {
  const ring = C.accent[700];
  const grounds = new Map();

  let examined = 0;
  for (const page of PAGES) {
    const dom = await loadPage(page);
    const d = dom.window.document;

    // Widen the walk to the states a page can reach: a control inside a locked row
    // or an open drawer is drawn on a different ground from one on the page.
    const table = d.querySelector('[data-region="holdings"]');
    if (table) {
      const data = dom.window.Alpine.$data(table.closest('table'));
      data.rows = ROWS;
      await new Promise((r) => dom.window.setTimeout(r, 40));
    }
    for (const trigger of ['[data-action="export"]', '[data-region="holdings"] [data-action="open-drawer"]']) {
      const el = d.querySelector(trigger);
      if (el) { el.click(); await new Promise((r) => dom.window.setTimeout(r, 40)); }
    }

    for (const el of d.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])')) {
      if (el.closest('[hidden]')) continue;
      examined += 1;
      /* Offset 2px: the ring sits outside the control, so the ground is whatever
         is behind it — not the control's own fill. */
      const behind = el.parentElement
        ? inherited(el.parentElement, 'bg', d)
        : null;
      const token = behind || { name: 'bg', parsed: parse(C.bg) };
      const ground = over(token.parsed, C.bg);
      if (!grounds.has(ground)) grounds.set(ground, { name: token.name, page });
    }
    dom.window.close();
  }

  /* One ground is the correct answer, not a broken walk: `.panel` and `.tile` set
     no background of their own, so a panel is transparent over the page and every
     offset ring lands on `bg`. Guard on the controls examined instead. */
  assert.ok(examined >= 40, `only ${examined} controls examined; the walk is not working`);
  const dim = [];
  for (const [ground, { name, page }] of grounds) {
    const r = ratio(ring, ground);
    if (r < NEED) dim.push(`accent-700 on ${name} (${ground}, first seen on ${page}) is ${r.toFixed(2)}:1`);
  }
  assert.deepEqual(dim, [], `a focus ring must reach ${NEED}:1 against what it is drawn on`);
});

test('a control that cannot show its own ring has one drawn for it', async () => {
  // A-19: the split bar's slider is opacity-0, so the visible bar takes the ring.
  for (const page of ['mandate.html', 'styleguide.html']) {
    const dom = await loadPage(page);
    const bar = [...dom.window.document.querySelectorAll('div')]
      .find((el) => el.className.includes('has-[:focus-visible]:outline-accent-700'));
    dom.window.close();
    assert.ok(bar, `${page}: the split bar must draw the ring its transparent input cannot`);
  }
});

test('the segmented ring inverts with its segment, which is why it is uncoloured', async () => {
  const dom = await loadPage('mandate.html');
  const label = [...dom.window.document.querySelectorAll('label')]
    .find((el) => el.className.includes('-outline-offset-2'));
  dom.window.close();
  assert.ok(label, 'the segmented control draws an inset ring');
  assert.equal(/outline-accent|outline-\[/.test(label.className), false,
    'it must stay currentColor: an accent-700 ring inset on an accent-700 fill is 1.00:1');

  // currentColor is `bg` on the checked segment and `text` elsewhere. Both are legible.
  assert.ok(label.className.includes('has-[:checked]:text-bg'));
  assert.ok(ratio(C.bg, C.accent[700]) >= NEED,
    'the checked segment: bg-on-accent-700 must clear the boundary floor');
  assert.ok(ratio(C.text, C.bg) >= NEED, 'and an unchecked one: text on the page');
});
