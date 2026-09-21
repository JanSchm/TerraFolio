/**
 * Epic §5 and §12: no status may be carried by colour alone.
 *
 * This is the invariant most easily reintroduced — adding one tile, or swapping a
 * marker for an icon, is enough. So rather than trusting review, every element that
 * expresses a state is checked for a second, non-colour signal: an aria state, a
 * mark, or text.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { PAGES, loadPage } = require('./helpers/page.js');

/** Text a sighted user can see that is not a colour: a mark, a word, a symbol. */
function visibleSignal(el) {
  return el.textContent.replace(/\s+/g, '').length > 0;
}

for (const page of PAGES) {
  test(`${page}: every toggle exposes its state to assistive technology`, async () => {
    const dom = await loadPage(page);
    const d = dom.window.document;

    for (const sw of d.querySelectorAll('[role="switch"]')) {
      const checked = sw.getAttribute('aria-checked');
      assert.ok(checked === 'true' || checked === 'false',
        `a switch must carry aria-checked, got ${JSON.stringify(checked)}`);
    }

    for (const chip of d.querySelectorAll('.chip')) {
      const pressed = chip.getAttribute('aria-pressed');
      assert.ok(pressed === 'true' || pressed === 'false',
        `a chip must carry aria-pressed, got ${JSON.stringify(pressed)}`);
      assert.ok(visibleSignal(chip),
        'a chip must show a mark or a label, not only a fill colour');
    }

    dom.window.close();
  });

  test(`${page}: chips repeat their pressed state without relying on the fill`, async () => {
    const dom = await loadPage(page);
    const d = dom.window.document;
    const chips = [...d.querySelectorAll('.chip')];

    for (const chip of chips) {
      const pressed = chip.getAttribute('aria-pressed') === 'true';
      // U+25A0 filled / U+25A1 hollow square: the same information as the fill,
      // readable in greyscale and by anyone who cannot distinguish the two blues.
      const mark = chip.textContent.includes(pressed ? '\u25A0' : '\u25A1');
      assert.ok(mark,
        `chip "${chip.textContent.trim().slice(0, 24)}" must show ${pressed ? 'a filled' : 'a hollow'} square`);
    }

    dom.window.close();
  });

  test(`${page}: no element states a status in colour words alone`, async () => {
    const dom = await loadPage(page);
    const d = dom.window.document;
    const body = d.body.textContent;
    for (const phrase of ['shown in red', 'in green', 'the red ', 'coloured red']) {
      assert.ok(!body.toLowerCase().includes(phrase),
        `copy must not refer to a colour as the signal (found "${phrase}")`);
    }
    dom.window.close();
  });
}
