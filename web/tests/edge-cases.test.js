/**
 * edge-cases.test.js — the browser half of spec §13 (issue #13, 4C).
 *
 * Most of §13 is decided on the server, and `tests/unit/test_edge_cases_*.py`
 * asserts those rows end to end. Three of them are decided here, because nothing
 * on the server can tell you whether a panel degraded, whether a blocker was
 * announced, or whether a drawer said why a project contributes only outflows.
 *
 * The file also carries a **ledger**: §13's nine rows are read out of
 * `docs/spec.md` and each one must name a test that covers it, here or in Python.
 * A case that loses its home fails this file rather than quietly going uncovered,
 * which is the failure mode a sixteen-row checklist invites.
 *
 * No test here stubs `fetch`. The pages make no network call by design (A-15,
 * and `offline.test.js` enforces it), so a stubbed response would be asserting
 * that a stub returns what it was told to. Components are driven with
 * wire-shaped payloads instead, exactly as `holdings-table.test.js` does.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const { WEB, loadPage } = require('./helpers/page.js');
const fmt = require('../js/format.js');
const controls = require('../js/controls.js');
const feasibility = require('../js/feasibility.js');
const edge = require('../js/edge-states.js');

const REPO = path.resolve(WEB, '..');
const SPEC = fs.readFileSync(path.join(REPO, 'docs', 'spec.md'), 'utf8');
const PY = path.join(REPO, 'tests', 'unit');

/**
 * One reactive pass, then one frame.
 *
 * `x-text` lands on the microtask queue, but `x-show` applies its style through
 * `Alpine.mutateDom`, which defers to `requestAnimationFrame` — so a bare
 * `setTimeout` can observe the text updated and the visibility not yet, which is
 * a flake that reads exactly like a bug in the component.
 */
const settled = (dom) =>
  new Promise((resolve) => {
    dom.window.setTimeout(
      () => dom.window.requestAnimationFrame(() => dom.window.setTimeout(resolve, 30)),
      30,
    );
  });

/* ------------------------------------------------------------------ */
/* The ledger                                                          */
/* ------------------------------------------------------------------ */

/** Every §13 row, and the test that covers it. `here` names a test in this file. */
const SPEC_13 = {
  'No candidate passes the screens': {
    python: 'test_a_mandate_no_candidate_passes_disables_the_run_and_names_the_screens_to_widen',
    here: 'a mandate nothing passes blocks the run and says which screens are rejecting',
  },
  'Locked projects alone exceed available capital': {
    python: 'test_locks_that_alone_exceed_capital_block_the_run_and_name_the_locks_to_release',
    here: 'locks that alone exceed capital block the run and name themselves',
  },
  'Locked projects alone breach a concentration cap': {
    python: 'test_locked_projects_breaching_a_concentration_cap_still_run_and_show_the_breach',
  },
  'Capacity target unreachable': {
    python: 'test_an_unreachable_capacity_target_still_returns_a_portfolio_showing_the_shortfall',
  },
  'IRR undefined (no sign change in the cash flow)': {
    python: 'test_an_undefined_irr_renders_an_em_dash_and_is_left_out_of_the_weighted_average',
    here: 'an undefined IRR reaches the holdings row as an em dash, never as zero',
  },
  'Project already operating in the base year': {
    python: 'test_a_project_operating_in_the_base_year_books_its_equity_in_year_one',
  },
  'Hold period shorter than the last COD': {
    python: 'test_a_project_whose_cod_falls_after_the_hold_contributes_outflows_and_an_exit_only',
    here: 'the drawer says when commercial operation falls after the hold',
  },
  'Pipeline changes mid-session': {
    python: 'test_a_pipeline_that_moves_mid_session_keeps_stored_runs_and_warns_on_the_next_run',
    here: 'a moved pipeline is announced with the sentence the 409 carried',
  },
  'Map geometry unavailable': {
    python: 'test_missing_map_geometry_degrades_to_a_notice_and_leaves_the_rest_of_the_page',
    here: 'the map panel degrades to a notice and the rest of the page is untouched',
  },
};

/** The seven §13 adds for the file-based input model (epic §2), which spec.md predates. */
const INGESTION = {
  'a file fails a tie-out':
    'test_a_file_that_fails_a_tie_out_is_excluded_by_name_and_never_partially_loaded',
  'two files share an id':
    'test_two_files_sharing_an_id_abort_the_load_and_the_server_keeps_the_pipeline_it_had',
  'the pipeline is empty':
    'test_an_empty_pipeline_says_so_plainly_rather_than_running_zero_candidates',
  'a file declares outlier assumptions':
    'test_a_file_declaring_an_outlier_assumption_loads_and_is_flagged_never_blocked',
  'a file supplies a derived result':
    'test_a_file_supplying_a_mandate_dependent_result_is_rejected_with_the_reason_explained',
  'a statement series is the wrong length':
    'test_a_statement_series_of_the_wrong_length_names_the_field_and_the_expected_length',
  'a non-EUR file meets the EUR-only screen':
    'test_a_non_eur_file_is_screened_out_rather_than_converted',
};

function specCases() {
  const section = SPEC.slice(SPEC.indexOf('## 13. Edge cases'), SPEC.indexOf('## 14. Content'));
  return section
    .split('\n')
    .filter((line) => line.startsWith('| ') && !line.startsWith('| CASE') && !line.startsWith('|---'))
    .map((line) => line.split('|')[1].trim());
}

function pythonTestNames() {
  return fs
    .readdirSync(PY)
    .filter((name) => name.startsWith('test_edge_cases') && name.endsWith('.py'))
    .flatMap((name) => {
      const source = fs.readFileSync(path.join(PY, name), 'utf8');
      return [...source.matchAll(/^(?:async )?def (test_\w+)\(/gm)].map((m) => m[1]);
    });
}

test('every §13 row in the specification has a test that covers it', () => {
  const cases = specCases();
  assert.equal(cases.length, 9, 'spec.md §13 has nine rows');
  assert.deepEqual(cases.sort(), Object.keys(SPEC_13).sort(), 'the ledger is the §13 table');
});

test('every case the ledger claims in Python names a test that exists', () => {
  const defined = new Set(pythonTestNames());
  assert.ok(defined.size >= 16, `expected the 4C suite, found ${defined.size} tests`);

  const claimed = [
    ...Object.values(SPEC_13).map((entry) => entry.python),
    ...Object.values(INGESTION),
  ];
  const missing = claimed.filter((name) => !defined.has(name));
  assert.deepEqual(missing, [], 'a case pointing at a test that no longer exists is uncovered');
});

test('every case the ledger claims in this file names a test in this file', () => {
  const source = fs.readFileSync(__filename, 'utf8');
  const here = Object.values(SPEC_13)
    .map((entry) => entry.here)
    .filter(Boolean);
  for (const name of here) {
    assert.ok(source.includes(`test('${name}'`), `no test in this file is named "${name}"`);
  }
});

/* ------------------------------------------------------------------ */
/* Map geometry unavailable — §13 row 9                                */
/* ------------------------------------------------------------------ */

const ATLAS = JSON.parse(
  fs.readFileSync(path.join(WEB, 'public', 'countries-110m.json'), 'utf8'),
);

/** Two holdings in `api.md` §8.2's shape. `country` is the name, as the wire sends it. */
const SITES = [
  { id: 'P01', country: 'Spain', lat: 39.25, lon: -6.52, capacityMw: 180, technology: 'solar' },
  { id: 'P46', country: 'Denmark', lat: 55.7, lon: 8.1, capacityMw: 400, technology: 'wind' },
];

test('the map panel degrades to a notice and the rest of the page is untouched', async () => {
  const dom = await loadPage('portfolio.html');
  try {
    const panel = dom.window.document.querySelector('[data-region="map"]');
    const notice = dom.window.document.querySelector('[data-region="map-notice"]');
    assert.ok(notice, 'the notice has its own element');
    assert.equal(panel.getAttribute('role'), 'img');
    assert.equal(notice.closest('[role="img"]'), null,
      'and it sits outside the ARIA img, whose subtree is announced to nobody');
    const data = dom.window.Alpine.$data(panel);

    data.atlas = ATLAS;
    data.sites = SITES;
    await settled(dom);
    assert.equal(panel.querySelectorAll('circle').length, SITES.length, 'a marker per site');
    assert.ok(panel.querySelectorAll('path').length > 100, 'and the countries behind them');
    assert.equal(notice.style.display, 'none', 'and no notice while the map is drawn');

    data.atlas = null;
    await settled(dom);
    assert.equal(notice.textContent.trim(), 'Map data unavailable.');
    assert.equal(notice.style.display, '', 'the notice is shown');
    assert.equal(panel.style.display, 'none', 'and the ARIA img is gone, not merely empty');
    assert.equal(panel.querySelectorAll('circle').length, 0, 'and no half-drawn map');

    // "The rest of the page is unaffected" is the clause that actually matters,
    // and it is about the page, not about the panel.
    const document_ = dom.window.document;
    assert.ok(document_.querySelector('[data-region="holdings"]'), 'the holdings table survives');
    assert.ok(document_.querySelector('[data-region="cashflow-bars"]'), 'and the chart');
    assert.equal(document_.querySelectorAll('[data-tile]').length, 12, 'and all twelve tiles');
    assert.ok(document_.querySelector('[data-action="export"]'), 'and the export action');
  } finally {
    dom.window.close();
  }
});

test('the countries a portfolio holds are tinted, and the others are not', () => {
  // The bug this exists to catch: keying the tint on a code (`ES`, `ESP`) finds
  // nothing, because the atlas identifies a country by `properties.name` and its
  // `id` is a numeric ISO-3166 code. `export/committee.py` joins name to name, so
  // a mismatch here means the printed pack and the screen tint different maps —
  // and counting `<path>` elements, as this file used to, cannot see it.
  const svg = edge.siteMap({ atlas: ATLAS, sites: SITES }).markup;
  const tinted = (svg.match(/fill-accent-200/g) || []).length;
  const plain = (svg.match(/fill-neutral-200/g) || []).length;

  assert.equal(tinted, SITES.length, 'one tint per held country');
  assert.ok(plain > 100, 'and every other country stays neutral');

  const none = edge.siteMap({ atlas: ATLAS, sites: [] }).markup;
  assert.equal(none, '', 'no selection, no map');
});

test('a malformed atlas degrades exactly as a missing one does', () => {
  // The last four are the sharp ones: an atlas can be an object, and carry an
  // `objects.countries`, and still be unusable. `topojson.feature` throws on
  // them, and a throw inside these getters takes the surrounding Alpine bindings
  // with it — the opposite of degrading.
  for (const atlas of [
    undefined, null, {}, { objects: {} }, { objects: { countries: null } },
    { objects: { countries: {} } },
    { objects: { countries: { type: 'Nope' } } },
    { objects: { countries: { type: 'GeometryCollection' } } },
    { objects: { countries: { type: 'GeometryCollection', geometries: [] } }, arcs: [] },
  ]) {
    const map = edge.siteMap({ atlas, sites: SITES });
    assert.equal(map.available, false);
    assert.equal(map.notice, 'Map data unavailable.');
    assert.equal(map.markup, '', 'the notice is text for the page, never injected markup');
  }
});

test('every colour the map paints with is a class the stylesheet actually defines', () => {
  // The bug this exists to catch: 1D's Tailwind theme defines literal colours and
  // emits no custom properties, so `fill="var(--color-accent-200)"` resolves to
  // nothing and every country and marker falls back to the browser default. A test
  // that only counted the *string* in the markup would pass while the rendered map
  // was black — which is exactly what the tint assertion above would do on its own.
  const built = fs.readFileSync(path.join(WEB, 'dist', 'app.css'), 'utf8');
  const painted = Object.values(edge.paint);
  assert.ok(painted.length >= 6, 'every fill and stroke the map sets');

  for (const name of painted) {
    assert.match(built, new RegExp('\\.' + name + '\\{'),
      `${name} is not in dist/app.css — Tailwind never generated it`);
  }

  const svg = edge.siteMap({ atlas: ATLAS, sites: SITES }).markup;
  assert.ok(!svg.includes('var(--color-'),
    'the design system has no custom properties, so a var() here paints nothing');
});

test('the map draws the projection ui-contract.md §5.3 pins, and the pack reads the same numbers', () => {
  const pinned = JSON.parse(
    fs.readFileSync(path.join(REPO, 'src', 'terrafolio', 'export', 'pack-layout.json'), 'utf8'),
  ).map;

  // The committee pack reimplements this projection in Python. The two agreeing
  // is what stops a printed pack and the screen showing a site in two places.
  //
  // Compared key-set first, not against a list written out here: a hand-listed
  // subset silently stops covering a constant the moment `pack-layout.json` gains
  // one, which is the whole failure this check exists to prevent.
  assert.deepEqual(Object.keys(edge.map).sort(), Object.keys(pinned).sort(),
    'edge-states.js and pack-layout.json pin the same constants');
  for (const key of Object.keys(pinned)) {
    assert.equal(edge.map[key], pinned[key], `${key} disagrees with the pack`);
  }

  // Pinned against the pack's own output for Almonte Solar, so a change to either
  // implementation has to be made in both.
  const svg = edge.siteMap({ atlas: ATLAS, sites: [SITES[0]] }).markup;
  assert.ok(svg.includes('cx="123.1" cy="600.8"'), 'the same pixel the pack renders');
});

/* ------------------------------------------------------------------ */
/* Pipeline empty, and pipeline moved — §13 row 8 and the ingestion row */
/* ------------------------------------------------------------------ */

test('an empty pipeline is told apart from a mandate whose screens are too tight', () => {
  const nothing = feasibility.feasibility(payload([]), mandate(), locks());
  const tooTight = feasibility.feasibility(payload([project()]), mandate({ minDscr: 2.0 }), locks());

  for (const preview of [nothing, tooTight]) {
    assert.equal(preview.eligibleCount, 0);
    assert.equal(preview.runnable, false);
    assert.equal(preview.warnings[0].code, 'NO_CANDIDATES');
  }

  // The one field that separates them, and therefore the one a page can branch
  // on: there is nothing to widen when there is nothing to screen.
  assert.equal(nothing.totalCount, 0);
  assert.equal(tooTight.totalCount, 1);
});

test('an empty pipeline says so in its own words, not by asking for a wider screen', async () => {
  const dom = await loadPage('mandate.html');
  try {
    const notice = dom.window.document.querySelector('[data-region="pipeline-notice"]');
    assert.ok(notice, 'the mandate page has somewhere to say it');
    const data = dom.window.Alpine.$data(notice);

    assert.equal(data.fromStatus({ fileCount: 48, loadedCount: 48 }), 'none');
    await settled(dom);
    assert.equal(data.visible, false);

    assert.equal(data.fromStatus({ fileCount: 0, loadedCount: 0 }), 'empty');
    await settled(dom);
    assert.equal(
      data.message,
      'The pipeline holds no project files. Add files to pipeline/ and reload.',
    );
    assert.ok(!data.message.includes('Widen'), 'there is nothing to widen');

    // §7.1: never a colour on its own.
    assert.equal(data.mark, controls.status.MARK.blocking);
    assert.equal(data.word, controls.status.WORD.blocking);
    assert.equal(notice.querySelector('[aria-hidden="true"]').textContent, data.mark);
    assert.equal(notice.querySelector('.sr-only').textContent, data.word);
  } finally {
    dom.window.close();
  }
});

test('a pipeline whose files all fail their tie-outs is not called empty', () => {
  const notice = edge.pipelineNotice();
  assert.equal(notice.fromStatus({ fileCount: 48, loadedCount: 0 }), 'none');
  assert.equal(notice.visible, false, 'those files are named in the rejection list instead');
});

test('a moved pipeline is announced with the sentence the 409 carried', async () => {
  const dom = await loadPage('mandate.html');
  try {
    const notice = dom.window.document.querySelector('[data-region="pipeline-notice"]');
    const data = dom.window.Alpine.$data(notice);

    const sentence = 'The pipeline changed since you loaded it; reload and try again.';
    assert.equal(
      data.fromConflict({
        error: { code: 'PIPELINE_MOVED', message: sentence, detail: { currentPipelineHash: 'x' } },
      }),
      'moved',
    );
    await settled(dom);

    assert.equal(data.message, sentence, 'rendered verbatim, never paraphrased in a second place');
    assert.equal(data.mark, controls.status.MARK.blocking);
    assert.equal(notice.textContent.includes(sentence), true);

    // Some other error is not evidence the pipeline moved back, so the blocker
    // stands; a body the server accepted is, so it clears.
    assert.equal(data.fromConflict({ error: { code: 'NO_CANDIDATES', message: 'x' } }), 'moved');
    assert.equal(data.fromConflict({ runId: '01ABC', status: 'queued' }), 'none');
    await settled(dom);
    assert.equal(data.visible, false, 'a recovered page stops reading as blocked');

    data.fromConflict({ error: { code: 'PIPELINE_MOVED', message: sentence } });
    assert.equal(data.clear(), 'none', 'and any other route back is explicit');
  } finally {
    dom.window.close();
  }
});

/* ------------------------------------------------------------------ */
/* Hold shorter than the last COD — §13 row 7                          */
/* ------------------------------------------------------------------ */

test('the drawer says when commercial operation falls after the hold', async () => {
  const dom = await loadPage('portfolio.html');
  try {
    const note = dom.window.document.querySelector('[data-region="drawer-hold-note"]');
    assert.ok(note, 'ui-contract.md §5.5: the drawer says so');
    const data = dom.window.Alpine.$data(note);

    Object.assign(data, { codYear: 2028, baseYear: 2027, holdYears: 10 });
    await settled(dom);
    assert.equal(data.afterHold, false);
    assert.equal(data.message, '');
    assert.equal(data.codLabel, '2028');

    Object.assign(data, { codYear: 2032, baseYear: 2027, holdYears: 5 });
    await settled(dom);
    assert.equal(data.afterHold, true, 'a 2032 COD against a 2031 exit year');
    assert.equal(
      data.message,
      'Commercial operation falls after the 5-year hold. '
      + 'The project contributes construction outflows and an exit value only.',
    );
    assert.equal(data.codLabel, `2032 ${fmt.DASH} after the 5-year hold`);

    // A note, not a breach: nothing here is outside the mandate.
    assert.equal(data.mark, controls.status.MARK.info);
    assert.equal(data.word, controls.status.WORD.info);
    assert.ok(!note.className.includes('text-breach'));
  } finally {
    dom.window.close();
  }
});

test('the hold note moves with the slider, because nothing about it is stored', () => {
  const note = edge.holdingNote({ codYear: 2032, baseYear: 2027 });
  note.holdYears = 5;
  assert.equal(note.afterHold, true);
  note.holdYears = 10;
  assert.equal(note.afterHold, false, 'the same project, a longer hold, a different answer');
  assert.equal(note.message, '');
});

/* ------------------------------------------------------------------ */
/* Undefined IRR — §13 row 5                                           */
/* ------------------------------------------------------------------ */

test('an undefined IRR reaches the holdings row as an em dash, never as zero', async () => {
  const { ROWS, NOT_SELECTED } = require('./helpers/holdings.js');
  assert.equal(NOT_SELECTED.equityIrr, null, 'the fixture is the wire shape api.md §1.4 pins');

  const dom = await loadPage('portfolio.html');
  try {
    const table = dom.window.document
      .querySelector('[data-region="holdings"]')
      .closest('table');
    const data = dom.window.Alpine.$data(table);
    data.dscrFloor = 1.25;
    data.rows = ROWS;
    await settled(dom);

    const row = [...table.querySelectorAll('tbody tr')].find((tr) =>
      tr.textContent.includes(NOT_SELECTED.id));
    assert.ok(row, 'the project with no IRR still has a row');
    assert.ok(row.textContent.includes(fmt.DASH), 'and an em dash where its IRR would be');
    assert.ok(!row.textContent.includes('0.0%'), 'never coerced to zero');
  } finally {
    dom.window.close();
  }
});

/* ------------------------------------------------------------------ */
/* The two blocking warnings — §13 rows 1 and 2                        */
/* ------------------------------------------------------------------ */

test('a mandate nothing passes blocks the run and says which screens are rejecting', () => {
  const preview = feasibility.feasibility(payload([project()]), mandate({ minDscr: 2.0 }), locks());

  assert.equal(preview.runnable, false);
  assert.equal(preview.warnings.length, 1, 'an empty pool raises this and nothing else');
  assert.equal(preview.warnings[0].code, 'NO_CANDIDATES');
  assert.equal(preview.warnings[0].severity, 'alert', 'never "blocking": runnable carries the block');
  assert.equal(preview.warnings[0].mark, controls.status.MARK.blocking);

  // The screen that is actually rejecting, which the pinned sentence does not
  // name. `riskCap` is the client's name for what the server calls `riskScore`;
  // the two vocabularies differ in five of nine names, which is #12's parity
  // work and is raised on issue #1 rather than pinned here.
  const tight = mandate({ minDscr: 2.0 });
  const cap = feasibility.riskCap(payload([project()]), tight.riskAppetite);
  assert.deepEqual(feasibility.failedScreens(project(), tight, cap, []), ['minDscr']);
});

test('locks that alone exceed capital block the run and name themselves', () => {
  const held = project({ id: 'P01', totalCapex_m: 1000, seniorDebt_m: 100, equity_m: 900 });
  const preview = feasibility.feasibility(
    payload([held]), mandate({ availableCapital_m: 200 }), locks({ lockedIds: ['P01'] }),
  );

  assert.equal(preview.runnable, false);
  const blocked = preview.warnings.find((w) => w.code === 'LOCKS_EXCEED_CAPITAL');
  assert.ok(blocked, 'the blocking warning is present');
  assert.deepEqual(blocked.detail.lockedIds, ['P01'], '§13: say which locks to release');
  assert.ok(blocked.message.includes('Release a lock to run.'));
  assert.equal(blocked.mark, controls.status.MARK.blocking);
});

/* ------------------------------------------------------------------ */
/* Wire-shaped builders, as feasibility.test.js builds them            */
/* ------------------------------------------------------------------ */

function project(overrides) {
  return Object.assign({
    id: 'P01',
    countryCode: 'ES',
    technology: 'solar',
    stage: 'ready_to_build',
    capacityMw: 180,
    codYear: 2028,
    totalCapex_m: 103,
    seniorDebt_m: 74,
    equity_m: 29,
    minDscr: 1.4,
    developmentRiskScore: 2.7,
    gridSecured: true,
    omContracted: true,
    currency: 'EUR',
  }, overrides || {});
}

function payload(projects) {
  return {
    projects,
    assumptions: { riskCaps: { low: 2.6, balanced: 3.6, high: 5 } },
  };
}

function mandate(overrides) {
  return Object.assign({
    availableCapital_m: 1200,
    capacityTargetMw: 1500,
    solarShare: 0.45,
    targetIrr: 0.11,
    holdYears: 10,
    countries: ['ES', 'PT', 'IT', 'GR', 'FR', 'DE', 'PL', 'RO', 'NL', 'DK', 'IE', 'SE', 'FI', 'GB'],
    stages: ['greenfield', 'ready_to_build', 'construction'],
    minLeverage: 0.6,
    minDscr: 1.25,
    maxMerchantShare: 0.35,
    maxCountryShare: 0.35,
    maxProjectShare: 0.15,
    codFrom: 2027,
    codTo: 2032,
    riskAppetite: 'balanced',
    gridSecuredOnly: false,
    eurRevenueOnly: false,
    omContractedOnly: false,
  }, overrides || {});
}

function locks(overrides) {
  return Object.assign({ lockedIds: [], excludedIds: [] }, overrides || {});
}
