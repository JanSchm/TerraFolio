/**
 * js/api.js — the guard that keeps the pages opening from disk, and the one adapter
 * between two endpoints that were specified apart.
 *
 * The behaviour under test is mostly what the client does when there is *no* server:
 * spec §12 requires the pages to work from a file:// URL, where fetch is CORS-blocked
 * (decisions.md A-15), and every page-loading test in this suite executes these
 * scripts under jsdom, which provides neither fetch nor EventSource. A call that
 * throws there takes Alpine's init pass down with it and fails a dozen other files.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { JSDOM } = require('jsdom');
const api = require('../js/api.js');

/* ── The offline guard ───────────────────────────────────────────────────────── */

test('bare node has no location, so the client reports itself offline', () => {
  assert.equal(api.offline(), true);
  assert.equal(api.streamable(), false);
});

test('every call resolves to null rather than throwing when there is no server', async () => {
  const calls = [
    api.getPipeline(10),
    api.getAssumptions(),
    api.getPipelineStatus(),
    api.reloadPipeline(),
    api.getProjectStatements('P001'),
    api.previewMandate({}, {}),
    api.postOptimisation({}),
    api.getResult('01JB2Q'),
  ];
  for (const call of calls) assert.equal(await call, null);
});

test('openStream answers null rather than constructing something that cannot exist', () => {
  assert.equal(api.openStream('01JB2Q', {}), null);
});

/** Loads the real js/api.js inside a window with the given URL and globals. */
function inWindow(url, globals = {}) {
  const dom = new JSDOM('<!doctype html><p>', { url, runScripts: 'dangerously' });
  Object.assign(dom.window, globals);
  dom.window.eval(fs.readFileSync(path.resolve(__dirname, '..', 'js', 'api.js'), 'utf8'));
  return dom;
}

test('a page opened from disk stays offline even where fetch and EventSource exist', () => {
  let called = false;
  const dom = inWindow('file://' + path.resolve(__dirname, '..', 'mandate.html'), {
    fetch: () => { called = true; return Promise.resolve(); },
    EventSource: function () { called = true; },
  });
  const client = dom.window.TerraFolio.api;
  assert.equal(client.offline(), true, 'file:// is an opaque origin; the request cannot succeed');
  assert.equal(client.streamable(), false);
  assert.equal(client.openStream('01JB2Q', {}), null);
  return client.getPipelineStatus().then((body) => {
    assert.equal(body, null);
    assert.equal(called, false, 'and nothing was attempted');
    dom.window.close();
  });
});

test('the same client served over http is online and does reach the network', () => {
  const asked = [];
  const dom = inWindow('http://127.0.0.1:8000/mandate.html', {
    fetch: (url) => {
      asked.push(url);
      return Promise.resolve({
        ok: true, status: 200, headers: { get: () => null },
        text: () => Promise.resolve('{"fileCount":300,"loadedCount":298}'),
      });
    },
  });
  const client = dom.window.TerraFolio.api;
  assert.equal(client.offline(), false);
  return client.getPipelineStatus().then((body) => {
    assert.deepEqual(asked, ['/pipeline/status'], 'a root-relative path: there is no CORS anywhere');
    assert.equal(body.loadedCount, 298);
    dom.window.close();
  });
});

/* ── The assumption-set adapter ──────────────────────────────────────────────── */

const ASSUMPTIONS = {
  id: 'default-2026',
  riskCaps: {
    project: { low: 2.6, balanced: 3.6, high: 5 },
    portfolio: { low: 2.4, balanced: 3.2, high: 4.2 },
  },
  co2FactorTPerMwh: 0.32,
};

test('the pipeline payload carries the per-project risk ceiling feasibility.js reads', () => {
  const merged = api.withAssumptions({ projectCount: 2, projects: [] }, ASSUMPTIONS);
  assert.deepEqual(merged.assumptions.riskCaps, { low: 2.6, balanced: 3.6, high: 5 },
    'ui-contract.md §3.3: the pre-screen is the project cap, not the portfolio average');
  assert.deepEqual(merged.assumptions.portfolioRiskCaps, { low: 2.4, balanced: 3.2, high: 4.2 });
});

test('the merge keeps every field GET /pipeline sent', () => {
  const pipeline = { pipelineHash: 'sha256:9f2c', baseYear: 2027, holdYears: 10, projectCount: 2, projects: [] };
  const merged = api.withAssumptions(pipeline, ASSUMPTIONS);
  for (const key of Object.keys(pipeline)) assert.deepEqual(merged[key], pipeline[key]);
  assert.equal(merged.assumptions.co2FactorTPerMwh, 0.32,
    'the tile that shows CO2 avoided must never carry its own factor');
});

test('the whole resolved set is kept, so the run can be shown what it used', () => {
  assert.equal(api.withAssumptions({}, ASSUMPTIONS).assumptions.set.id, 'default-2026');
});

test('a pipeline with no assumption set yields no cap rather than a silent default', () => {
  const merged = api.withAssumptions({ projects: [] }, null);
  assert.equal(merged.assumptions.riskCaps, null,
    'feasibility.js throws on a missing cap; failing open would admit the whole pipeline');
});

/* ── The error envelope ──────────────────────────────────────────────────────── */

test('an error carries the envelope api.md §1.7 pins', () => {
  const error = api.apiError(422, {
    error: {
      code: 'LOCKS_EXCEED_CAPITAL',
      message: 'Locked projects need €1,420m of equity against €1,200m available. Release a lock to run.',
      detail: { excess_m: 220, lockedIds: ['P01'] },
    },
  });
  assert.equal(error.status, 422);
  assert.equal(error.code, 'LOCKS_EXCEED_CAPITAL');
  assert.equal(error.detail.excess_m, 220);
  assert.match(error.message, /Release a lock to run\./);
});

test('a body that is not the envelope still gives something printable', () => {
  const error = api.apiError(500, null);
  assert.equal(error.code, null);
  assert.deepEqual(error.detail, {});
  assert.ok(error.message.length > 0, 'a user must never be shown an empty reason');
});

/* ── Export URLs ─────────────────────────────────────────────────────────────── */

test('the three exports are the server-side URLs api.md §9 lists', () => {
  assert.deepEqual(api.exportUrls('01JB2Q'), {
    holdings: '/optimisations/01JB2Q/holdings.csv',
    cashflow: '/optimisations/01JB2Q/cashflow.csv',
    pack: '/optimisations/01JB2Q/pack',
  });
});

test('a run id is escaped rather than pasted into the path', () => {
  assert.equal(api.exportUrls('a/b').holdings, '/optimisations/a%2Fb/holdings.csv');
});

/* ── The run id, read once for both screens ──────────────────────────────────── */

test('the run id comes out of the query string, whatever else is in it', () => {
  const cases = [
    ['?run=01JB2Q', '01JB2Q'],
    ['?cb=1&run=01JB2Q', '01JB2Q'],
    ['?run=01JB2Q&cb=1', '01JB2Q'],
    ['?run=a%2Fb', 'a/b'],
    ['?other=1', null],
    ['', null],
  ];
  for (const [search, expected] of cases) {
    const dom = inWindow('http://127.0.0.1:8000/portfolio.html' + search);
    assert.equal(dom.window.TerraFolio.api.runIdFromUrl(), expected, search || '(no query)');
    dom.window.close();
  }
});

/* ── The base year, which is not on a stored run ─────────────────────────────── */

test('the base year comes from a project\'s own declared assumptions', async () => {
  const asked = [];
  const dom = inWindow('http://127.0.0.1:8000/portfolio.html', {
    fetch: (url) => {
      asked.push(url);
      return Promise.resolve({
        ok: true, status: 200, headers: { get: () => null },
        text: () => Promise.resolve('{"id":"P001","assumptions":{"baseYear":2027}}'),
      });
    },
  });
  const year = await dom.window.TerraFolio.api.getBaseYear('P001');
  assert.equal(year, 2027);
  assert.deepEqual(asked, ['/projects/P001/statements'],
    'a few KB, not the several hundred GET /pipeline costs');
  dom.window.close();
});

test('it falls back to the pipeline when the project cannot answer', async () => {
  const asked = [];
  const dom = inWindow('http://127.0.0.1:8000/portfolio.html', {
    fetch: (url) => {
      asked.push(url);
      if (url.indexOf('/statements') !== -1) {
        return Promise.resolve({ ok: false, status: 404, headers: { get: () => null },
          text: () => Promise.resolve('{"error":{"code":"PROJECT_NOT_FOUND"}}') });
      }
      return Promise.resolve({ ok: true, status: 200, headers: { get: () => null },
        text: () => Promise.resolve('{"baseYear":2027,"projects":[]}') });
    },
  });
  assert.equal(await dom.window.TerraFolio.api.getBaseYear('P999'), 2027,
    'a project deleted since the run must not cost the chart its axis');
  assert.equal(asked.length, 2);
  dom.window.close();
});

test('a base year nobody can supply is null, never a guess', async () => {
  const dom = inWindow('file:///tmp/portfolio.html');
  assert.equal(await dom.window.TerraFolio.api.getBaseYear('P001'), null,
    'the mandate carries a COD window that merely looks like this number');
  dom.window.close();
});

/* ── A reload invalidates everything derived from the old directory ──────────── */

test('reloading the pipeline forgets the statements cached from before it', async () => {
  const bodies = ['{"id":"P001","assumptions":{"baseYear":2027}}', '{"id":"P001","assumptions":{"baseYear":2031}}'];
  let call = 0;
  const dom = inWindow('http://127.0.0.1:8000/mandate.html', {
    fetch: (url) => Promise.resolve({
      ok: true, status: 200, headers: { get: () => null },
      text: () => Promise.resolve(url.indexOf('/statements') !== -1 ? bodies[call++] : '{}'),
    }),
  });
  const client = dom.window.TerraFolio.api;
  assert.equal((await client.getProjectStatements('P001')).assumptions.baseYear, 2027);
  assert.equal((await client.getProjectStatements('P001')).assumptions.baseYear, 2027, 'cached');
  await client.reloadPipeline();
  assert.equal((await client.getProjectStatements('P001')).assumptions.baseYear, 2031,
    'users add and remove files while the server runs; a reload can change any of them');
  dom.window.close();
});
