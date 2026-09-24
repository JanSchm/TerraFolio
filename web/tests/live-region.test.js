/**
 * Progress and feasibility are announced — once, and only when there is news.
 *
 * ui-contract.md §7.2 asks for the mandate footer's figures and the search screen's
 * round counter to be polite live regions. Marking those regions live directly
 * satisfies the wording and defeats the purpose: feasibility recomputes on `input`
 * (A-18), so one drag of the capital slider would emit dozens of announcements, and
 * the engine streams a round at least every 100 ms (epic §7), so the counter would
 * interrupt itself ten times a second. A screen-reader user would turn it off, and
 * then hear nothing at all — which is the outcome §7.2 exists to prevent.
 *
 * What ships instead: the visible figures update at full rate and say nothing, and
 * one sr-only region carries a composed sentence per quiet window, derived by
 * observing the figures rather than written beside them. This is a deviation from
 * §7.2's letter and is recorded as such in docs/decisions.md.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { loadPage } = require('./helpers/page.js');
const { liveRegion } = require('../js/controls.js');

const after = (window, ms) => new Promise((r) => window.setTimeout(r, ms));

/** A footer's worth of figures, with no Alpine and no page in the way. */
function figures(html) {
  const dom = new JSDOM(`<dl id="figures">${html}</dl>`);
  return { dom, dl: dom.window.document.getElementById('figures') };
}

const PAIR = (term, value) => `<div><dt>${term}</dt><dd>${value}</dd></div>`;

/* ── The throttle ───────────────────────────────────────────────────────────── */

test('a burst of updates produces one announcement, after they stop', async () => {
  const { dom, dl } = figures(PAIR('Eligible capacity', '3,120 MW'));
  const region = liveRegion({ quiet: 40 });
  region.observe([dl]);

  const announced = [];
  Object.defineProperty(region, 'message', {
    get() { return announced[announced.length - 1] || ''; },
    set(v) { announced.push(v); },
  });

  // Forty ticks, which is about one drag of the capital slider.
  for (let i = 0; i < 40; i += 1) {
    dl.querySelector('dd').textContent = `${3000 + i} MW`;
    await after(dom.window, 1);
  }
  assert.deepEqual(announced, [], 'nothing is said while the figures are still moving');

  await after(dom.window, 80);
  assert.equal(announced.length, 1, 'and exactly one sentence once they settle');
  assert.equal(announced[0], 'Eligible capacity 3039 MW.');
  dom.window.close();
});

test('saying the same thing twice is not news', async () => {
  const { dom, dl } = figures(PAIR('Eligible capacity', '3,120 MW'));
  const region = liveRegion({ quiet: 20 });
  region.observe([dl]);

  dl.querySelector('dd').textContent = '3,200 MW';
  await after(dom.window, 50);
  assert.equal(region.message, 'Eligible capacity 3,200 MW.');

  // A re-render that lands on the same figures: the DOM changed, the news did not.
  dl.querySelector('dd').textContent = '3,200 MW';
  const said = region.message;
  await after(dom.window, 50);
  assert.equal(region.message, said, 'an unchanged sentence must not interrupt the reader again');
  dom.window.close();
});

test('a row of em dashes is not announced, because nothing is known yet', async () => {
  const { dom, dl } = figures(PAIR('Eligible capacity', '—') + PAIR('Equity required', '—'));
  const region = liveRegion({ quiet: 20 });
  region.observe([dl]);

  dl.querySelector('dd').textContent = '—';
  await after(dom.window, 50);
  assert.equal(region.message, '',
    'the pages ship showing em dashes; reading them aloud on arrival is noise, not progress');

  dl.querySelector('dd').textContent = '3,120 MW';
  await after(dom.window, 50);
  assert.match(region.message, /3,120 MW/, 'and it speaks as soon as a figure is real');
  dom.window.close();
});

test('a sentence pairs each label with its figure', async () => {
  const { dom, dl } = figures(
    PAIR('Candidates passing screens', '214 of 300')
    + PAIR('Eligible capacity', '3,120 MW')
    + PAIR('Equity required at full draw', '€1,154m'),
  );
  const region = liveRegion({ quiet: 10 });
  region.observe([dl]);
  dl.querySelector('dd').textContent = '214 of 300';
  await after(dom.window, 40);

  assert.equal(region.message,
    'Candidates passing screens 214 of 300. Eligible capacity 3,120 MW. '
    + 'Equity required at full draw €1,154m.');
  dom.window.close();
});

test('a flat definition list pairs each term with its own value', async () => {
  // The conventional <dl> shape has no per-pair wrapper. Asking a term's parent
  // for its first <dd> pairs every term with the first value, and the footer
  // announces confidently wrong figures with nothing failing.
  const dom = new JSDOM('<dl id="figures">'
    + '<dt>Eligible capacity</dt><dd>3,120 MW</dd>'
    + '<dt>Equity required at full draw</dt><dd>\u20AC1,154m</dd></dl>');
  const dl = dom.window.document.getElementById('figures');
  const region = liveRegion({ quiet: 10 });
  region.observe([dl]);
  dl.querySelector('dd').textContent = '3,120 MW';
  await after(dom.window, 40);

  assert.equal(region.message,
    'Eligible capacity 3,120 MW. Equity required at full draw \u20AC1,154m.');
  dom.window.close();
});

test('a source that renders empty announces nothing, not a lone full stop', async () => {
  const { dom, dl } = figures(PAIR('Eligible capacity', '3,120 MW'));
  const region = liveRegion({ quiet: 10 });
  region.observe([dl]);

  // #11 re-rendering the figures leaves the region empty for a frame.
  dl.textContent = '';
  await after(dom.window, 40);
  assert.equal(region.compose(), '', 'nothing composed means nothing to end with a stop');
  assert.equal(region.message, '', 'and nothing is announced');
  dom.window.close();
});

test('a label containing a digit does not defeat the unknown-figure guard', async () => {
  // The guard is about whether a *value* has arrived. Several labels #11 adds
  // carry numbers — "P50 GWh/y", "30-year FCFE", the CO2 factor.
  const { dom, dl } = figures(PAIR('P50 generation, GWh/y', '\u2014'));
  const region = liveRegion({ quiet: 10 });
  region.observe([dl]);
  dl.querySelector('dd').textContent = '\u2014';
  await after(dom.window, 40);
  assert.equal(region.message, '', 'the label\'s digits say nothing about the figure');

  dl.querySelector('dd').textContent = '388';
  await after(dom.window, 40);
  assert.match(region.message, /388/, 'and it speaks once the figure is real');
  dom.window.close();
});

test('a region with no definition list is announced as its own text', async () => {
  const dom = new JSDOM('<p id="round">ROUND <span>12</span> / <span>60</span></p>');
  const counter = dom.window.document.getElementById('round');
  const region = liveRegion({ quiet: 10 });
  region.observe([counter]);
  counter.querySelector('span').textContent = '13';
  await after(dom.window, 40);
  assert.equal(region.message, 'ROUND 13 / 60.');
  dom.window.close();
});

/* ── The wiring, on the real pages ──────────────────────────────────────────── */

const WIRED = [
  { page: 'mandate.html', announcer: 'feasibility-announcer', silent: 'feasibility-figures' },
  { page: 'search.html', announcer: 'progress-announcer', silent: 'live-stats' },
];

for (const { page, announcer, silent } of WIRED) {
  test(`${page} carries a polite status region, and the figures themselves stay silent`, async () => {
    const dom = await loadPage(page);
    const d = dom.window.document;
    const region = d.querySelector(`[data-region="${announcer}"]`);

    assert.ok(region, `${page} must have somewhere to announce from`);
    assert.equal(region.getAttribute('role'), 'status');
    assert.equal(region.getAttribute('aria-live'), 'polite', '§7.2: polite, never assertive');
    assert.equal(region.getAttribute('aria-atomic'), 'true', 'the whole sentence, not the diff');
    assert.ok(region.className.includes('sr-only'), 'it repeats what is already on screen');
    assert.equal(region.textContent.trim(), '', 'and it says nothing on arrival');

    const visible = d.querySelector(`[data-region="${silent}"]`);
    assert.equal(visible.hasAttribute('aria-live'), false,
      'the visible figures update on every tick; making them live is what §7.2 must not mean');
    dom.window.close();
  });
}

test('the mandate footer announces once the figures settle', async () => {
  const dom = await loadPage('mandate.html');
  const { window: w, window: { document: d } } = dom;
  const region = d.querySelector('[data-region="feasibility-announcer"]');

  for (let i = 0; i < 25; i += 1) {
    d.querySelector('[data-field="eligibleCount"]').textContent = String(200 + i);
    d.querySelector('[data-field="eligibleCapacityMw"]').textContent = `${3000 + i} MW`;
  }
  assert.equal(region.textContent.trim(), '', 'still silent while the slider is moving');

  await after(w, 900);
  assert.match(region.textContent, /Candidates passing screens 224/);
  assert.match(region.textContent, /Eligible capacity 3024 MW/);
  dom.window.close();
});
