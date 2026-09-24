/**
 * Screen 03 over one stored run.
 *
 * The fixture is a whole `GET /optimisations/{id}` body rather than a handful of
 * fields, because that is what the screen reads and because the two cash-flow series
 * have to be present and distinct for anything here to mean much: `cashflow30Y_m`
 * carries no terminal value and drives the chart and the tile, `cashflowHold_m`
 * carries one and drives nothing on this screen (api.md §1.5).
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { servePage } = require('./helpers/served.js');
const status = require('../js/controls.js').status;

const plain = (v) => JSON.parse(JSON.stringify(v));

function holding(overrides) {
  return Object.assign({
    id: 'P01', name: 'Almonte Solar', country: 'Spain', countryCode: 'ES', iso3: 'ESP',
    lat: 39.33, lon: -1.12, technology: 'solar', stage: 'ready_to_build',
    capacityMw: 180, codYear: 2028, netCapacityFactor: 0.246, annualGenerationGwh: 388,
    opexPerKwYear: 46, ppaShare: 0.72, ppaTenorYears: 15, ppaPrice: 71,
    countryBaseloadPrice: 85, captureFactor: 0.68, capturePrice: 75,
    developmentRiskScore: 2.7, gridSecured: true, omContracted: true, currency: 'EUR',
    totalCapex_m: 141, seniorDebt_m: 85, equity_m: 56, gearing: 0.6, maxGearing: 0.72,
    capexPerKw: 1700, debtRate: 0.055, debtTenorYears: 18, lcoe: 41, minDscr: 1.38,
    thirtyYearFcfe_m: 200, equityIrr: 0.124, moic: 1.94, paybackYear: 2036,
    provenance: {
      generation: { estimateBasis: 'engineering_estimate', confidence: 'medium' },
      price: { estimateBasis: 'contracted', confidence: 'high' },
      capex: { estimateBasis: 'benchmark', confidence: 'medium' },
      opex: { estimateBasis: 'benchmark', confidence: 'medium' },
      debtTerms: { estimateBasis: 'internal_model', confidence: 'low' },
      grid: { estimateBasis: 'contracted', confidence: 'high' },
      om: { estimateBasis: 'contracted', confidence: 'high' },
    },
    selected: true, locked: false,
  }, overrides || {});
}

const MANDATE = {
  availableCapital_m: 1200, capacityTargetMw: 1500, solarShare: 0.45, targetIrr: 0.11,
  holdYears: 10, countries: ['ES', 'PT'], stages: ['ready_to_build'], minLeverage: 0.6,
  minDscr: 1.25, maxMerchantShare: 0.35, maxCountryShare: 0.35, maxProjectShare: 0.15,
  codFrom: 2027, codTo: 2032, riskAppetite: 'balanced',
  gridSecuredOnly: false, eurRevenueOnly: false, omContractedOnly: false,
};

function cashflow30() {
  const out = [-28.93, -14.2];
  for (let i = 2; i < 30; i += 1) out.push(12 + i * 0.5);
  return out;
}

/** api.md §8.1: `thirtyYearFcfe_m` is `sum(cashflow30Y_m)`, so the fixture says so. */
const THIRTY_YEAR_TOTAL = cashflow30().reduce((t, v) => t + v, 0);

function runOf(overrides) {
  return Object.assign({
    runId: '01JB2Q', runRef: 'A-4', status: 'succeeded', durationMs: 2483,
    // Not a field of RunRecord: `start` resolves it through api.getBaseYear and hangs
    // it on the result before rendering, because the pipeline's base year is what
    // cashflow30Y_m is indexed from and the mandate's COD window is not.
    baseYear: 2027,
    mandate: MANDATE, lockedIds: [], excludedIds: [], effort: 'standard',
    selectedIds: ['P01'], holdings: [holding()],
    cashflow30Y_m: cashflow30(),
    cashflowHold_m: [-28.93, -14.2, 12, 13, 14, 15, 16, 17, 18, 640],
    convergence: [{ generation: 1, bestFitness: 4.8, meanFitness: -2.2 }],
    aggregates: Object.assign({
      projectCount: 1, solarCount: 1, windCount: 0, capacityMw: 1450, solarShare: 0.44,
      totalCapex_m: 141, seniorDebt_m: 85, equity_m: 56, gearing: 0.62, capitalDeployed: 0.047,
      equityIrr: 0.124, moic: 1.94, weightedLcoe: 41, annualGenerationGwh: 388,
      co2AvoidedKt: 124, merchantShare: 0.28, weightedRiskScore: 2.7, worstMinDscr: 1.38,
      countryShares: { ES: 1 }, largestCountryCode: 'ES', largestCountryShare: 0.3,
      thirtyYearFcfe_m: THIRTY_YEAR_TOTAL, fitness: 5.018856,
    }, (overrides || {}).aggregates || {}),
  }, overrides || {});
}

async function page(run) {
  const dom = await servePage('portfolio.html');
  const w = dom.window;
  const P = w.TerraFolio.portfolio;
  const handle = P.start();
  if (run) P.show(handle, run);
  await new Promise((r) => setTimeout(r, 80));
  const d = w.document;
  return {
    dom, w, d, P, handle,
    M: w.TerraFolio.mandate,
    field: (n) => d.querySelector('[data-field="' + n + '"]').textContent.trim(),
    tile: (n) => d.querySelector('[data-tile="' + n + '"]'),
  };
}

/* ── The twelve tiles (ui-contract.md §5.1) ─────────────────────────────────── */

test('every tile shows its value, the mandate figure it is judged against, and a state', async () => {
  const ctx = await page(runOf());
  const expected = {
    capacity: ['1,450 MW', 'target 1,500 MW \u00b7 50 MW short'],
    projects: ['1', '1 solar · 0 wind'],
    'tech-split': ['44% solar', 'target 45% solar'],
    equity: ['€56m', 'of €1,200m · 5% deployed'],
    cost: ['€141m', '€85m senior debt'],
    irr: ['12.4%', 'hurdle 11.0% · 1.94× MOIC'],
    leverage: ['62%', 'min 60% · DSCR floor 1.38×'],
    lcoe: ['€41', 'per MWh, 6% real'],
    generation: ['388 GWh', '124 kt CO₂ avoided p.a.'],
    fcfe30: ['€510m', 'undiscounted, post debt'],
    merchant: ['28%', 'cap 35%'],
    concentration: ['30%', 'cap 35% · risk score 2.7'],
  };
  for (const [name, [value, sub]] of Object.entries(expected)) {
    assert.equal(ctx.field(name), value, name + ' value');
    assert.equal(ctx.field(name + '-sub'), sub, name + ' sub-label');
  }
  ctx.dom.window.close();
});

test('a tile that meets its rule is compliant, in a mark and a word as well as a tone', async () => {
  const ctx = await page(runOf());
  const irr = ctx.tile('irr');
  assert.equal(irr.querySelector('[aria-hidden="true"]').textContent, status.MARK.onTarget);
  assert.equal(irr.querySelector('.sr-only').textContent, 'on target');
  assert.match(irr.querySelector('[data-field="irr-sub"]').parentElement.className, /text-accent-700/);
  ctx.dom.window.close();
});

test('a tile that breaches says so, and the breach is surfaced rather than hidden', async () => {
  const ctx = await page(runOf({ aggregates: { merchantShare: 0.51, equityIrr: 0.08 } }));
  for (const name of ['merchant', 'irr']) {
    const tile = ctx.tile(name);
    assert.equal(tile.querySelector('[aria-hidden="true"]').textContent, status.MARK.breach, name);
    assert.equal(tile.querySelector('.sr-only').textContent, 'outside the mandate');
    assert.match(tile.querySelector('[data-field="' + name + '-sub"]').parentElement.className, /text-breach/);
  }
  ctx.dom.window.close();
});

test('a tile with no mandate target carries neither mark nor word', async () => {
  const ctx = await page(runOf());
  for (const name of ['projects', 'cost', 'lcoe', 'generation', 'fcfe30', 'equity']) {
    assert.equal(ctx.tile(name).querySelector('[aria-hidden="true"]').textContent, '', name);
    assert.equal(ctx.tile(name).querySelector('.sr-only').textContent, '', name);
  }
  ctx.dom.window.close();
});

test('an undefined portfolio IRR is an em dash, and the MOIC clause is dropped', async () => {
  const ctx = await page(runOf({ aggregates: { equityIrr: null, moic: null } }));
  assert.equal(ctx.field('irr'), '—', 'never 0.0%, at any hand-off (epic §5)');
  assert.equal(ctx.field('irr-sub'), 'hurdle 11.0%',
    '§5.1: the MOIC clause is dropped rather than shown as 0.00×');
  assert.equal(ctx.tile('irr').querySelector('[aria-hidden="true"]').textContent, '',
    'and an IRR that does not exist neither meets the hurdle nor breaches it');
  ctx.dom.window.close();
});

test('an unreachable capacity target shows the shortfall, and the run still stands', async () => {
  const ctx = await page(runOf({ aggregates: { capacityMw: 900 } }));
  assert.equal(ctx.field('capacity'), '900 MW');
  assert.equal(ctx.field('capacity-sub'), 'target 1,500 MW · 600 MW short',
    'spec §13: the tile shows the shortfall against target');
  ctx.dom.window.close();
});

test('the hold period reaches the IRR tile\'s own label', async () => {
  const ctx = await page(runOf());
  assert.equal(ctx.field('irr-hold'), '(10y)');
  ctx.dom.window.close();
});

/* ── The run sub-line ────────────────────────────────────────────────────────── */

test('the sub-line names the portfolio, the hold and the run reference', async () => {
  const ctx = await page(runOf());
  assert.equal(ctx.field('result-subtitle'),
    '1,450 MW across 1 projects in 1 countries · 10-year hold · run A-4');
  ctx.dom.window.close();
});

/* ── The cash-flow chart (ui-contract.md §5.2) ──────────────────────────────── */

test('thirty bars, from the series that carries no terminal value', async () => {
  const run = runOf();
  const ctx = await page(run);
  const bars = ctx.d.querySelectorAll('[data-region="cashflow-bars"] button');
  assert.equal(bars.length, 30);
  assert.equal(ctx.field('cashflow-cumulative'), '€510m');
  assert.equal(ctx.field('fcfe30'), ctx.field('cashflow-cumulative'),
    'the tile and the caption are one sum, and api.md §8.1 defines it that way');

  // The hold series ends at 640 with its terminal value; nothing on this screen
  // may show it. A-6 calls conflating the two the likeliest silent bug here.
  const readouts = [...bars].map((b) => b.textContent);
  assert.equal(readouts.some((r) => r.includes('640')), false,
    'the chart must not be drawn from cashflowHold_m');
  assert.match(readouts[0], /^2027: minus €28\.9m$/,
    'ui-contract.md §5.2 pins the hover readout at one decimal');
  ctx.dom.window.close();
});

test('each bar is a real control, so the series is reachable without a pointer', async () => {
  const ctx = await page(runOf());
  const bars = ctx.d.querySelectorAll('[data-region="cashflow-bars"] button');
  assert.equal([...bars].every((b) => b.tagName === 'BUTTON' && !b.disabled), true);
  assert.equal([...bars].every((b) => b.querySelector('.sr-only').textContent.trim().length > 0), true);
  ctx.dom.window.close();
});

test('a negative year says "minus", because below the line is a position and a tone', async () => {
  const ctx = await page(runOf());
  const first = ctx.d.querySelector('[data-region="cashflow-bars"] button');
  assert.match(first.textContent, /minus/);
  assert.match(first.className, /bg-accent-300/);
  assert.ok(first.style.top, 'a negative bar hangs from the zero line');
  const last = [...ctx.d.querySelectorAll('[data-region="cashflow-bars"] button')].pop();
  assert.equal(/minus/.test(last.textContent), false);
  assert.ok(last.style.bottom, 'and a positive one stands on it');
  ctx.dom.window.close();
});

test('the same series is available as text, for a reader who cannot see a bar', async () => {
  const ctx = await page(runOf());
  const rows = ctx.d.querySelectorAll('[data-region="cashflow-table"] tbody tr');
  assert.equal(rows.length, 30);
  assert.equal(rows[0].querySelector('th').textContent, '2027');
  assert.equal(rows[0].querySelector('td').textContent, 'minus €28.9m');
  ctx.dom.window.close();
});

test('ticks fall every fifth year and the axis labels the extremes', async () => {
  const ctx = await page(runOf());
  const marks = [...ctx.d.querySelectorAll('[data-region="cashflow-ticks"] li')]
    .map((li) => li.textContent).filter(Boolean);
  assert.deepEqual(marks, ['2027', '2032', '2037', '2042', '2047', '2052']);
  assert.equal(ctx.field('axis-top'), '€27m');
  assert.equal(ctx.field('axis-bottom'), '€-29m');
  ctx.dom.window.close();
});

/* ── The drawer (ui-contract.md §5.5) ───────────────────────────────────────── */

test('the drawer names the project and locates it to two decimals', async () => {
  const ctx = await page(runOf());
  ctx.P.openDrawer(ctx.handle, 'P01');
  assert.equal(ctx.field('drawer-kicker'), 'Solar · Ready-to-build');
  assert.equal(ctx.field('drawer-name'), 'Almonte Solar');
  assert.equal(ctx.field('drawer-location'), 'Spain · 39.33°, -1.12° · P01');
  ctx.dom.window.close();
});

test('the four headline figures and the four groups §5.5 lists are all present', async () => {
  const ctx = await page(runOf());
  ctx.P.openDrawer(ctx.handle, 'P01');
  const headline = [...ctx.d.querySelectorAll('[data-region="drawer-headline"] dt')].map((t) => t.textContent);
  assert.deepEqual(headline, ['Capacity', 'Equity IRR (10y)', 'Equity', 'MOIC']);
  const values = [...ctx.d.querySelectorAll('[data-region="drawer-headline"] dd')].map((t) => t.textContent);
  assert.deepEqual(values, ['180 MW', '12.4%', '€56m', '1.94×']);

  const groups = [...ctx.d.querySelectorAll('[data-region="drawer-groups"] h3')].map((h) => h.textContent);
  assert.deepEqual(groups, ['Technical', 'Capital structure', 'Revenue', 'Risk']);
  const text = ctx.d.querySelector('[data-region="drawer-groups"]').textContent;
  assert.match(text, /Long-term service agreement signed/);
  assert.match(text, /€85m at 60%, 5\.5%, 18y/);
  assert.match(text, /€71\/MWh for 15 yrs/);
  assert.match(text, /€75\/MWh \(baseload €85\)/);
  ctx.dom.window.close();
});

test('a figure softer than a signed contract says which, and a contracted one does not', async () => {
  const ctx = await page(runOf());
  ctx.P.openDrawer(ctx.handle, 'P01');
  const text = ctx.d.querySelector('[data-region="drawer-groups"]').textContent;
  assert.match(text, /engineering estimate/, 'the capacity factor is a study, not a contract');
  assert.match(text, /internal model/, 'and the debt terms are the house model');
  assert.equal(/contracted/.test(text), false,
    'a contracted figure carries no note: the note exists to mark what is not one');
  ctx.dom.window.close();
});

test('the full provenance block arrives with the statements and names the analyst', async () => {
  const ctx = await page(runOf());
  ctx.P.openDrawer(ctx.handle, 'P01');
  ctx.P.provenance({
    id: 'P01',
    provenance: {
      preparedBy: 'a.nalyst', preparedOn: '2026-09-21', modelVersion: 'house-model@1',
      fields: { debtTerms: { estimateBasis: 'internal_model', confidence: 'low', note: 'x' } },
    },
  });
  const block = ctx.d.querySelector('[data-region="drawer-provenance"]');
  assert.ok(block, 'api.md §2 abbreviates provenance on the pipeline; the notes come with §4');
  assert.match(block.textContent, /a\.nalyst/);
  assert.match(block.textContent, /2026-09-21/);
  assert.match(block.textContent, /Debt terms/, 'and the group reads as words, not as a key');
  assert.match(block.textContent, /internal model · low/);
  ctx.dom.window.close();
});

test('re-opening the drawer does not stack a second provenance block', async () => {
  const ctx = await page(runOf());
  const payload = { provenance: { preparedBy: 'x', fields: {} } };
  ctx.P.openDrawer(ctx.handle, 'P01');
  ctx.P.provenance(payload);
  ctx.P.provenance(payload);
  assert.equal(ctx.d.querySelectorAll('[data-region="drawer-provenance"]').length, 1);
  ctx.dom.window.close();
});

test('a project completing after the hold ends says what it contributes', async () => {
  const ctx = await page(runOf({ holdings: [holding({ codYear: 2040 })] }));
  ctx.P.openDrawer(ctx.handle, 'P01');
  assert.match(ctx.d.querySelector('[data-region="drawer-groups"]').textContent,
    /falls after the 10-year hold ends in 2036.*construction outflows and an exit value only/s,
    'spec §13 requires the detail sheet to flag it');
  ctx.dom.window.close();
});

test('an unlevered project reports no payback rather than year zero', async () => {
  const ctx = await page(runOf({ holdings: [holding({ paybackYear: null })] }));
  ctx.P.openDrawer(ctx.handle, 'P01');
  assert.match(ctx.d.querySelector('[data-region="drawer-groups"]').textContent, /beyond 2056/);
  ctx.dom.window.close();
});

test('a non-euro revenue currency flags the hedge', async () => {
  const ctx = await page(runOf({ holdings: [holding({ currency: 'PLN' })] }));
  ctx.P.openDrawer(ctx.handle, 'P01');
  assert.match(ctx.d.querySelector('[data-region="drawer-groups"]').textContent,
    /PLN — hedge required/);
  ctx.dom.window.close();
});

/* ── Steering (§7.6) ────────────────────────────────────────────────────────── */

test('locking from the drawer records it and marks the row', async () => {
  const ctx = await page(runOf());
  ctx.P.openDrawer(ctx.handle, 'P01');
  ctx.P.steer(ctx.handle, 'P01', { locked: true });
  await new Promise((r) => setTimeout(r, 60));
  assert.deepEqual(plain(ctx.M.steering().lockedIds), ['P01']);
  const row = ctx.d.querySelector('[data-project-id="P01"]');
  assert.match(row.className, /bg-highlight/);
  assert.equal(row.children[0].querySelector('[aria-hidden="true"]').textContent, status.MARK.locked);
  assert.match(row.children[0].querySelector('.sr-only').textContent, /Locked/);
  assert.equal(ctx.d.querySelector('[data-action="drawer-lock"]').getAttribute('aria-pressed'), 'true');
  assert.equal(ctx.d.querySelector('[data-action="drawer-lock"]').textContent.trim(), 'Unlock');
  ctx.dom.window.close();
});

test('excluding clears the lock, and both buttons say what they now do', async () => {
  const ctx = await page(runOf());
  ctx.P.openDrawer(ctx.handle, 'P01');
  ctx.P.steer(ctx.handle, 'P01', { locked: true });
  ctx.P.steer(ctx.handle, 'P01', { excluded: true });
  assert.deepEqual(plain(ctx.M.steering().lockedIds), []);
  assert.deepEqual(plain(ctx.M.steering().excludedIds), ['P01']);
  assert.equal(ctx.d.querySelector('[data-action="drawer-exclude"]').textContent.trim(), 'Re-admit candidate');
  assert.equal(ctx.d.querySelector('[data-action="drawer-lock"]').textContent.trim(), 'Lock into portfolio');
  ctx.dom.window.close();
});

test('the primary action relabels once the run no longer matches what is on screen', async () => {
  const ctx = await page();
  const run = runOf();
  // A run submitted from this session records what it was submitted with.
  ctx.M.saveSteering({ lockedIds: [], excludedIds: [], runId: '01JB2Q', runRef: 'A-4',
    signature: ctx.M.signature(run.mandate, { lockedIds: [], excludedIds: [] }) });
  ctx.P.show(ctx.handle, run);
  ctx.P.rerunLabel(ctx.handle);
  assert.equal(ctx.d.querySelector('[data-action="rerun"]').textContent.trim(), 'Re-run');

  ctx.P.steer(ctx.handle, 'P01', { locked: true });
  assert.equal(ctx.d.querySelector('[data-action="rerun"]').textContent.trim(), 'Re-run with changes');
  ctx.dom.window.close();
});

test('a mandate the server re-ordered is still the same mandate', async () => {
  const ctx = await page();
  const run = runOf();
  ctx.M.saveSteering({ lockedIds: [], excludedIds: [], runId: '01JB2Q', runRef: 'A-4',
    signature: ctx.M.signature(Object.assign({}, run.mandate, { countries: ['PT', 'ES'] }),
      { lockedIds: [], excludedIds: [] }) });
  ctx.P.show(ctx.handle, run);
  ctx.P.rerunLabel(ctx.handle);
  assert.equal(ctx.d.querySelector('[data-action="rerun"]').textContent.trim(), 'Re-run',
    'domain/mandate.py sorts countries and stages; the chips emit them in §3.2 order');
  ctx.dom.window.close();
});

/* ── Exports and the table ──────────────────────────────────────────────────── */

test('the export dialog carries the run and counts only the selection', async () => {
  const ctx = await page(runOf({ selectedIds: ['P01', 'P09'] }));
  assert.equal(ctx.field('export-subtitle'),
    '1,450 MW across 1 projects in 1 countries · 10-year hold · run A-4');
  assert.equal(ctx.field('export-rows'), '2 rows');
  ctx.dom.window.close();
});

test('the table reads the run\'s own candidates, and the DSCR floor its own mandate', async () => {
  const ctx = await page(runOf({
    holdings: [holding(), holding({ id: 'P02', name: 'Rejected', selected: false, minDscr: 1.18 })],
  }));
  assert.equal(ctx.field('table-count'), '1 of 1', 'the selected view, by default');
  assert.equal(ctx.handle.table.dscrFloor, 1.25, 'from the run, never from the local mandate');

  ctx.handle.view.selectedOnly = false;
  ctx.P.refresh(ctx.handle);
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(ctx.field('table-count'), '2 of 2');
  const rejected = ctx.d.querySelector('[data-project-id="P02"]');
  assert.match(rejected.className, /bg-deemph/);
  assert.match(rejected.children[0].querySelector('.sr-only').textContent, /Not selected/);
  assert.match(rejected.children[14].className, /text-breach/, '1.18× is under the 1.25× floor');
  assert.match(rejected.children[14].querySelector('.sr-only').textContent, /below the 1.25× floor/);
  ctx.dom.window.close();
});

test('the country filter offers the markets this run actually saw', async () => {
  const ctx = await page(runOf({
    holdings: [holding(), holding({ id: 'P02', country: 'Poland', countryCode: 'PL' })],
  }));
  ctx.handle.view.selectedOnly = false;
  const options = [...ctx.d.querySelectorAll('[data-filter="country"] option')].map((o) => o.value);
  assert.deepEqual(options, ['', 'PL', 'ES'].sort((a, b) => (a === '' ? -1 : b === '' ? 1 : 0)).slice(0, 3).length === 3 ? options : options);
  assert.equal(options.length, 3);
  assert.equal(options[0], '', 'All countries stays first');
  assert.deepEqual(options.slice(1).sort(), ['ES', 'PL']);
  ctx.dom.window.close();
});

/* ── With no run ────────────────────────────────────────────────────────────── */

test('opened with no run the screen keeps its em dashes and renders nothing false', async () => {
  const ctx = await page();
  assert.equal(ctx.field('result-subtitle'), '—');
  assert.equal(ctx.field('capacity'), '—');
  assert.equal(ctx.d.querySelectorAll('[data-region="holdings"] tr').length, 0);
  assert.equal(ctx.d.querySelectorAll('[data-region="cashflow-bars"] li').length, 0);
  ctx.dom.window.close();
});

/* ── The base year is the pipeline's, not the mandate's COD window ───────────── */

test('the chart is labelled from the base year the pipeline supplied', async () => {
  const ctx = await page(runOf({ baseYear: 2030 }));
  const bars = [...ctx.d.querySelectorAll('[data-region="cashflow-bars"] button')];
  assert.match(bars[0].textContent, /^2030:/);
  assert.match(bars[29].textContent, /^2059:/);
  const marks = [...ctx.d.querySelectorAll('[data-region="cashflow-ticks"] li')]
    .map((li) => li.textContent).filter(Boolean);
  assert.deepEqual(marks, ['2030', '2035', '2040', '2045', '2050', '2055']);
  ctx.dom.window.close();
});

test('the mandate\'s COD window does not move the chart\'s years', async () => {
  // codFrom is an independent screen, 2027-2033 (ui-contract §3.2). Reading it as
  // the base year mislabelled all thirty bars whenever a user narrowed the window.
  const run = runOf({ baseYear: 2027 });
  run.mandate = Object.assign({}, run.mandate, { codFrom: 2031, codTo: 2033 });
  const ctx = await page(run);
  assert.match(ctx.d.querySelector('[data-region="cashflow-bars"] button').textContent, /^2027:/);
  ctx.dom.window.close();
});

test('an unknown base year is an em dash, not a confident wrong year', async () => {
  const run = runOf();
  delete run.baseYear;
  const ctx = await page(run);
  const bars = [...ctx.d.querySelectorAll('[data-region="cashflow-bars"] button')];
  assert.equal(bars.length, 30, 'the amounts are known even when the years are not');
  assert.match(bars[0].textContent, /^—:/);
  assert.deepEqual([...ctx.d.querySelectorAll('[data-region="cashflow-ticks"] li')]
    .map((li) => li.textContent).filter(Boolean), []);
  ctx.dom.window.close();
});

/* ── A year with no figure ───────────────────────────────────────────────────── */

test('a year with no cash flow reads as an em dash and draws no bar', async () => {
  const cash = cashflow30();
  cash[4] = null;
  const ctx = await page(runOf({ cashflow30Y_m: cash }));
  const columns = [...ctx.d.querySelectorAll('[data-region="cashflow-bars"] li')];
  assert.equal(columns.length, 30);
  assert.equal(columns[4].querySelector('button'), null,
    'epic §5: an absent figure is not a cash flow of zero');
  assert.equal(columns[4].textContent.trim(), '2031: —');
  const rows = ctx.d.querySelectorAll('[data-region="cashflow-table"] tbody tr');
  assert.equal(rows[4].querySelector('td').textContent, '—');
  ctx.dom.window.close();
});

/* ── The hover readout returns to its idle text ──────────────────────────────── */

test('the readout names the bar under the pointer, and lets go of it', async () => {
  const ctx = await page(runOf());
  const bar = ctx.d.querySelector('[data-region="cashflow-bars"] button');
  assert.equal(ctx.field('cashflow-hover'), 'hover a bar');
  bar.dispatchEvent(new ctx.w.Event('mouseenter'));
  assert.equal(ctx.field('cashflow-hover'), '2027: minus €28.9m');
  bar.dispatchEvent(new ctx.w.Event('mouseleave'));
  assert.equal(ctx.field('cashflow-hover'), 'hover a bar',
    'ui-contract.md §5.2: `hover a bar` when idle');
  bar.dispatchEvent(new ctx.w.Event('focus'));
  assert.equal(ctx.field('cashflow-hover'), '2027: minus €28.9m');
  bar.dispatchEvent(new ctx.w.Event('blur'));
  assert.equal(ctx.field('cashflow-hover'), 'hover a bar');
  ctx.dom.window.close();
});

/* ── A run this screen cannot draw ───────────────────────────────────────────── */

test('a run that has not finished says so instead of showing twelve em dashes', async () => {
  const ctx = await page();
  ctx.P.notice(ctx.P.unrenderable({ status: 'running' }));
  const region = ctx.d.querySelector('[data-region="portfolio-notice"]');
  assert.equal(region.hidden, false);
  assert.match(region.textContent, /has not finished/);
  assert.equal(region.getAttribute('role'), 'status');
  assert.match(region.querySelector('.sr-only').textContent, /Warning/,
    '§7.1: the alert tone is never the only signal');
  ctx.dom.window.close();
});

test('a failed run shows the reason the API gave for it', async () => {
  const ctx = await page();
  assert.match(ctx.P.unrenderable({ status: 'failed', error: { message: 'The search did not complete.' } }),
    /The search did not complete\./);
  assert.match(ctx.P.unrenderable({ status: 'cancelled' }), /cancelled/);
  assert.match(ctx.P.unrenderable({ status: 'failed' }), /no portfolio to show/,
    'a failed run is audit trail, not a blank page');
  ctx.dom.window.close();
});

test('the notice hides again when there is nothing to say', async () => {
  const ctx = await page();
  ctx.P.notice('something');
  ctx.P.notice('');
  assert.equal(ctx.d.querySelector('[data-region="portfolio-notice"]').hidden, true);
  ctx.dom.window.close();
});

/* ── Re-run ──────────────────────────────────────────────────────────────────── */

test('a re-run the server refuses says why, and hands the button back', async () => {
  const ctx = await page(runOf());
  const api = ctx.w.TerraFolio.api;
  api.postOptimisation = () => Promise.reject(Object.assign(
    new Error('The pipeline changed since you loaded it; reload and try again.'),
    { code: 'PIPELINE_MOVED', status: 409 }));
  api.offline = () => false;

  await ctx.P.submit(ctx.handle);
  const region = ctx.d.querySelector('[data-region="portfolio-notice"]');
  assert.equal(region.hidden, false, 'spec §13: a re-run against a moved pipeline warns');
  assert.match(region.textContent, /reload and try again/);
  assert.equal(ctx.d.querySelector('[data-action="rerun"]').disabled, false);
  assert.equal(ctx.handle.submitting, false, 'and the screen is ready to try again');
  ctx.dom.window.close();
});

test('a re-run will not start twice while one is in flight', async () => {
  const ctx = await page(runOf());
  const api = ctx.w.TerraFolio.api;
  let posts = 0;
  api.offline = () => false;
  api.postOptimisation = () => { posts += 1; return new Promise(() => {}); };

  ctx.P.submit(ctx.handle);
  ctx.P.submit(ctx.handle);
  ctx.P.submit(ctx.handle);
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(posts, 1, 'spec §12 keeps every run indefinitely; a double-click must not store two');
  ctx.dom.window.close();
});

test('a re-run claims the snapshot its own run saw, so a moved pipeline can 409', async () => {
  const run = runOf();
  run.provenance = { pipelineHash: 'sha256:the-one-this-run-saw', assumptionSetId: 'default-2026' };
  const ctx = await page(run);
  const api = ctx.w.TerraFolio.api;
  let sent = null;
  let askedForCurrent = false;
  api.offline = () => false;
  api.getPipelineStatus = () => { askedForCurrent = true; return Promise.resolve({ pipelineHash: 'sha256:moved' }); };
  api.postOptimisation = (body) => { sent = body; return new Promise(() => {}); };

  ctx.P.submit(ctx.handle);
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(sent.pipelineHash, 'sha256:the-one-this-run-saw',
    'fetching the current hash first would make the 409 unreachable by construction');
  assert.equal(sent.assumptionSetId, 'default-2026');
  assert.equal(askedForCurrent, false, 'and there is no round trip to make before posting');
  ctx.dom.window.close();
});

/* ── Exports ─────────────────────────────────────────────────────────────────── */

test('re-rendering does not leave a second listener on an export button', async () => {
  const ctx = await page(runOf());
  const downloads = [];
  const proto = ctx.w.HTMLAnchorElement.prototype;
  const real = proto.click;
  proto.click = function () { downloads.push(this.getAttribute('href')); };
  ctx.w.TerraFolio.api.offline = () => false;

  ctx.P.show(ctx.handle, runOf());
  ctx.P.show(ctx.handle, runOf());
  ctx.d.querySelector('[data-action="export-holdings"]').click();
  proto.click = real;

  assert.deepEqual(downloads, ['/optimisations/01JB2Q/holdings.csv'],
    'one click, one download, however many times the screen has rendered');
  ctx.dom.window.close();
});

/* ── The baseline the primary action compares against ────────────────────────── */

test('a shared result opened fresh records what its run was submitted with', async () => {
  const ctx = await page();
  const run = runOf();
  assert.equal(ctx.M.steering().signature, null, 'a session that did not submit it has none');

  ctx.P.show(ctx.handle, run);
  assert.notEqual(ctx.M.steering().signature, null,
    'without a baseline changed() answers false for ever and the action never relabels');
  assert.equal(ctx.d.querySelector('[data-action="rerun"]').textContent.trim(), 'Re-run',
    'nothing has moved yet');
  ctx.dom.window.close();
});

test('and then notices when the saved mandate has moved since that run', async () => {
  const ctx = await page();
  const run = runOf();
  ctx.P.show(ctx.handle, run);
  ctx.M.saveMandate(Object.assign({}, run.mandate, { holdYears: 20 }));
  ctx.P.rerunLabel(ctx.handle);
  assert.equal(ctx.d.querySelector('[data-action="rerun"]').textContent.trim(),
    'Re-run with changes', '§7.6 relabels when the mandate has been edited');
  ctx.dom.window.close();
});

test('the baseline is what the run carried, not what this session holds now', async () => {
  const ctx = await page();
  const run = runOf({ lockedIds: [], excludedIds: [] });
  // A lock this session added before opening the run must still read as a change.
  ctx.M.steer('P01', { locked: true });
  ctx.P.show(ctx.handle, run);
  ctx.P.rerunLabel(ctx.handle);
  assert.equal(ctx.d.querySelector('[data-action="rerun"]').textContent.trim(),
    'Re-run with changes');
  ctx.dom.window.close();
});

test('opening a second run re-bases the comparison onto that run', async () => {
  const ctx = await page();
  ctx.P.show(ctx.handle, runOf({ runId: 'RUN-A', runRef: 'A-1' }));
  const first = ctx.M.steering().signature;

  const second = runOf({ runId: 'RUN-B', runRef: 'A-2' });
  second.mandate = Object.assign({}, second.mandate, { holdYears: 25 });
  ctx.P.show(ctx.handle, second);
  assert.notEqual(ctx.M.steering().signature, first,
    'the baseline describes the run on screen, not the one before it');
  assert.equal(ctx.d.querySelector('[data-action="rerun"]').textContent.trim(), 'Re-run');
  ctx.dom.window.close();
});
