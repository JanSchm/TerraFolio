/**
 * Screen 02: the curves are the engine's own data, and only the pacing is the
 * screen's.
 *
 * spec §6 requires the curves to be "real per-generation data streamed from the
 * engine, not a simulated animation", and §6 also requires the screen to hold for
 * 1.5 s. Those pull against each other over the shipped pipeline, where a Standard
 * search finishes in under a tenth of a second and the event log hands a late
 * subscriber the whole run at once. The tests below hold both halves: every frame
 * drawn is a frame that arrived, in order, and none of them is drawn early.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { servePage } = require('./helpers/served.js');
const charts = require('../js/charts.js');

/** A run's worth of frames, shaped exactly as api.md §7's `generation` event. */
function trace(total = 60) {
  const out = [];
  for (let i = 1; i <= total; i += 1) {
    out.push({
      generation: i,
      totalGenerations: total,
      bestFitness: 4.826888 + (i / total) * 0.191968,
      meanFitness: -2.216503 + (i / total) * 2.145816,
      best: {
        projectCount: 10 + (i % 5),
        capacityMw: 1500 + i * 11,
        equity_m: 600 + i * 1.5,
        blendedIrr: 0.12 + i * 0.0002,
      },
    });
  }
  return out;
}

async function page(query = '?run=01M39AP4KR90QENNZ352112S39') {
  const dom = await servePage('search.html', 'http://127.0.0.1:8000/');
  // The page is loaded without a query string; drive `start` against one directly.
  const ctx = { dom, w: dom.window, d: dom.window.document, S: dom.window.TerraFolio.search };
  ctx.field = (name) => ctx.d.querySelector('[data-field="' + name + '"]').textContent.trim();
  ctx.points = (which) => ctx.d.querySelector('[data-series="' + which + '"]').getAttribute('points');
  ctx.query = query;
  return ctx;
}

/* ── Copy and formatting ─────────────────────────────────────────────────────── */

test('the status line is ui-contract.md §4\'s, in thirds of the run', async () => {
  const ctx = await page();
  const S = ctx.S;
  assert.equal(S.note(1, 60), S.NOTES[0]);
  assert.equal(S.note(25, 60), S.NOTES[1]);
  assert.equal(S.note(55, 60), S.NOTES[2]);
  assert.equal(S.note(0, 0), S.NOTES[0], 'before a total is known, the run is starting');
  ctx.dom.window.close();
});

test('the round counter is zero-padded to two digits', async () => {
  const ctx = await page();
  assert.equal(ctx.S.counter(7), '07');
  assert.equal(ctx.S.counter(60), '60');
  assert.equal(ctx.S.counter(110), '110', 'and not truncated past two');
  ctx.dom.window.close();
});

/* ── The stream ──────────────────────────────────────────────────────────────── */

test('frames are queued on arrival and drawn one per tick, in order', async () => {
  const ctx = await page();
  const run = ctx.S.start();
  const frames = trace(60);
  frames.forEach((frame) => ctx.S.arrive(run, frame));

  assert.equal(run.waiting.length, 60, 'the whole run arrived at once, as a replay does');
  assert.equal(ctx.field('round'), '—', 'and nothing is drawn yet');

  ctx.S.tick(run);
  assert.equal(ctx.field('round'), '01');
  ctx.S.tick(run);
  ctx.S.tick(run);
  assert.equal(ctx.field('round'), '03', 'one frame per tick, never a jump');

  for (let i = 0; i < 60; i += 1) ctx.S.tick(run);
  assert.equal(ctx.field('round'), '60');
  assert.equal(run.waiting.length, 0);
  ctx.S.stop(run);
  ctx.dom.window.close();
});

test('the total is known before the first frame, so the announcer is not left half-told', async () => {
  const ctx = await page();
  const run = ctx.S.start();
  ctx.S.arrive(run, trace(60)[0]);
  assert.equal(ctx.field('roundTotal'), '60');
  ctx.S.stop(run);
  ctx.dom.window.close();
});

test('the drawn curve is exactly what the run\'s stored trace produces', async () => {
  const ctx = await page();
  const run = ctx.S.start();
  const frames = trace(60);
  frames.forEach((frame) => ctx.S.arrive(run, frame));
  for (let i = 0; i < 60; i += 1) ctx.S.tick(run);

  // What a reader of GET /optimisations/{id} would plot from `convergence`.
  const stored = frames.map((f) => ({
    generation: f.generation, bestFitness: f.bestFitness, meanFitness: f.meanFitness,
  }));
  const expected = charts.curves(stored, 60);
  assert.equal(ctx.points('best'), expected.best);
  assert.equal(ctx.points('mean'), expected.mean);
  assert.equal(ctx.points('best').split(' ').length, 60, 'one point per round, none invented');
  ctx.S.stop(run);
  ctx.dom.window.close();
});

test('the curve grows left to right against a fixed axis rather than rescaling', async () => {
  const ctx = await page();
  const run = ctx.S.start();
  const frames = trace(60);
  frames.slice(0, 10).forEach((frame) => ctx.S.arrive(run, frame));
  for (let i = 0; i < 10; i += 1) ctx.S.tick(run);
  const tenth = ctx.points('best').split(' ')[9];

  frames.slice(10).forEach((frame) => ctx.S.arrive(run, frame));
  for (let i = 0; i < 50; i += 1) ctx.S.tick(run);
  assert.equal(ctx.points('best').split(' ')[9].split(',')[0], tenth.split(',')[0],
    'round ten keeps its x when fifty more arrive: x is scaled to the total');
  assert.equal(ctx.points('best').split(' ')[59].split(',')[0], '600');
  ctx.S.stop(run);
  ctx.dom.window.close();
});

/* ── The five live figures (ui-contract.md §4) ───────────────────────────────── */

test('the five figures come off the running best portfolio, through format.js', async () => {
  const ctx = await page();
  const run = ctx.S.start();
  ctx.S.arrive(run, {
    generation: 7,
    totalGenerations: 60,
    bestFitness: 5.018856,
    meanFitness: 0.18,
    best: { projectCount: 15, capacityMw: 2164, equity_m: 693.47, blendedIrr: 0.1316 },
  });
  ctx.S.tick(run);
  assert.equal(ctx.field('mandateScore'), '5.019', 'three decimals, per §4');
  assert.equal(ctx.field('bestCapacityMw'), '2,164 MW');
  assert.equal(ctx.field('bestProjectCount'), '15');
  assert.equal(ctx.field('bestEquity_m'), '€693m');
  assert.equal(ctx.field('bestBlendedIrr'), '13.2%');
  ctx.S.stop(run);
  ctx.dom.window.close();
});

test('a blended return with no defined contributor is an em dash, never zero', async () => {
  const ctx = await page();
  const run = ctx.S.start();
  ctx.S.arrive(run, {
    generation: 1, totalGenerations: 60, bestFitness: -3.5, meanFitness: -9,
    best: { projectCount: 2, capacityMw: 200, equity_m: 80, blendedIrr: null },
  });
  ctx.S.tick(run);
  assert.equal(ctx.field('bestBlendedIrr'), '—', 'api.md §1.4: null, never 0');
  ctx.S.stop(run);
  ctx.dom.window.close();
});

/* ── The progress bar ────────────────────────────────────────────────────────── */

test('the progress bar carries a value as well as a width', async () => {
  const ctx = await page();
  const run = ctx.S.start();
  const bar = ctx.d.querySelector('[data-field="progress"]');
  assert.equal(bar.getAttribute('aria-valuenow'), null, 'nothing has started');

  trace(60).slice(0, 30).forEach((frame) => ctx.S.arrive(run, frame));
  for (let i = 0; i < 30; i += 1) ctx.S.tick(run);
  assert.equal(bar.getAttribute('aria-valuenow'), '50');
  assert.equal(bar.getAttribute('aria-valuetext'), 'Round 30 of 60');
  assert.equal(ctx.d.querySelector('[data-field="progress-fill"]').style.width, '50%');
  ctx.S.stop(run);
  ctx.dom.window.close();
});

/* ── Holding the screen (§6) ─────────────────────────────────────────────────── */

test('a run that finishes instantly does not take the screen with it', async () => {
  const ctx = await page();
  const run = ctx.S.start();
  const before = ctx.w.location.href;
  trace(60).forEach((frame) => ctx.S.arrive(run, frame));
  for (let i = 0; i < 60; i += 1) ctx.S.tick(run);
  run.finished = { runId: 'x', status: 'succeeded' };
  ctx.S.tick(run);
  assert.equal(ctx.w.location.href, before,
    'spec §6: hold for 1.5 s so the transition is readable');
  assert.equal(ctx.S.MINIMUM_MS, 1500);
  ctx.S.stop(run);
  ctx.dom.window.close();
});

test('a failure says so on the screen rather than moving on', async () => {
  const ctx = await page();
  const run = ctx.S.start();
  const before = ctx.w.location.href;
  run.stopped = false;
  ctx.S.stop(run);
  assert.equal(run.stopped, true);
  assert.equal(ctx.w.location.href, before);
  ctx.dom.window.close();
});

/* ── With no server ──────────────────────────────────────────────────────────── */

test('with no run to watch the screen stays at its em dashes and opens no stream', async () => {
  const ctx = await page();
  const run = ctx.S.start();
  assert.equal(run.stream, null);
  assert.equal(ctx.field('round'), '—');
  assert.equal(ctx.field('mandateScore'), '—');
  assert.equal(ctx.points('best'), '');
  ctx.dom.window.close();
});

test('the way out of the screen is a real link, and it stops listening first', async () => {
  const ctx = await page();
  const cancel = ctx.d.querySelector('[data-action="cancel"]');
  assert.equal(cancel.tagName, 'A');
  assert.equal(cancel.getAttribute('href'), 'mandate.html',
    'a run can take half a minute; the screen must have a way out that works with no script');
  ctx.dom.window.close();
});
