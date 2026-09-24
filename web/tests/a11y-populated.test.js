/**
 * axe over the pages once they carry data.
 *
 * tests/a11y.test.js audits the four pages as they ship, which is the right
 * regression guard and is also a page on which every figure is an em dash, the
 * holdings body is empty, the warning list is empty and the map has not drawn.
 * Almost everything issue #11 renders is therefore audited by nothing there.
 *
 * So this runs the same rule set over the same pages after they have been given a
 * real mandate payload and a real stored run: the feasibility warnings, the
 * validation banner, thirty cash-flow bars, the map, sixteen columns of holdings,
 * the drawer with its four groups and its provenance block, and the export dialog.
 * The four rules jsdom cannot run are disabled here for the same reason and are
 * checked the same way — by tests/contrast.test.js over the token table.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const axe = require('axe-core');
const { servePage } = require('./helpers/served.js');

/** The same four, for the same reason. a11y.test.js pins the list itself. */
const NEEDS_LAYOUT = {
  'color-contrast': { enabled: false },
  'color-contrast-enhanced': { enabled: false },
  'target-size': { enabled: false },
  'scrollable-region-focusable': { enabled: false },
};

function describe(violations) {
  return violations.map((v) =>
    `\n  [${v.impact}] ${v.id}: ${v.help}\n    ${v.nodes.slice(0, 3)
      .map((n) => n.html.replace(/\s+/g, ' ').slice(0, 160)).join('\n    ')}`).join('');
}

async function audit(window, label) {
  window.eval(axe.source);
  const results = await window.axe.run(window.document, {
    resultTypes: ['violations'],
    rules: NEEDS_LAYOUT,
  });
  assert.equal(results.violations.length, 0,
    `${label} has ${results.violations.length} axe violation(s):${describe(results.violations)}`);
}

/* ── Screen 01, with a pipeline and every warning showing ───────────────────── */

function candidate(overrides) {
  return Object.assign({
    id: 'P01', countryCode: 'ES', stage: 'ready_to_build', technology: 'onshore_wind',
    capacityMw: 100, codYear: 2029, minDscr: 1.4, developmentRiskScore: 2.2,
    gridSecured: true, omContracted: true, currency: 'EUR',
    totalCapex_m: 100, seniorDebt_m: 55, equity_m: 45,
  }, overrides || {});
}

test('axe: the mandate screen with figures, warnings and the validation banner', async () => {
  const dom = await servePage('mandate.html');
  const w = dom.window;
  const M = w.TerraFolio.mandate;
  const page = M.startPage();

  M.usePipeline(page, {
    projects: [candidate(), candidate({ id: 'P02', technology: 'solar' })],
    assumptions: { riskCaps: { low: 2.6, balanced: 3.6, high: 5 } },
  });
  // A mandate that raises several warnings at once, including a blocking one later.
  page.mandate = Object.assign(M.readMandate(w.document.querySelector('[data-form="mandate"]')),
    { capacityTargetMw: 4000, solarShare: 0.95, minLeverage: 0.85, availableCapital_m: 4000 });
  M.steer('P01', { locked: true });
  w.document.querySelector('[data-form="mandate"]').dispatchEvent(
    new w.CustomEvent('tf:change', { detail: { name: 'minDscr', value: 1.25 }, bubbles: true }));

  M.renderHealth({ rejected: [{ file: 'P217.json', message: 'P217: closing balance remains.' }] });
  M.toggleRejected(w.document.querySelector('[data-action="show-rejected"]'));

  assert.ok(w.document.querySelectorAll('[data-region="feasibility-warnings"] li').length >= 3,
    'the audit is only worth running over markup that is actually there');
  assert.equal(w.document.querySelector('[data-region="pipeline-health"]').hidden, false);
  await audit(w, 'mandate.html with data');
  dom.window.close();
});

/* ── Screen 02, mid-run ─────────────────────────────────────────────────────── */

test('axe: the search screen with a curve, a progress value and five figures', async () => {
  const dom = await servePage('search.html');
  const w = dom.window;
  const S = w.TerraFolio.search;
  const run = S.start();
  for (let i = 1; i <= 20; i += 1) {
    S.arrive(run, {
      generation: i, totalGenerations: 60, bestFitness: 4.8 + i / 100, meanFitness: -2 + i / 10,
      best: { projectCount: 11, capacityMw: 1661, equity_m: 1154, blendedIrr: 0.121 },
    });
  }
  for (let i = 0; i < 20; i += 1) S.tick(run);
  assert.notEqual(w.document.querySelector('[data-series="best"]').getAttribute('points'), '');
  await audit(w, 'search.html mid-run');
  S.stop(run);
  dom.window.close();
});

/* ── Screen 03, with a run, the drawer open and the dialog open ─────────────── */

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
    provenance: { generation: { estimateBasis: 'benchmark', confidence: 'low' } },
    selected: true, locked: false,
  }, overrides || {});
}

test('axe: the portfolio screen with tiles, bars, a map, rows and both overlays open', async () => {
  const dom = await servePage('portfolio.html');
  const w = dom.window;
  const d = w.document;
  const P = w.TerraFolio.portfolio;
  const page = P.start();

  const cash = [-28.93, -14.2];
  for (let i = 2; i < 30; i += 1) cash.push(12 + i * 0.5);

  P.show(page, {
    runId: '01JB2Q', runRef: 'A-4', status: 'succeeded',
    mandate: {
      availableCapital_m: 1200, capacityTargetMw: 1500, solarShare: 0.45, targetIrr: 0.11,
      holdYears: 10, countries: ['ES'], stages: ['ready_to_build'], minLeverage: 0.6,
      minDscr: 1.25, maxMerchantShare: 0.35, maxCountryShare: 0.35, maxProjectShare: 0.15,
      codFrom: 2027, codTo: 2032, riskAppetite: 'balanced',
      gridSecuredOnly: false, eurRevenueOnly: false, omContractedOnly: false,
    },
    lockedIds: [], excludedIds: [], effort: 'standard', selectedIds: ['P01'],
    holdings: [holding(), holding({ id: 'P02', name: 'Rejected Wind', technology: 'onshore_wind',
      iso3: 'GBR', country: 'United Kingdom', countryCode: 'GB',
      selected: false, minDscr: 1.18, equityIrr: null, moic: null, paybackYear: null })],
    cashflow30Y_m: cash, cashflowHold_m: [], convergence: [],
    aggregates: {
      projectCount: 1, solarCount: 1, windCount: 0, capacityMw: 1450, solarShare: 0.44,
      totalCapex_m: 141, seniorDebt_m: 85, equity_m: 56, gearing: 0.62, capitalDeployed: 0.047,
      equityIrr: 0.124, moic: 1.94, weightedLcoe: 41, annualGenerationGwh: 388,
      co2AvoidedKt: 124, merchantShare: 0.51, weightedRiskScore: 2.7, worstMinDscr: 1.18,
      countryShares: { ES: 1 }, largestCountryCode: 'ES', largestCountryShare: 0.3,
      thirtyYearFcfe_m: cash.reduce((t, v) => t + v, 0), fitness: 5.018856,
    },
  });
  // Both views, so the de-emphasised row is audited too.
  page.view.selectedOnly = false;
  P.refresh(page);
  w.TerraFolio.map.draw(d.querySelector('[data-region="map"]'), [holding()], w.TerraFolio.worldAtlas);
  await new Promise((r) => setTimeout(r, 80));

  // Both overlays open, in the order a user reaches them: the row opens the drawer,
  // and the statements arrive after it, which is what appends the provenance block.
  d.querySelector('[data-region="holdings"] [data-action="open-drawer"]').click();
  P.provenance({ provenance: {
    preparedBy: 'a.nalyst', preparedOn: '2026-09-21', modelVersion: 'house-model@1',
    fields: { debtTerms: { estimateBasis: 'internal_model', confidence: 'low' } },
  } });
  d.querySelector('[data-action="export"]').click();
  await new Promise((r) => setTimeout(r, 80));

  assert.equal(d.querySelectorAll('[data-region="cashflow-bars"] button').length, 30);
  assert.equal(d.querySelectorAll('[data-region="holdings"] tr').length, 2);
  assert.ok(d.querySelector('[data-region="map"] svg'));
  assert.ok(d.querySelector('[data-region="drawer-provenance"]'));
  await audit(w, 'portfolio.html with a run, drawer and dialog');
  dom.window.close();
});

/* ── Keyboard reachability, over the same populated markup ──────────────────── */

const TABBABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]),'
  + ' select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** 3B's rule: a control the page renders is a control a keyboard can reach. */
function stranded(document) {
  return [...document.querySelectorAll('[data-action], [data-sort], [data-filter]')]
    .filter((el) => !el.closest('[hidden]'))
    .filter((el) => !el.matches(TABBABLE) && !el.querySelector(TABBABLE))
    .map((el) => el.tagName.toLowerCase() + '['
      + (el.dataset.action || el.dataset.sort || el.dataset.filter) + ']');
}

test('every control the mandate screen renders can be reached by keyboard', async () => {
  const dom = await servePage('mandate.html');
  const w = dom.window;
  const M = w.TerraFolio.mandate;
  M.startPage();
  M.renderHealth({ rejected: [{ file: 'P217.json', message: 'P217: closing balance remains.' }] });
  M.toggleRejected(w.document.querySelector('[data-action="show-rejected"]'));
  assert.deepEqual(stranded(w.document), []);
  dom.window.close();
});

test('every cash-flow bar is a control, and every rendered control is reachable', async () => {
  const dom = await servePage('portfolio.html');
  const w = dom.window;
  const d = w.document;
  const P = w.TerraFolio.portfolio;
  const page = P.start();
  const cash = [-28.93, -14.2];
  for (let i = 2; i < 30; i += 1) cash.push(12 + i * 0.5);
  P.show(page, {
    runId: '01JB2Q', runRef: 'A-4', status: 'succeeded',
    mandate: { holdYears: 10, minDscr: 1.25, codFrom: 2027, availableCapital_m: 1200,
      capacityTargetMw: 1500, solarShare: 0.45, targetIrr: 0.11, minLeverage: 0.6,
      maxMerchantShare: 0.35, maxCountryShare: 0.35, countries: ['ES'], stages: ['ready_to_build'] },
    lockedIds: [], excludedIds: [], selectedIds: ['P01'], holdings: [holding()],
    cashflow30Y_m: cash, cashflowHold_m: [], convergence: [],
    aggregates: { projectCount: 1, solarCount: 1, windCount: 0, capacityMw: 1450, solarShare: 0.44,
      totalCapex_m: 141, seniorDebt_m: 85, equity_m: 56, gearing: 0.62, capitalDeployed: 0.05,
      equityIrr: 0.124, moic: 1.94, weightedLcoe: 41, annualGenerationGwh: 388, co2AvoidedKt: 124,
      merchantShare: 0.28, weightedRiskScore: 2.7, worstMinDscr: 1.38, countryShares: { ES: 1 },
      largestCountryShare: 0.3, thirtyYearFcfe_m: cash.reduce((t, v) => t + v, 0), fitness: 5 },
  });
  await new Promise((r) => setTimeout(r, 80));

  const bars = d.querySelectorAll('[data-region="cashflow-bars"] button');
  assert.equal(bars.length, 30);
  assert.equal([...bars].every((b) => b.matches(TABBABLE)), true,
    'ui-contract.md §5.2: each bar is focusable, so the series is not pointer-only');
  assert.deepEqual(stranded(d), []);
  dom.window.close();
});
