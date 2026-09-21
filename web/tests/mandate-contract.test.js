/**
 * The mandate page emits a mandate object; feasibility.js and the optimiser consume it.
 * Nothing in between type-checks that hand-off, so these assert the two properties that
 * make it a contract rather than a coincidence.
 *
 * Both were real defects found in review:
 *   - the stage chips emitted their display labels ("Ready-to-build"), while every
 *     consumer compares against the wire value ("ready_to_build"), so every candidate
 *     failed the stage screen and the pool was always empty;
 *   - the three percent spinners emitted 35 where the mandate contract (D13) is
 *     fractions throughout, so a 35% merchant cap reached the objective as 3500%.
 *
 * Neither shows up as an error. The first returns an empty portfolio, the second
 * returns a portfolio with the cap silently disabled.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const controls = require('../js/controls.js');
const F = require('../js/feasibility.js');
const { loadPage } = require('./helpers/page.js');

/** Collects the value each control emits, by driving its own change handler. */
function emitted(factory, options, mutate) {
  const control = factory(options);
  let captured;
  const target = {
    dispatchEvent(event) { captured = event.detail; return true; },
  };
  mutate(control);
  control.changed({ target });
  return captured;
}

/* ── Wire values, not display labels ───────────────────────────────────────── */

test('the stage chips emit the values feasibility.js screens on', async () => {
  const dom = await loadPage('mandate.html');
  const chips = [...dom.window.document.querySelectorAll('.chip')];
  dom.window.close();

  const stages = chips
    .map((c) => c.textContent.replace(/[■□\s]/g, ''))
    .filter((t) => /Greenfield|Ready-to-build|Construction/i.test(t));
  assert.equal(stages.length, 3, 'the three stage chips must render their display labels');

  // What the control emits is a different thing from what it shows. A chip group
  // emits from toggle(), so capture there rather than through changed().
  const group = controls.chipGroup({
    name: 'stages',
    values: ['greenfield', 'ready_to_build', 'construction'],
    selected: ['greenfield', 'ready_to_build', 'construction'],
    labels: { greenfield: 'Greenfield', ready_to_build: 'Ready-to-build', construction: 'Construction' },
  });

  assert.equal(group.label('ready_to_build'), 'Ready-to-build', 'it shows the display label');

  let captured;
  const target = { dispatchEvent(event) { captured = event.detail; return true; } };
  group.toggle('greenfield', { target });

  assert.deepEqual(captured.value, ['ready_to_build', 'construction'],
    'toggling off greenfield leaves the other two, as wire values');
  for (const stage of captured.value) {
    assert.match(stage, /^[a-z_]+$/,
      `"${stage}" is a display label; the screens compare against the wire value`);
  }
});

test('the stage values in the page are exactly the ones the stage screen accepts', async () => {
  const dom = await loadPage('mandate.html');
  const html = dom.window.document.documentElement.outerHTML;
  dom.window.close();

  const declared = html.match(/values: \[([^\]]*)\][^}]*labels: \{ greenfield/);
  assert.ok(declared, 'the stage chip group must declare canonical values with display labels');
  const values = declared[1].split(',').map((v) => v.trim().replace(/'/g, ''));
  assert.deepEqual(values, ['greenfield', 'ready_to_build', 'construction']);

  // And the screen really does accept them.
  const candidate = (stage) => ({ id: 'P01', countryCode: 'ES', stage, technology: 'solar',
    capacityMw: 1, codYear: 2029, minDscr: 1.4, developmentRiskScore: 2, gridSecured: true,
    omContracted: true, currency: 'EUR', totalCapex_m: 1, seniorDebt_m: 0.6, equity_m: 0.4 });
  for (const stage of values) {
    const pool = F.projects({ projects: [candidate(stage)] });
    assert.equal(F.screens.stage(pool[0], { stages: values }), true,
      `the stage screen rejects "${stage}", which the page emits`);
  }
  assert.equal(F.screens.stage(F.projects({ projects: [candidate('Ready-to-build')] })[0],
    { stages: values }), false, 'a display label must not pass the screen — that is the bug');
});

test('the risk appetite emits a wire value, not its button label', async () => {
  const dom = await loadPage('mandate.html');
  const html = dom.window.document.documentElement.outerHTML;
  dom.window.close();
  assert.match(html, /options: \['low','balanced','high'\]/,
    'the segmented control must carry canonical values');
  assert.match(html, /labels: \{ low: 'Low', balanced: 'Balanced', high: 'High' \}/,
    'with the capitalised strings supplied as display labels');
  assert.doesNotMatch(html, /value: 'Balanced'/,
    'the default must be the wire value; risk_caps is keyed by it');
});

/* ── Fractions throughout (D13) ────────────────────────────────────────────── */

test('percent spinners emit fractions, not whole percent', () => {
  for (const [name, shown, expected] of [
    ['maxMerchantShare', 35, 0.35], ['maxCountryShare', 35, 0.35], ['maxProjectShare', 15, 0.15],
  ]) {
    const value = emitted(controls.numberField,
      { name, value: shown, min: 0, max: 100, step: 5, scale: 100 },
      () => {});
    assert.equal(value.value, expected,
      `${name} shows ${shown}% but must emit ${expected}; a cap emitted as ${shown} is 100x too large`);
  }
});

test('fields that are not percentages are emitted unscaled', () => {
  for (const [name, shown] of [['holdYears', 10], ['minDscr', 1.25],
    ['codFrom', 2027], ['codTo', 2032]]) {
    const value = emitted(controls.numberField, { name, value: shown }, () => {});
    assert.equal(value.value, shown, `${name} is not a percentage and must not be divided`);
  }
});

test('every percentage control on the page declares a scale of 100', async () => {
  const dom = await loadPage('mandate.html');
  const html = dom.window.document.documentElement.outerHTML;
  dom.window.close();

  // Every rate and share in the mandate: the two sliders and the three spinners.
  for (const name of ['targetIrr', 'minLeverage', 'maxMerchantShare',
    'maxCountryShare', 'maxProjectShare']) {
    const declaration = html.match(new RegExp(`name: '${name}'[^}]*}`));
    assert.ok(declaration, `${name} is missing from the page`);
    assert.match(declaration[0], /scale: 100/,
      `${name} is a percentage; without scale: 100 it emits whole percent (D13)`);
  }
  // And the split bar, which divides in its own handler.
  assert.match(html, /splitBar\(\{ name: 'solarShare'/);
});

test('the whole mandate round-trips into a pool feasibility can screen', () => {
  // The values the page emits, assembled as the optimiser would receive them.
  const mandate = {
    countries: ['ES'], stages: ['greenfield', 'ready_to_build', 'construction'],
    codFrom: 2027, codTo: 2032, minDscr: 1.25, riskAppetite: 'balanced',
    gridSecuredOnly: false, omContractedOnly: false, eurRevenueOnly: false,
    availableCapital_m: 1200, capacityTargetMw: 1500, solarShare: 0.45, minLeverage: 0.6,
    maxMerchantShare: 0.35, maxCountryShare: 0.35, maxProjectShare: 0.15, holdYears: 10,
  };
  const payload = {
    projects: [{ id: 'P01', countryCode: 'ES', stage: 'ready_to_build', technology: 'solar',
      capacityMw: 100, codYear: 2029, minDscr: 1.4, developmentRiskScore: 2.2,
      gridSecured: true, omContracted: true, currency: 'EUR',
      totalCapex_m: 100, seniorDebt_m: 70, equity_m: 30 }],
    assumptions: { riskCaps: { low: 2.6, balanced: 3.6, high: 5 } },
  };
  const result = F.feasibility(payload, mandate, { lockedIds: [], excludedIds: [] });
  assert.equal(result.pool.length, 1,
    'the mandate the page emits must admit a project that matches it');
  for (const rate of [mandate.solarShare, mandate.minLeverage, mandate.maxMerchantShare,
    mandate.maxCountryShare, mandate.maxProjectShare]) {
    assert.ok(rate > 0 && rate <= 1,
      `${rate} is not a fraction; api.md §1 says the wire never carries a percentage`);
  }

  // Every name the page emits must be a field api.md §6.1 defines.
  const FIELDS = new Set(['availableCapital_m', 'capacityTargetMw', 'solarShare', 'targetIrr',
    'holdYears', 'countries', 'stages', 'minLeverage', 'minDscr', 'maxMerchantShare',
    'maxCountryShare', 'maxProjectShare', 'codFrom', 'codTo', 'riskAppetite',
    'gridSecuredOnly', 'eurRevenueOnly', 'omContractedOnly']);
  for (const name of Object.keys(mandate)) {
    assert.ok(FIELDS.has(name), `${name} is not in api.md §6.1's mandate object`);
  }
});

/* ── Live feedback (epic §7: under 100ms, hence client-side) ───────────────── */

test('sliders and spinners dispatch on input, not only on change', async () => {
  const dom = await loadPage('mandate.html');
  const d = dom.window.document;
  const inputs = [...d.querySelectorAll('input[type=range], input[type=number]')];
  dom.window.close();

  assert.ok(inputs.length >= 10, `expected the mandate controls, saw ${inputs.length}`);
  for (const el of inputs) {
    const attrs = [...el.attributes].map((a) => a.name);
    assert.ok(attrs.includes('@input') || attrs.includes('x-on:input'),
      `a ${el.type} field dispatches only on change; dragging a slider or typing without ` +
      'blurring would leave the feasibility figures stale');
  }
});

test('radios still dispatch on change, which is when a radio changes', async () => {
  const dom = await loadPage('mandate.html');
  const radios = [...dom.window.document.querySelectorAll('input[type=radio]')];
  dom.window.close();
  for (const el of radios) {
    const attrs = [...el.attributes].map((a) => a.name);
    assert.ok(attrs.includes('@change') || attrs.includes('x-on:change'));
  }
});

/* ── The transparent slider's focus ring ───────────────────────────────────── */

for (const page of ['mandate.html', 'styleguide.html']) {
  test(`${page}: the split bar shows keyboard focus despite its transparent input`, async () => {
    const dom = await loadPage(page);
    const d = dom.window.document;
    const range = d.querySelector('input.opacity-0[type=range], input[type=range].opacity-0');
    assert.ok(range, 'the split bar is driven by a transparent range input');

    // opacity: 0 makes the element's own focus ring invisible, so the visible bar
    // must take it. Without this the control is operable but gives no focus feedback.
    const wrapper = range.parentElement;
    assert.match(wrapper.className, /has-\[:focus-visible\]:outline\b/,
      'the visible bar must show the ring');
    assert.match(wrapper.className, /has-\[:focus-visible\]:outline-accent-700/,
      'and it must be the system focus colour');
    dom.window.close();
  });
}

