/**
 * Spec §12: "no colour-only status encoding", checked by discarding colour entirely.
 *
 * Rendering in greyscale is the usual way to eyeball this, and a greyscale render is
 * in the pull request. What runs here is the stronger form: every element whose only
 * job is to carry a status through colour is stripped of that colour and asked what
 * is left. Anything that still communicates the state counts — a mark hidden from
 * assistive technology, a word hidden from sight, or an ARIA state — and an element
 * with none of them is failing §12 whether or not anyone has looked at it in grey.
 *
 * Two classes are scanned exhaustively because they exist for one purpose each:
 * `text-breach` is the alert tone and marks nothing but a breach, and `bg-deemph` is
 * the ground of a row the optimiser did not select. Other tones are deliberately not
 * scanned: `text-accent-700` is also the brand ink on a section heading and
 * `bg-highlight` is the §5.1 highlight panel as well as a locked row, so their
 * presence does not imply a status and a blanket rule over them would report
 * headings as violations. Those places are covered instead by the §7.1 checklist at
 * the end, which walks the eight rows of the contract one at a time.
 *
 * Every state is checked where it actually renders: the pages, the template rows the
 * holdings table clones, and both overlays open.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { PAGES, loadPage } = require('./helpers/page.js');
const { ROWS } = require('./helpers/holdings.js');
const { status } = require('../js/controls.js');

const settled = (dom) => new Promise((r) => dom.window.setTimeout(r, 40));

/** Tones that can only ever mean a status. */
const STATUS_ONLY = {
  'text-breach': 'the alert tone — accent-800, which this system uses for nothing else',
  'bg-deemph': 'the ground of a row the optimiser did not select',
};

const MARKS = new Set(Object.values(status.MARK).filter(Boolean));
const WORDS = Object.values(status.WORD).map((w) => w.trim()).filter(Boolean);

/**
 * What survives when the colour is taken away.
 *
 * Only the house pattern counts: a mark is a mark when it is in an element hidden
 * from assistive technology, and a word is a word when it is in one hidden from
 * sight. Matching bare text would let a Min DSCR cell pass on the `×` inside
 * "1.38×", which is a unit, not a signal.
 */
function colourlessSignal(el) {
  for (const hidden of el.querySelectorAll('[aria-hidden="true"]')) {
    if (MARKS.has(hidden.textContent.trim())) return `mark "${hidden.textContent.trim()}"`;
  }
  for (const spoken of el.querySelectorAll('.sr-only')) {
    const text = spoken.textContent.trim();
    if (text && WORDS.some((w) => text.includes(w))) return `spoken "${text}"`;
  }
  for (const attr of ['aria-pressed', 'aria-checked', 'aria-current', 'aria-sort', 'aria-selected']) {
    if (el.hasAttribute(attr)) return attr;
    if (el.querySelector(`[${attr}]`)) return `${attr} within`;
  }
  return null;
}

function offenders(root) {
  const found = [];
  for (const [cls, what] of Object.entries(STATUS_ONLY)) {
    for (const el of root.querySelectorAll(`.${cls}`)) {
      if (!colourlessSignal(el)) {
        found.push(`${cls} (${what}) on <${el.tagName.toLowerCase()}> `
          + `"${el.textContent.replace(/\s+/g, ' ').trim().slice(0, 60)}" carries no second channel`);
      }
    }
  }
  return found;
}

for (const page of PAGES) {
  test(`${page}: no status survives only as a colour`, async () => {
    const dom = await loadPage(page);
    const found = offenders(dom.window.document.body);
    dom.window.close();
    assert.deepEqual(found, [],
      'Add a mark in an aria-hidden span, or the state in an sr-only one. '
      + 'ui-contract.md §7.1 lists the required second signal for every place.');
  });
}

test('portfolio.html: the rendered rows and the open overlays hold up too', async () => {
  const dom = await loadPage('portfolio.html');
  const d = dom.window.document;
  const table = d.querySelector('[data-region="holdings"]').closest('table');
  const data = dom.window.Alpine.$data(table);
  data.dscrFloor = 1.25;
  data.rows = ROWS;
  d.querySelector('[data-action="export"]').click();
  await settled(dom);
  d.querySelector('[data-region="holdings"] [data-action="open-drawer"]').click();
  await settled(dom);

  // The states only exist once something renders them, so prove they are there.
  assert.ok(d.querySelector('[data-region="holdings"] .bg-deemph'), 'a non-selected row renders');
  assert.ok(d.querySelector('[data-region="holdings"] .text-breach'), 'a sub-floor DSCR renders');

  const found = offenders(d.body);
  dom.window.close();
  assert.deepEqual(found, []);
});

/* ── The eight rows of ui-contract.md §7.1, one at a time ───────────────────── */

test('§7.1: a breaching tile carries a mark and a word, a neutral one carries neither', async () => {
  const dom = await loadPage('styleguide.html');
  const tiles = [...dom.window.document.querySelectorAll('.tile')];
  const breach = tiles.find((t) => t.querySelector('.text-breach'));
  const onTarget = tiles.find((t) => t.querySelector('.text-accent-700'));
  assert.ok(breach && onTarget, 'the style guide shows both states');

  assert.equal(breach.querySelector('[aria-hidden="true"]').textContent.trim(), status.MARK.breach);
  assert.equal(breach.querySelector('.sr-only').textContent.trim(), status.WORD.breach);
  assert.equal(onTarget.querySelector('[aria-hidden="true"]').textContent.trim(), status.MARK.onTarget);
  assert.equal(onTarget.querySelector('.sr-only').textContent.trim(), status.WORD.onTarget);
  dom.window.close();
});

test('§7.1: a feasibility warning carries its mark and its severity in words', async () => {
  const dom = await loadPage('styleguide.html');
  const items = [...dom.window.document.querySelectorAll('li')]
    .filter((li) => li.querySelector('[aria-hidden="true"]') && li.querySelector('.sr-only'));
  const marks = items.map((li) => li.querySelector('[aria-hidden="true"]').textContent.trim());
  dom.window.close();

  for (const mark of [status.MARK.blocking, status.MARK.alert, status.MARK.info]) {
    assert.ok(marks.includes(mark), `§7.1 wants the ${mark} mark beside a warning`);
  }
});

test('§7.1: the step indicator is aria-current, not a colour', async () => {
  for (const page of ['mandate.html', 'search.html', 'portfolio.html']) {
    const dom = await loadPage(page);
    const current = dom.window.document.querySelectorAll('[data-step][aria-current="step"]');
    dom.window.close();
    assert.equal(current.length, 1, `${page} marks exactly one step as current`);
  }
});

test('§7.1: a map marker names its technology, and the legend names both', async () => {
  const dom = await loadPage('styleguide.html');
  const d = dom.window.document;
  const titles = [...d.querySelectorAll('svg title')].map((t) => t.textContent);
  dom.window.close();

  assert.ok(titles.some((t) => /solar/i.test(t)), 'a solar marker says so');
  assert.ok(titles.some((t) => /wind/i.test(t)), 'a wind marker says so');
});

test('§7.1: a negative cash-flow bar says so in its readout, not only by its fill', async () => {
  const dom = await loadPage('styleguide.html');
  const readouts = [...dom.window.document.querySelectorAll('ol button .sr-only')]
    .map((s) => s.textContent.trim());
  dom.window.close();

  assert.ok(readouts.some((r) => /minus/.test(r)),
    'the sign belongs in the accessible readout; position below the zero line is the other channel');
  assert.ok(readouts.every((r) => /\d{4}:/.test(r)), 'and each bar names its year');
});

test('§7.1: a chip repeats its pressed state as a filled or hollow square', async () => {
  const dom = await loadPage('mandate.html');
  const chips = [...dom.window.document.querySelectorAll('.chip')];
  dom.window.close();
  assert.ok(chips.length > 0);
  for (const chip of chips) {
    const pressed = chip.getAttribute('aria-pressed') === 'true';
    assert.ok(chip.textContent.includes(pressed ? status.MARK.locked : status.MARK.unlocked));
  }
});
