#!/usr/bin/env node
// ---------------------------------------------------------------------------
// extract_reference.mjs — turn the JS reference implementation into golden
// fixtures.  Issue 1C (#4).  Node stdlib only; no npm dependencies.
//
//   node tools/extract_reference.mjs           emit every fixture
//   node tools/extract_reference.mjs --check   re-emit to a temp dir and
//                                              byte-compare with the committed
//                                              fixtures (the determinism gate)
//
// WHERE THE REFERENCE LIVES, AND HOW TO RE-RUN THIS IF THE BUNDLE CHANGES
// ----------------------------------------------------------------------
// `Portfolio Optimiser (standalone).html` is a self-extracting page.  It holds
// four `<script type="__bundler/*">` tags:
//
//   __bundler/manifest       gzip+base64 assets (React, d3, topojson, fonts,
//                            a world-atlas TopoJSON) keyed by uuid
//   __bundler/ext_resources  external resource map
//   __bundler/page_order     nested-page bundle order (empty here)
//   __bundler/template       a JSON-encoded *string* holding the page's HTML
//
// The application is inside that last one.  JSON.parse the tag's text and you
// get an HTML document; inside it, `<script type="text/x-dc" data-dc-script>`
// holds the whole application as `class Component extends DCLogic { ... }`.
// That class carries the model we want: buildPipeline(), seeded(), model(),
// irr(), holdCf(), projIrr(), eligible(), aggregate(), fitness() and the GA
// step() inside run().
//
// We locate both tags by tag boundaries rather than by line number, so
// reformatting the bundle does not break extraction.  The class body is
// written out VERBATIM to tests/golden/fixtures/reference/component.mjs,
// wrapped in `defineComponent(DCLogic)` so the base class becomes a parameter
// instead of a global.  The tool then imports that written file, so the
// committed artefact is provably the code that produced the fixtures.
//
// The emitted modules use the .mjs extension deliberately.  There is no
// package.json in this repository, so a `.js` file's module classification
// would fall to Node's module-syntax detection — which is version-dependent
// and can be turned off with --no-experimental-detect-module, under which the
// import fails with "Named export 'defineComponent' not found".  A .mjs
// extension is unambiguous on every Node that supports ESM at all.
//
// If the bundle is replaced, BUNDLE_SHA256 below will not match and the tool
// refuses to emit.  Re-verify the new bundle by hand, check the assertions in
// assertReferenceIntegrity() still describe it, then update the constant.
// ---------------------------------------------------------------------------

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');

const BUNDLE_NAME = 'Portfolio Optimiser (standalone).html';
const BUNDLE_SHA256 = 'bc4722f836d1274a4351c1c986f7661ae0ef399c5f915cad758dbce4f10a7463';

const TOOL_VERSION = '1.1.0';

// Node 18 is the first release where everything this tool relies on is stable:
// ESM, node:test, structuredClone-free stdlib use, and fs.rmSync/cpSync.
const MIN_NODE_MAJOR = 18;
// The version string 1B's schema requires: docs/pipeline-schema §4 rejects a
// file at any other version, naming it.
const SCHEMA_VERSION = '1.0';

// ===========================================================================
// 1. EXTRACTION
// ===========================================================================

function sha256(buf) {
  return crypto.createHash('sha256').update(buf).digest('hex');
}

function fail(msg) {
  throw new Error(msg);
}

function readBundle() {
  const file = path.join(ROOT, BUNDLE_NAME);
  if (!fs.existsSync(file)) fail(`bundle not found at ${file}`);
  const bytes = fs.readFileSync(file);
  const got = sha256(bytes);
  if (got !== BUNDLE_SHA256) {
    fail(
      `bundle sha256 mismatch.\n  expected ${BUNDLE_SHA256}\n  got      ${got}\n` +
      `The reference bundle has changed. Re-verify it by hand, confirm the\n` +
      `assertions in assertReferenceIntegrity() still hold, then update\n` +
      `BUNDLE_SHA256. Refusing to emit goldens from an unverified bundle.`
    );
  }
  return bytes.toString('utf8');
}

// Pull the text content of a <script> tag out of an HTML string, by tag
// boundaries.  `opener` is matched literally against the start of the tag.
function scriptBody(html, opener, what) {
  const at = html.indexOf(opener);
  if (at < 0) fail(`could not find ${what} (${opener})`);
  const bodyStart = html.indexOf('>', at) + 1;
  const bodyEnd = html.indexOf('</script>', bodyStart);
  if (bodyStart <= 0 || bodyEnd < 0) fail(`malformed ${what} tag`);
  return html.slice(bodyStart, bodyEnd);
}

function extractClassBody(bundleText) {
  const templateJson = scriptBody(bundleText, '<script type="__bundler/template">', 'the bundler template tag');
  let pageHtml;
  try {
    pageHtml = JSON.parse(templateJson);
  } catch (e) {
    fail(`the bundler template tag did not hold a JSON string: ${e.message}`);
  }
  if (typeof pageHtml !== 'string') fail('the bundler template tag decoded to a non-string');
  const body = scriptBody(pageHtml, '<script type="text/x-dc"', 'the application script tag').trim();
  if (!body.startsWith('class Component extends DCLogic')) {
    fail(`the application script does not start with the expected class declaration; got: ${body.slice(0, 80)}`);
  }
  return body;
}

// Everything downstream depends on these being the reference we think it is.
// A bundle that still parses but whose model has changed would silently emit
// wrong goldens, so assert the shape of what we extracted.
function assertReferenceIntegrity(classBody) {
  const methods = [
    'buildPipeline', 'seeded', 'model', 'irr', 'holdCf', 'projIrr',
    'eligible', 'aggregate', 'fitness',
  ];
  for (const m of methods) {
    if (!new RegExp(`(^|[\\s;}])${m}\\s*[(=]`, 'm').test(classBody)) {
      fail(`extracted reference is missing method ${m}()`);
    }
  }
  // We re-implement the GA loop below (the reference's own run() is bound to
  // setInterval and React setState).  Assert the mechanics we replicate are
  // still the mechanics in the bundle, so a changed GA is caught here rather
  // than silently diverging from the trace fixture.
  const gaMarkers = [
    ['init inclusion probability', 'rnd() < 0.35 ? 1 : 0'],
    ['mutation rate', 'rnd() < 0.025'],
    ['tournament draw', 'Math.floor(rnd() * POP)'],
    ['uniform crossover', 'rnd() < 0.5 ? p1[i] : p2[i]'],
    ['elitism of two', 'const next = [scored[0].g, scored[1].g]'],
    ['descending sort', 'sort((a, b) => b.f - a.f)'],
    ['effort table', 'Fast: [50, 35], Standard: [90, 60], Exhaustive: [160, 110]'],
    ['lock force operator', 'const force = g => { lockedIdx.forEach(i => g[i] = 1); return g; };'],
    ['locks applied at initialisation', 'pop.push(force(Array.from({ length: N }'],
    ['locks applied to each child', 'next.push(force(c));'],
  ];
  for (const [what, marker] of gaMarkers) {
    if (!classBody.includes(marker)) fail(`extracted reference no longer contains the ${what} (${marker})`);
  }
  // And the model literals this tool mirrors in REF below.
  const modelMarkers = [
    ['model constants', 'const Y0 = 2027, N = 30, rate = 0.055, tenor = 18'],
    ['DSCR sizing basis', 'ebitda1 / 1.40 / af'],
    ['tax rate', 'taxable * 0.20'],
    ['depreciation life', 'p.capex / 25'],
    ['ramp factor', "(age === 0 ? 0.55 : 1)"],
    ['LCOE discount rate', 'Math.pow(1.06, t + 1)'],
    ['IRR bracket', 'let lo = -0.5, hi = 1.2'],
  ];
  for (const [what, marker] of modelMarkers) {
    if (!classBody.includes(marker)) fail(`extracted reference no longer contains the ${what} (${marker})`);
  }
}

const COMPONENT_HEADER = `// GENERATED by tools/extract_reference.mjs — do not edit.
//
// This file is ESM. The .mjs extension is deliberate: there is no package.json
// here, so a .js extension would leave the module classification to Node's
// syntax detection, which is version-dependent.
//
// The class body below is copied VERBATIM out of the \`text/x-dc\` script tag
// inside the __bundler/template payload of "${BUNDLE_NAME}"
// (sha256 ${BUNDLE_SHA256}).  Only the wrapper is ours: the reference declares
// \`class Component extends DCLogic\` against a global base class, and we take
// that base as a parameter instead so the module is importable.
//
// Regenerate with: node tools/extract_reference.mjs

// \`setInterval\`/\`clearInterval\` are taken as parameters rather than read from
// the global scope: the reference's run() schedules its GA on a timer, and
// passing stubs here lets us drive that loop synchronously and deterministically
// without editing a character of the reference's own source.
/**
 * @param {Function} DCLogic base class supplying \`this.props\` and \`this.setState\`
 * @param {Function} setInterval captures the GA step function
 * @param {Function} clearInterval signals the GA has finished
 */
export function defineComponent(DCLogic, setInterval, clearInterval) {
  return `;

const HARNESS_SOURCE = `// GENERATED by tools/extract_reference.mjs — do not edit.
//
// Minimal host for the extracted reference component.  The reference's render
// methods are never invoked, so no DOM or React shim is needed — only
// \`this.props\` (the model reads the exit multiple from it) and a synchronous
// \`setState\` (run() reads back \`this.state.gen\` and \`this.state.hist\`
// between GA generations).
//
// Regenerate with: node tools/extract_reference.mjs

import { defineComponent } from './component.mjs';

class DCLogic {
  constructor(props) {
    this.props = props;
    this.state = {};
  }

  // React's setState is asynchronous, but the reference's GA only reads state
  // between ticks, which is exactly what a synchronous shallow merge gives.
  setState(patch) {
    this.state = { ...this.state, ...(typeof patch === 'function' ? patch(this.state) : patch) };
  }

  forceUpdate() {}
}

export const DEFAULT_PROPS = Object.freeze({ algorithmEffort: 'Standard', exitMultiple: 9 });

/**
 * Build a fresh reference instance.  Always prefer a fresh instance over
 * mutating \`props\` on an existing one: projIrr() memoises onto the project
 * objects, and a fresh instance keeps those caches from crossing runs.
 *
 * The returned object carries a \`__timer\` handle holding the GA step function
 * that run() scheduled, and a \`__stopped\` flag set when run() calls
 * clearInterval.  driveGa() below uses both to pump the loop.
 */
export function createReference(props = DEFAULT_PROPS) {
  const timer = { step: null, stopped: false };
  const setInterval = (fn) => { timer.step = fn; timer.stopped = false; return 1; };
  const clearInterval = () => { timer.stopped = true; };
  const Component = defineComponent(DCLogic, setInterval, clearInterval);
  const instance = new Component({ ...DEFAULT_PROPS, ...props });
  instance.__timer = timer;
  return instance;
}

/**
 * Run the reference's OWN genetic algorithm to completion, deterministically.
 *
 * Two things stand between run() and a reproducible trace: it seeds from
 * \`Date.now()\`, and it advances on a 45 ms timer.  We replace the first by
 * overriding \`seeded\` on the instance AFTER construction (so buildPipeline has
 * already drawn its real per-project streams), and the second by pumping the
 * captured step function ourselves.  The reference's GA source is not touched.
 *
 * @param {object} instance from createReference()
 * @param {object} mandate  merged over the reference's default state
 * @param {number} seed     the PRNG seed to force in place of Date.now()
 * @param {Function} onGeneration called with the instance after each generation
 */
export function driveGa(instance, mandate, seed, onGeneration) {
  instance.setState({ ...instance.state, ...mandate });
  const seeded = Object.getPrototypeOf(instance).seeded;
  instance.seeded = () => seeded.call(instance, seed);

  instance.run();
  if (typeof instance.__timer.step !== 'function') {
    throw new Error('run() did not schedule a GA step — the reference has changed shape');
  }

  let guard = 0;
  while (!instance.__timer.stopped) {
    instance.__timer.step();
    if (onGeneration) onGeneration(instance);
    if (++guard > 100000) throw new Error('GA did not terminate');
  }
  return instance.state;
}
`;

let loadCounter = 0;

async function loadReference(outDir) {
  const classBody = extractClassBody(readBundle());
  assertReferenceIntegrity(classBody);

  const refDir = path.join(outDir, 'reference');
  fs.mkdirSync(refDir, { recursive: true });
  writeText(path.join(refDir, 'component.mjs'), `${COMPONENT_HEADER}${classBody};\n}\n`);
  writeText(path.join(refDir, 'harness.mjs'), HARNESS_SOURCE);

  // Import what we just wrote, so the committed artefact is the code that
  // produced the fixtures.  Cache-bust with a counter (never a timestamp — the
  // tool must contain no source of non-determinism at all) so --check picks up
  // the temp-dir copy rather than a cached one.
  const href = `${pathToFileURL(path.join(refDir, 'harness.mjs')).href}?v=${++loadCounter}`;
  return await import(href);
}

// ===========================================================================
// 2. REFERENCE CONSTANTS
//
// Mirrors of literals inside the reference's model().  Kept here as named
// constants so the emitted files can state their own assumptions; every one of
// them is asserted to still be present in the bundle by
// assertReferenceIntegrity() above, and the reconstruction below is checked
// against the reference's own arrays before anything is written.
// ===========================================================================

const REF = Object.freeze({
  firstYear: 2027,
  years: 30,
  debtRate: 0.055,
  debtTenorYears: 18,
  sizingDSCR: 1.40,
  taxRate: 0.20,
  depreciationYears: 25,
  rampYearFactor: 0.55,
  // Multipliers, used in the arithmetic exactly as the reference writes them.
  ppaEscalation: 1.005,
  merchantEscalation: 1.021,
  opexEscalation: 1.021,
  // The same escalators as RATES, which is how a file states them: schema §2,
  // "shares and rates are fractions of one, never percentages", and it is what
  // every other rate in the file already looks like (taxRate 0.2, debtRate
  // 0.055). Declared as literals rather than derived: `1.021 - 1` is
  // 0.020999999999999908, which is not the number anyone means.
  ppaEscalationRate: 0.005,
  merchantEscalationRate: 0.021,
  opexEscalationRate: 0.021,
  lcoeDiscountRate: 0.06,
  degradation: { Solar: 0.005, default: 0.002 },
  captureFactor: { Solar: 0.68, Wind: 0.88, 'Offshore wind': 0.86 },
  irr: { method: 'bisection', low: -0.5, high: 1.2, iterations: 70, firstPeriod: 1 },
});

const ANNUITY_FACTOR = REF.debtRate / (1 - Math.pow(1 + REF.debtRate, -REF.debtTenorYears));

// The rate form and the multiplier form must describe the same escalator.
for (const [rate, multiplier, what] of [
  [REF.ppaEscalationRate, REF.ppaEscalation, 'PPA'],
  [REF.merchantEscalationRate, REF.merchantEscalation, 'merchant'],
  [REF.opexEscalationRate, REF.opexEscalation, 'opex'],
]) {
  if (1 + rate !== multiplier) {
    throw new Error(`${what} escalation rate ${rate} does not match its multiplier ${multiplier}`);
  }
}

const degradationFor = (tech) => (tech === 'Solar' ? REF.degradation.Solar : REF.degradation.default);
const buildYearsFor = (p) => Math.max(1, p.cod - REF.firstYear);

// ===========================================================================
// 3. STATEMENT RECONSTRUCTION
//
// The reference stores only EBITDA, generation and signed equity cash flow per
// year.  Everything else in a statement file — revenue, achieved price, opex,
// depreciation, interest, principal, tax, the debt schedule and the PP&E
// roll-forward — is recomputed here from the same inputs and the same formulas,
// then asserted against the reference's own arrays before use.
//
// The construction-funding schedule is NOT ours to choose: docs/pipeline-schema
// §6 fixes it, because the rendering changes FCFE timing and therefore every
// IRR.  Equity funds the construction period pro rata; debt is drawn in one go
// at COD; an already-operating project books everything in year one.  That
// makes `capex[t] = debtDrawdown[t] + equityDrawdown[t]` true in every year,
// and because `−capex + debtDrawdown = −equityDrawdown` the signed FCFE it
// produces is byte-identical to the reference's own `cfEquity` — asserted with
// Object.is, all 48 × 30 values.
//
// The reference books no interest during construction, so no IDC is
// capitalised.  Interest in the COD year accrues on `opening + drawdown`,
// because that is the year the facility is drawn.
// ===========================================================================

function buildSeries(p) {
  const N = REF.years;
  const deg = degradationFor(p.tech);
  const buildYears = buildYearsFor(p);
  const equity = p.capex - p.debt;
  const annuity = p.debt * ANNUITY_FACTOR;
  const depreciationCharge = p.capex / REF.depreciationYears;
  const alreadyOperating = p.cod <= REF.firstYear;

  const s = {
    year: [], age: [],
    generationGwh: [], achievedPrice: [],
    revenue: [], opex: [], ebitda: [], depreciation: [], ebit: [],
    interestExpense: [], pbt: [], taxExpense: [], netIncome: [],
    interestPaid: [], debtRepayment: [], taxPaid: [],
    capex: [], debtDrawdown: [], equityDrawdown: [], fcfe: [],
    debtOpening: [], debtDraw: [], debtRepay: [], debtClosing: [],
    ppe: [],
    dscr: [],
  };

  let closing = 0;
  let ppe = 0;

  for (let t = 0; t < N; t++) {
    const year = REF.firstYear + t;
    const age = year - p.cod;
    const inDebtLife = age >= 0 && age < REF.debtTenorYears;

    // ── funding, per docs/pipeline-schema §6 ──────────────────────────────
    let capex = 0, equityDrawdown = 0, debtDrawdown = 0;
    if (alreadyOperating) {
      if (t === 0) { capex = p.capex; equityDrawdown = equity; debtDrawdown = p.debt; }
    } else if (year < p.cod) {
      capex = equity / buildYears;
      equityDrawdown = equity / buildYears;
    } else if (year === p.cod) {
      capex = p.debt;
      debtDrawdown = p.debt;
    }

    // ── operations ────────────────────────────────────────────────────────
    let generationGwh = 0, achievedPrice = 0, revenue = 0, opex = 0, ebitda = 0;
    let depreciation = 0, interest = 0, repayment = 0, tax = 0;

    const opening = closing;
    if (age >= 0) {
      generationGwh = p.mw * 8.760 * p.cf * Math.pow(1 - deg, age) * (age === 0 ? REF.rampYearFactor : 1);
      const underPpa = p.ppaTenor > 0 && age < p.ppaTenor;
      achievedPrice = underPpa
        ? p.ppaShare * p.ppaPrice * Math.pow(REF.ppaEscalation, age)
          + (1 - p.ppaShare) * p.merchant * Math.pow(REF.merchantEscalation, age)
        : p.merchant * Math.pow(REF.merchantEscalation, age);
      revenue = generationGwh * 1000 * achievedPrice / 1e6;
      opex = p.mw * 1000 * p.opexKw * Math.pow(REF.opexEscalation, age) / 1e6;
      ebitda = revenue - opex;
      // Interest accrues on the balance after that year's drawdown: at COD the
      // facility is drawn and carries a full year's interest.
      interest = inDebtLife ? (opening + debtDrawdown) * REF.debtRate : 0;
      repayment = inDebtLife ? Math.max(0, annuity - interest) : 0;
      depreciation = age < REF.depreciationYears ? depreciationCharge : 0;
      // No tax-loss carryforward: the reference leaves a loss year untaxed and
      // never relieves it later (decision 1C-3).
      tax = Math.max(0, (ebitda - interest - depreciation) * REF.taxRate);
    }

    closing = opening - repayment + debtDrawdown;
    ppe = ppe - depreciation + capex;

    const ebit = ebitda - depreciation;
    const pbt = ebit - interest;
    const netIncome = pbt - tax;
    // §7.3 states this as `ebitda - interestPaid - debtRepayment - taxPaid
    // - capex + debtDrawdown`, and §6 notes that `-capex + debtDrawdown` is
    // exactly `-equityDrawdown`. We use the equityDrawdown form because it is
    // what the reference accumulates: the §7.3 form subtracts the whole
    // facility and adds it straight back in the COD year, which is
    // algebraically nil but numerically lossy — it costs the low bits of a
    // small result. Computed this way, fcfe is bit-identical to the
    // reference's own series (asserted with Object.is), and §7.3's form still
    // holds to ~1e-15, far inside its EUR 0.01m tolerance.
    const fcfe = ebitda - interest - repayment - tax - equityDrawdown;

    s.year.push(year);
    s.age.push(age);
    s.generationGwh.push(generationGwh);
    s.achievedPrice.push(achievedPrice);
    s.revenue.push(revenue);
    s.opex.push(opex);
    s.ebitda.push(ebitda);
    s.depreciation.push(depreciation);
    s.ebit.push(ebit);
    s.interestExpense.push(interest);
    s.pbt.push(pbt);
    s.taxExpense.push(tax);
    s.netIncome.push(netIncome);
    s.interestPaid.push(interest);
    s.debtRepayment.push(repayment);
    s.taxPaid.push(tax);
    s.capex.push(capex);
    s.debtDrawdown.push(debtDrawdown);
    s.equityDrawdown.push(equityDrawdown);
    s.fcfe.push(fcfe);
    s.debtOpening.push(opening);
    s.debtDraw.push(debtDrawdown);
    s.debtRepay.push(repayment);
    s.debtClosing.push(closing);
    s.ppe.push(ppe);

    // §5.7: non-null EXACTLY in the debt life, including the ramp year.
    if (!inDebtLife) {
      s.dscr.push(null);
    } else {
      const service = interest + repayment;
      if (service <= 0) fail(`${p.id} year ${year}: in the debt life but no debt service, so DSCR is undefined where the schema requires a value`);
      s.dscr.push(ebitda / service);
    }
  }

  return s;
}

// Minimum DSCR over the debt life, excluding the ramp year.
//
// The binding year is NOT always the first full year: contracted revenue
// escalates at 0.5% while solar generation degrades at 0.5% and opex escalates
// at 2.1%, so EBITDA declines through the PPA period for high-PPA-share solar
// and then steps up at rolloff.  Measured across the 48: age 1 binds for 34,
// and ages 9, 11, 14 or 17 bind for the other 14.
//
// This is a REDUCTION over `ratios.dscr` with a documented exclusion, so per
// docs/pipeline-schema §9 it belongs with the consumer and must not be stored
// in a file.  It is emitted in derived_expectations.json instead.
function minDscrFrom(series) {
  let min = Infinity;
  let bindingAge = null;
  for (let t = 0; t < series.year.length; t++) {
    const age = series.age[t];
    if (age <= 0 || age >= REF.debtTenorYears) continue;
    if (series.dscr[t] === null) continue;
    if (series.dscr[t] < min) { min = series.dscr[t]; bindingAge = age; }
  }
  return Number.isFinite(min) ? { raw: min, bindingAge } : { raw: null, bindingAge: null };
}

const REF_MIN_DSCR_CAP = 3.2;
const referenceMinDscr = (raw) => Math.round(Math.min(raw, REF_MIN_DSCR_CAP) * 100) / 100;

// Entry-pricing bands the reference clamps capex/kW into.  25 of the 48 sit on
// the floor and 3 on the cap, so "capex falls out of the revenue case" is only
// literally true for the other 20.
const CAPEX_BANDS = { Solar: [560, 950], Wind: [1050, 1700], 'Offshore wind': [2200, 3400] };

function capexClampOf(p) {
  const [lo, hi] = CAPEX_BANDS[p.tech];
  if (Math.abs(p.capexKw - lo) < 1e-9) return 'floor';
  if (Math.abs(p.capexKw - hi) < 1e-9) return 'cap';
  return 'none';
}

// Debt is the lesser of a gearing cap and a DSCR sculpt.  27 capped, 21 sculpted.
const debtSizingBasisOf = (p) =>
  Math.abs(p.lev - p.maxGear) < 1e-12 ? 'max-gearing-cap' : 'dscr-sculpt';

// ===========================================================================
// 4. THE SCHEMA BOUNDARY
//
// ***  This section is the ONLY place the emitted file shape is decided.  ***
//
// The shape is `templates/project-template.json` and the rules are
// `docs/pipeline-schema.md`, both owned by issue 1B (#3).  Nothing above or
// below this section mentions a template key.
//
// Three of that schema's rules drive everything here:
//
//  * §3 — "Unknown keys at any level are a validation error, not a warning."
//    The template is CLOSED.  1C emits exactly its keys and nothing else; the
//    richer balance sheet and extra ratios 1C used to carry are not smuggled in
//    as extensions, they move to derived_expectations.json, which is 1C's own
//    oracle rather than a pipeline file.
//
//  * §9 — the reject-derived-fields rule.  A file carrying `minDscr`, `lcoe`,
//    `gearing`, `equity`, `capexPerKw`, a scalar `capex`, or any of the
//    mandate-dependent names is REJECTED AT LOAD.  Every one of those is one
//    arithmetic step from a field that is present, and two sources for one
//    number is one source too many.
//
//  * §6 — the construction-funding convention, applied in buildSeries above.
//
// The units are §2's: €m, GWh, MW, €/MWh, €/kW/year, and shares as fractions
// of one.  Costs are positive magnitudes; only `fcfe` is signed.
// ===========================================================================

// ---------------------------------------------------------------------------
// The project-file contract is SETTLED: `reconciled` — 1B's technology enum
// spelling with 1A's five `assumptions` fields.  1C recommended it in 1C-20 and
// 1A adopted it in full, reversing its own C-1:
//   https://github.com/JanSchm/TerraFolio/issues/1#issuecomment-5766800812
//
// That comment left one action outstanding — "`DEFAULT_CONTRACT = 'reconciled'`
// in tools/extract_reference.mjs, then regenerate" — which #4 closed without
// doing, leaving all 48 committed fixtures and 1B's own template failing 1A's
// pydantic ProjectFile 48/48 on the five missing `assumptions` fields.  It
// blocked 2A's first acceptance criterion and 2C's parity test alike, and both
// applied it independently: 2A (#6) flipped DEFAULT_CONTRACT and regenerated,
// and 2C (#8) did the same and also brought templates/project-template.json up
// to the settled contract, which is what TEMPLATE_CONTRACT below now reflects.
// The regenerated fixtures were byte-identical, which is the point of a
// deterministic extractor.
//
// The parameterisation stays.  `--contract=<name>` still selects one, so the
// superseded shapes remain reproducible and the next contract change is again
// one constant rather than a rewrite.
// ---------------------------------------------------------------------------

const STAGE = { Greenfield: 'greenfield', 'Ready-to-build': 'ready_to_build', Construction: 'construction' };

const CONTRACTS = {
  // docs/pipeline-schema.md §4.2 / templates/project-template.json. The epic
  // calls that document Normative and puts docs/ and templates/ in 1B's
  // ownership row, so it is the default until the epic says otherwise.
  'pipeline-schema-1.0': {
    describe: "1B's docs/pipeline-schema.md §4.2 and templates/project-template.json",
    technology: { Solar: 'solar', Wind: 'onshore_wind', 'Offshore wind': 'offshore_wind' },
    assumptionsExtras: false,
  },
  // 1A's src/terrafolio/domain/project_file.py, which is what actually rejects
  // a file at load. Its five extra assumptions are real reference values, not
  // padding: a validator that wants to REPRODUCE the physicals rather than
  // take them on trust needs the degradation rate and the three escalators,
  // and targetDscr records the sizing basis epic §6.3 fixes.
  'domain-model-1a': {
    describe: "1A's pydantic ProjectFile in src/terrafolio/domain/project_file.py",
    technology: { Solar: 'solar_pv', Wind: 'onshore_wind', 'Offshore wind': 'offshore_wind' },
    assumptionsExtras: true,
  },
  // What 1C recommends the epic settle on: 1B's spelling, 1A's fields.
  'reconciled': {
    describe: "1C's recommendation on #1 — 1B's enum spelling with 1A's five assumptions",
    technology: { Solar: 'solar', Wind: 'onshore_wind', 'Offshore wind': 'offshore_wind' },
    assumptionsExtras: true,
  },
};

const DEFAULT_CONTRACT = 'reconciled';

// The contract that templates/project-template.json describes. The closed-shape
// check is 1B's oracle, so it governs that contract and no other. #8 added the
// five settled `assumptions` fields to that template, so it now describes
// `reconciled` and the check runs against the default again.
const TEMPLATE_CONTRACT = 'reconciled';

let CONTRACT = CONTRACTS[DEFAULT_CONTRACT];

const contractNameOf = (c) => Object.keys(CONTRACTS).find((k) => CONTRACTS[k] === c);

function selectContract(name) {
  if (!Object.prototype.hasOwnProperty.call(CONTRACTS, name)) {
    fail(`unknown contract '${name}'; known: ${Object.keys(CONTRACTS).join(', ')}`);
  }
  CONTRACT = CONTRACTS[name];
  return name;
}

// Fixed, never `new Date()`: the emitted bytes must be identical on every
// machine and in every CI image. This is the date 1C first extracted the
// fixtures, not a claim about an analyst.
const PREPARED_ON = '2026-09-21';

// The schema's §4.3 states `capture price = countryBaseloadPrice × captureFactor`.
// The reference ROUNDS its capture price to a whole €/MWh
// (`Math.round(baseload × captureFactor)`), so emitting the nominal factor
// (0.68 solar, 0.88 onshore, 0.86 offshore) would leave that identity false by
// up to ~1.1% — and the achieved price, and therefore revenue, is built on the
// ROUNDED figure. We emit the effective factor, so the identity holds exactly
// and a consumer deriving the capture price from it reproduces the revenue in
// the file. The nominal factor is recorded in derived_expectations.json.
const effectiveCaptureFactor = (p) => p.merchant / p.baseload;

// docs/pipeline-schema §8. The earlier the stage, the more of a file is
// prediction; these seven groups say which parts. The bases below follow what
// the reference actually models — it varies grid, O&M, contracted share and
// entry pricing by stage — and every note states plainly that the figures come
// from the JavaScript reference rather than from an analyst.
function provenanceFor(p) {
  const stage = p.stage;
  const construction = stage === 'Construction';
  const greenfield = stage === 'Greenfield';

  const generation = greenfield
    ? { estimateBasis: 'benchmark', confidence: 'low', note: 'Country-level resource benchmark with a project-specific adjustment; no site measurement. Produced by the JavaScript reference model.' }
    : { estimateBasis: 'engineering_estimate', confidence: construction ? 'high' : 'medium', note: 'P50 net capacity factor from the reference model\'s country resource table, adjusted per project. Produced by the JavaScript reference model.' };

  const price = p.ppaTenor > 0
    ? { estimateBasis: 'contracted', confidence: p.ppaShare >= 0.5 ? 'high' : 'medium', note: `${Math.round(p.ppaShare * 100)}% of revenue under a ${p.ppaTenor}-year PPA; the merchant tail is the reference model's capture price against the country baseload curve.` }
    : { estimateBasis: 'internal_model', confidence: 'low', note: 'Fully merchant; revenue is the reference model\'s capture price against the country baseload curve.' };

  const capex = construction
    ? { estimateBasis: 'binding_offer', confidence: 'medium', note: 'Entry pricing at a construction-stage EBITDA yield. Produced by the JavaScript reference model.' }
    : greenfield
      ? { estimateBasis: 'internal_model', confidence: 'low', note: 'Entry pricing at a greenfield EBITDA yield, clamped to the technology cost band. Produced by the JavaScript reference model.' }
      : { estimateBasis: 'benchmark', confidence: 'medium', note: 'Entry pricing at a ready-to-build EBITDA yield, clamped to the technology cost band. Produced by the JavaScript reference model.' };

  return {
    preparedBy: 'tools/extract_reference.mjs',
    preparedOn: PREPARED_ON,
    modelVersion: `js-reference@${BUNDLE_SHA256.slice(0, 12)}`,
    fields: {
      generation,
      price,
      capex,
      opex: { estimateBasis: 'benchmark', confidence: 'medium', note: 'Technology opex benchmark in base-year euros, escalated at 2.1%. Produced by the JavaScript reference model.' },
      debtTerms: construction
        ? { estimateBasis: 'binding_offer', confidence: 'medium', note: 'Sized to a 1.40x base-case DSCR on stabilised first-full-year EBITDA, then capped by the stage gearing ceiling. Produced by the JavaScript reference model.' }
        : { estimateBasis: 'internal_model', confidence: greenfield ? 'low' : 'medium', note: 'Sized to a 1.40x base-case DSCR on stabilised first-full-year EBITDA, then capped by the stage gearing ceiling. Produced by the JavaScript reference model.' },
      grid: p.gridSecured
        ? { estimateBasis: 'contracted', confidence: 'high', note: 'Firm connection agreement in place.' }
        : { estimateBasis: 'placeholder', confidence: 'low', note: 'Connection application pending; no firm capacity yet.' },
      om: p.omPartner
        ? { estimateBasis: 'contracted', confidence: 'high', note: 'Long-term full-scope service agreement signed.' }
        : { estimateBasis: 'benchmark', confidence: 'low', note: 'No service agreement contracted; opex carried at benchmark.' },
    },
  };
}

function toProjectFile(p, series) {
  return {
    schemaVersion: SCHEMA_VERSION,
    id: p.id,
    name: p.name,
    location: {
      country: p.country,
      countryCode: p.cc,
      iso3: p.iso3,
      lat: p.lat,
      lon: p.lon,
    },
    asset: {
      technology: CONTRACT.technology[p.tech],
      stage: STAGE[p.stage],
      capacityMw: p.mw,
      codYear: p.cod,
      netCapacityFactor: p.cf,
      opexPerKwYear: p.opexKw,
    },
    revenue: {
      ppaShare: p.ppaShare,
      ppaPrice: p.ppaPrice,
      ppaTenorYears: p.ppaTenor,
      countryBaseloadPrice: p.baseload,
      captureFactor: effectiveCaptureFactor(p),
    },
    execution: {
      developmentRiskScore: p.devRisk,
      gridSecured: p.gridSecured,
      omContracted: p.omPartner,
      // §4.4: the currency revenue is earned in, which drives the EUR-only
      // screen. NOT the denomination of the statements, which are always euros.
      currency: p.ccy,
    },
    capitalStructure: {
      totalCapex: p.capex,
      seniorDebt: p.debt,
      maxGearing: p.maxGear,
    },
    assumptions: {
      baseYear: REF.firstYear,
      taxRate: REF.taxRate,
      depreciationYears: REF.depreciationYears,
      debtRate: REF.debtRate,
      debtTenorYears: REF.debtTenorYears,
      // Present only under a contract that asks for them. Every value is the
      // reference's own: they are not invented to satisfy a model.
      ...(CONTRACT.assumptionsExtras ? {
        degradationRate: degradationFor(p.tech),
        priceEscalation: REF.ppaEscalationRate,
        merchantEscalation: REF.merchantEscalationRate,
        opexEscalation: REF.opexEscalationRate,
        targetDscr: REF.sizingDSCR,
      } : {}),
    },
    statements: {
      years: series.year,
      physicals: {
        generationGwh: series.generationGwh,
        achievedPrice: series.achievedPrice,
      },
      incomeStatement: {
        revenue: series.revenue,
        opex: series.opex,
        ebitda: series.ebitda,
        depreciation: series.depreciation,
        ebit: series.ebit,
        interestExpense: series.interestExpense,
        pbt: series.pbt,
        taxExpense: series.taxExpense,
        netIncome: series.netIncome,
      },
      cashFlow: {
        interestPaid: series.interestPaid,
        debtRepayment: series.debtRepayment,
        taxPaid: series.taxPaid,
        capex: series.capex,
        debtDrawdown: series.debtDrawdown,
        equityDrawdown: series.equityDrawdown,
        fcfe: series.fcfe,
      },
      debtSchedule: {
        opening: series.debtOpening,
        drawdown: series.debtDraw,
        repayment: series.debtRepay,
        closing: series.debtClosing,
      },
      balanceSheet: {
        ppe: series.ppe,
      },
      ratios: {
        dscr: series.dscr,
      },
    },
    provenance: provenanceFor(p),
  };
}

// ---------------------------------------------------------------------------
// Tie-outs — docs/pipeline-schema §7, one entry per rule in that table.
// Tolerance is §7's: €0.01m absolute or 0.1% relative, whichever is looser.
// ---------------------------------------------------------------------------

const ABS_TOL = 0.01;   // €m
const REL_TOL = 0.001;  // 0.1%

function within(a, b) {
  const d = Math.abs(a - b);
  return d <= ABS_TOL || d <= REL_TOL * Math.max(Math.abs(a), Math.abs(b));
}

const sum = (xs) => xs.reduce((a, x) => a + x, 0);

function worst(pairs) {
  let w = 0;
  for (const [a, b] of pairs) {
    if (!within(a, b)) return { ok: false, residual: Math.abs(a - b) };
    w = Math.max(w, Math.abs(a - b));
  }
  return { ok: true, residual: w };
}

const zip = (xs, f) => xs.map((_, i) => f(i));

// Every tie-out is checkable against an emitted file alone: no private keys,
// nothing carried over from the reference.
const agesOf = (f) => f.statements.years.map((y) => y - f.asset.codYear);

const TIE_OUTS = [
  // §7.1 Income statement
  { name: '7.1 ebitda = revenue - opex', check: (f) => {
    const i = f.statements.incomeStatement;
    return worst(zip(i.revenue, (t) => [i.ebitda[t], i.revenue[t] - i.opex[t]])); } },

  { name: '7.1 ebit = ebitda - depreciation', check: (f) => {
    const i = f.statements.incomeStatement;
    return worst(zip(i.ebit, (t) => [i.ebit[t], i.ebitda[t] - i.depreciation[t]])); } },

  { name: '7.1 pbt = ebit - interestExpense', check: (f) => {
    const i = f.statements.incomeStatement;
    return worst(zip(i.pbt, (t) => [i.pbt[t], i.ebit[t] - i.interestExpense[t]])); } },

  { name: '7.1 netIncome = pbt - taxExpense', check: (f) => {
    const i = f.statements.incomeStatement;
    return worst(zip(i.netIncome, (t) => [i.netIncome[t], i.pbt[t] - i.taxExpense[t]])); } },

  // §7.2 Revenue ties to physicals
  { name: '7.2 revenue = generationGwh x 1000 x achievedPrice / 1e6', check: (f) => {
    const i = f.statements.incomeStatement, ph = f.statements.physicals;
    return worst(zip(i.revenue, (t) => [i.revenue[t], ph.generationGwh[t] * 1000 * ph.achievedPrice[t] / 1e6])); } },

  { name: '7.2 opex = capacityMw x 1000 x opexPerKwYear / 1e6, escalated', check: (f) => {
    const i = f.statements.incomeStatement, age = agesOf(f);
    const base = f.asset.capacityMw * 1000 * f.asset.opexPerKwYear / 1e6;
    return worst(zip(i.opex, (t) => [i.opex[t], age[t] < 0 ? 0 : base * Math.pow(REF.opexEscalation, age[t])])); } },

  // §7.3 Cash flow
  { name: '7.3 fcfe = ebitda - interestPaid - debtRepayment - taxPaid - capex + debtDrawdown', check: (f) => {
    const c = f.statements.cashFlow, i = f.statements.incomeStatement;
    return worst(zip(c.fcfe, (t) => [
      c.fcfe[t],
      i.ebitda[t] - c.interestPaid[t] - c.debtRepayment[t] - c.taxPaid[t] - c.capex[t] + c.debtDrawdown[t],
    ])); } },

  // §7.4 Debt schedule
  { name: '7.4 closing = opening - repayment + drawdown', check: (f) => {
    const d = f.statements.debtSchedule;
    return worst(zip(d.closing, (t) => [d.closing[t], d.opening[t] - d.repayment[t] + d.drawdown[t]])); } },

  { name: '7.4 opening[t] = closing[t-1], opening[0] = 0', check: (f) => {
    const d = f.statements.debtSchedule;
    return worst(zip(d.opening, (t) => [d.opening[t], t === 0 ? 0 : d.closing[t - 1]])); } },

  { name: '7.4 closing[last] = 0 (fully amortised)', check: (f) => {
    const d = f.statements.debtSchedule;
    return worst([[d.closing[d.closing.length - 1], 0]]); } },

  // §7.5 Funding
  { name: '7.5 sum debtDrawdown = seniorDebt', check: (f) =>
    worst([[sum(f.statements.cashFlow.debtDrawdown), f.capitalStructure.seniorDebt]]) },

  { name: '7.5 sum equityDrawdown = totalCapex - seniorDebt', check: (f) =>
    worst([[sum(f.statements.cashFlow.equityDrawdown), f.capitalStructure.totalCapex - f.capitalStructure.seniorDebt]]) },

  { name: '7.5 sum capex = totalCapex', check: (f) =>
    worst([[sum(f.statements.cashFlow.capex), f.capitalStructure.totalCapex]]) },

  { name: '7.5 capex[t] = debtDrawdown[t] + equityDrawdown[t]', check: (f) => {
    const c = f.statements.cashFlow;
    return worst(zip(c.capex, (t) => [c.capex[t], c.debtDrawdown[t] + c.equityDrawdown[t]])); } },

  // §7.6 Depreciation and PP&E
  { name: '7.6 ppe[t] = ppe[t-1] - depreciation[t] + capex[t]', check: (f) => {
    const b = f.statements.balanceSheet, i = f.statements.incomeStatement, c = f.statements.cashFlow;
    return worst(zip(b.ppe, (t) => [b.ppe[t], (t === 0 ? 0 : b.ppe[t - 1]) - i.depreciation[t] + c.capex[t]])); } },

  { name: '7.6 sum depreciation = totalCapex - ppe[last]', check: (f) => {
    const b = f.statements.balanceSheet;
    return worst([[sum(f.statements.incomeStatement.depreciation), f.capitalStructure.totalCapex - b.ppe[b.ppe.length - 1]]]); } },

  // §7.7 DSCR
  { name: '7.7 dscr = ebitda / (interestPaid + debtRepayment)', check: (f) => {
    const r = f.statements.ratios, c = f.statements.cashFlow, i = f.statements.incomeStatement;
    const pairs = [];
    for (let t = 0; t < r.dscr.length; t++) {
      if (r.dscr[t] === null) continue;
      pairs.push([r.dscr[t], i.ebitda[t] / (c.interestPaid[t] + c.debtRepayment[t])]);
    }
    return worst(pairs); } },

  // §7.8 Shape
  { name: '7.8 every series has exactly 30 elements', check: (f) => {
    const st = f.statements;
    const series = [st.years, ...Object.values(st.physicals), ...Object.values(st.incomeStatement),
      ...Object.values(st.cashFlow), ...Object.values(st.debtSchedule),
      ...Object.values(st.balanceSheet), ...Object.values(st.ratios)];
    for (const arr of series) if (arr.length !== REF.years) return { ok: false, residual: arr.length };
    return { ok: true, residual: 0 }; } },

  { name: '7.8 years = [baseYear .. baseYear + 29], contiguous ascending', check: (f) =>
    worst(zip(f.statements.years, (t) => [f.statements.years[t], f.assumptions.baseYear + t])) },

  { name: '7.8 no nulls anywhere except ratios.dscr', check: (f) => {
    let bad = 0;
    const walk = (o, key) => {
      if (o === null) { if (key !== 'dscr') bad++; return; }
      if (Array.isArray(o)) { for (const v of o) walk(v, key); return; }
      if (o && typeof o === 'object') { for (const [k, v] of Object.entries(o)) walk(v, k); }
    };
    walk(f, null);
    return { ok: bad === 0, residual: bad }; } },

  { name: '7.8 dscr non-null exactly where 0 <= year - codYear < debtTenorYears', check: (f) => {
    const age = agesOf(f), r = f.statements.ratios;
    let bad = 0;
    for (let t = 0; t < r.dscr.length; t++) {
      const shouldHave = age[t] >= 0 && age[t] < f.assumptions.debtTenorYears;
      if (shouldHave !== (r.dscr[t] !== null)) bad++;
    }
    return { ok: bad === 0, residual: bad }; } },

  { name: '7.8 no unknown keys and no missing keys, at any level', check: (f) => {
    // Skipped, loudly, when templates/project-template.json is not present —
    // it lands with #3. summarise() says which of the two happened; it must
    // never look like a pass that did not run.
    if (TEMPLATE_SHAPE === null) return { ok: true, residual: 0, skipped: true };
    const diffs = shapeDiff(f, TEMPLATE_SHAPE, '');
    return { ok: diffs.length === 0, residual: diffs.length, detail: diffs.slice(0, 8) }; } },

  // §9 The reject-derived-fields rule
  { name: '9 carries none of the reject-derived field names', check: (f) => {
    const banned = new Set(['irr', 'moic', 'terminalValue', 'exitValue', 'payback', 'paybackYear',
      'minDscr', 'lcoe', 'leverage', 'gearing', 'equity', 'capexPerKw', 'cashflowSchedule']);
    const hits = [];
    const walk = (o, path) => {
      if (!o || typeof o !== 'object') return;
      if (Array.isArray(o)) return;
      for (const [k, v] of Object.entries(o)) {
        if (banned.has(k)) hits.push(`${path}.${k}`);
        // A top-level scalar named `capex` is rejected too (A-2): `capex` is
        // the annual cash-flow line, `totalCapex` is the project total.
        if (k === 'capex' && typeof v === 'number') hits.push(`${path}.${k} (scalar)`);
        walk(v, `${path}.${k}`);
      }
    };
    walk(f, '');
    return { ok: hits.length === 0, residual: hits.length, detail: hits }; } },
];

// The template is closed (§3), so conformance is a two-way set comparison
// against 1B's own templates/project-template.json rather than a spot check.
let TEMPLATE_SHAPE = null;

function loadTemplateShape() {
  const file = path.join(ROOT, 'templates', 'project-template.json');
  if (!fs.existsSync(file)) return null;
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

// Compare key sets structurally: same keys at every object level, same
// leaf-vs-object kind. Array contents are not compared, only that both sides
// agree a path is an array.
function shapeDiff(actual, expected, path) {
  const out = [];
  if (expected === null || typeof expected !== 'object' || Array.isArray(expected)) {
    const bothArrays = Array.isArray(expected) === Array.isArray(actual);
    if (!bothArrays) out.push(`${path}: expected ${Array.isArray(expected) ? 'array' : 'scalar'}`);
    return out;
  }
  if (actual === null || typeof actual !== 'object' || Array.isArray(actual)) {
    out.push(`${path}: expected an object`);
    return out;
  }
  for (const k of Object.keys(expected)) {
    if (!(k in actual)) out.push(`${path}.${k}: missing`);
    else out.push(...shapeDiff(actual[k], expected[k], `${path}.${k}`));
  }
  for (const k of Object.keys(actual)) {
    if (!(k in expected)) out.push(`${path}.${k}: unknown key`);
  }
  return out;
}

// ===========================================================================
// 5. THE ORACLE FIXTURES
//
// Everything here CALLS the reference rather than reimplementing it.  The two
// exceptions are the PRNG consumption map and the GA's initial population,
// which the reference does not expose; both are reconstructed and then proved
// correct against values the reference did produce, so a wrong reconstruction
// aborts the run instead of shipping a wrong golden.
// ===========================================================================

const PRNG_STREAM_LENGTH = 1000;
const PRNG_PROJECT_PREVIEW = 16;
const GA_SEED = 42;
const HOLD_YEARS = 10;
const EXIT_MULTIPLE_BASES = [8.5, 9, 9.5];

// The reference's per-technology exit multiple, as holdCf() applies it.
const exitMultipleFor = (tech, base) => (tech === 'Solar' ? base : tech === 'Wind' ? base - 0.5 : base + 0.5);

// How many draws buildPipeline() consumes for a project.  Two of the draws are
// short-circuited by stage, and one by ppaShare, so the count is 7, 8 or 9 —
// a reimplementation that draws unconditionally diverges from project 1 on.
function prngDrawPlan(p) {
  const plan = [
    { field: 'cf', drawn: true },
    { field: 'opexKw', drawn: true },
    { field: 'devRisk', drawn: true },
    { field: 'gridSecured', drawn: p.stage === 'Greenfield' },
    { field: 'omPartner', drawn: p.stage !== 'Construction' },
    { field: 'ppaShare', drawn: true },
    { field: 'ppaPrice', drawn: true },
    { field: 'ppaTenor', drawn: p.ppaShare > 0.05 },
    { field: 'yieldTarget', drawn: true },
  ];
  return { plan, consumed: plan.filter((x) => x.drawn).length };
}

// Replay a project's own seeded stream through the draw plan and check the
// values it produces are the ones the reference actually stored.  If this
// passes for all 48, the recorded consumption order is right.
function assertPrngPlan(ref, projects) {
  const cfSolarLike = (p, draw) => {
    if (p.tech === 'Offshore wind') return 0.49 + draw * 0.06;
    return null; // solar/onshore need country tables we deliberately do not copy
  };
  for (let i = 0; i < projects.length; i++) {
    const p = projects[i];
    const rnd = ref.seeded(i * 97 + 13);
    const draws = [];
    const { plan, consumed } = prngDrawPlan(p);
    for (const step of plan) draws.push(step.drawn ? rnd() : null);

    // Offshore capacity factor is the one field derivable without copying the
    // reference's country tables; it pins draw 0.
    const cf = cfSolarLike(p, draws[0]);
    if (cf !== null && Math.abs(cf - p.cf) > 1e-12) {
      fail(`PRNG draw plan is wrong for ${p.id}: reconstructed cf ${cf} != ${p.cf}`);
    }
    // devRisk is draw 2 and is a pure function of stage, technology and that draw.
    const riskBase = p.stage === 'Greenfield' ? 3.4 : p.stage === 'Ready-to-build' ? 2.2 : 1.5;
    const devRisk = Math.min(5, Math.max(1, Math.round((riskBase + (p.tech === 'Offshore wind' ? 0.8 : 0) + draws[2] * 1.2) * 10) / 10));
    if (Math.abs(devRisk - p.devRisk) > 1e-12) {
      fail(`PRNG draw plan is wrong for ${p.id}: reconstructed devRisk ${devRisk} != ${p.devRisk}`);
    }
    // gridSecured / omPartner pin the two short-circuits.
    const gridSecured = p.stage !== 'Greenfield' ? true : draws[3] > 0.45;
    const omPartner = p.stage === 'Construction' ? true : draws[4] > 0.35;
    if (gridSecured !== p.gridSecured || omPartner !== p.omPartner) {
      fail(`PRNG draw plan is wrong for ${p.id}: short-circuited draws do not reproduce gridSecured/omPartner`);
    }
    if (consumed < 7 || consumed > 9) fail(`unexpected draw count ${consumed} for ${p.id}`);
  }
}

function buildPrngFixture(ref, projects) {
  assertPrngPlan(ref, projects);

  const stream = (seed, n) => {
    const rnd = ref.seeded(seed);
    const out = [];
    for (let i = 0; i < n; i++) out.push(rnd());
    return out;
  };

  return {
    $schemaId: 'terrafolio/js-prng/v1',
    algorithm: {
      name: 'xorshift32',
      seeding: 'x = seed >>> 0 || 1   (seed 0 becomes 1)',
      step: 'x ^= x << 13; x ^= x >>> 17; x ^= x << 5; x >>>= 0',
      output: 'x / 4294967296',
      note: 'All shifts and xors are 32-bit. `x << 13` is signed 32-bit in JS; the unsigned coercion happens once, at the end of the step, before the division. A port that coerces at every shift produces a different stream.',
    },
    streams: [
      { seed: GA_SEED, count: PRNG_STREAM_LENGTH, draws: stream(GA_SEED, PRNG_STREAM_LENGTH) },
      { seed: 0, count: 8, note: 'seed 0 is coerced to 1 by `s >>> 0 || 1`', draws: stream(0, 8) },
      { seed: 1, count: 8, draws: stream(1, 8) },
    ],
    // buildPipeline() seeds a fresh stream per project as `i * 97 + 13`, and
    // consumes a branch-dependent number of draws from it.
    projectSeeds: projects.map((p, i) => {
      const { plan, consumed } = prngDrawPlan(p);
      return {
        index: i,
        id: p.id,
        seed: i * 97 + 13,
        drawsConsumed: consumed,
        drawPlan: plan,
        draws: stream(i * 97 + 13, PRNG_PROJECT_PREVIEW),
      };
    }),
  };
}

// ---------------------------------------------------------------------------

function buildDerivedExpectations(makeRef, projects, seriesById) {
  const byBase = {};
  for (const base of EXIT_MULTIPLE_BASES) {
    const r = makeRef({ exitMultiple: base });
    byBase[base] = {
      ref: r,
      byId: Object.fromEntries(r.projects.map((p) => [p.id, p])),
    };
  }

  return {
    $schemaId: 'terrafolio/derived-expectations/v1',
    holdYears: HOLD_YEARS,
    exitMultipleBases: EXIT_MULTIPLE_BASES,
    exitMultipleByTechnology: {
      Solar: 'base',
      Wind: 'base - 0.5',
      'Offshore wind': 'base + 0.5',
      note: 'The issue asks for exit multiples 9.0/8.5/9.5. Those are the three technology-specific multiples the reference derives from a base of 9. To remove the ambiguity, every figure below is also given under bases 8.5 and 9.5.',
    },
    irr: {
      ...REF.irr,
      npv: 'sum(cf[i] / (1 + r) ** (i + 1))  — note the +1: every element is discounted, including the first',
      nullRule: 'null when npv(low) * npv(high) > 0, i.e. no sign change in the bracket',
      note: 'The root matches the standard convention (the NPVs differ by a constant factor), but the null behaviour and out-of-bracket cases do not. A port must reproduce the bracket and the null rule, not just call an IRR routine.',
    },
    // docs/pipeline-schema §9 rejects a file carrying minDscr, lcoe, gearing,
    // equity or capexPerKw: each is one arithmetic step from a field that is
    // present, and two sources for one number is one source too many. They are
    // not lost — they belong with the consumer, which for a golden corpus is
    // this file. A port can check its own derivation against them here without
    // any of them being stored in a pipeline file.
    derivedNotStoredInFiles: {
      rule: 'docs/pipeline-schema.md §9, the reject-derived-fields rule',
      fields: ['minDscr', 'lcoe', 'gearing', 'equity', 'capexPerKw'],
      note: 'Every figure below is derivable from the pipeline files alone. It is repeated here so a reimplementation has an oracle for its own derivation.',
    },
    seriesNaming: {
      thirtyYear: 'fcfe — 30 years, NO terminal value. Lives in the pipeline statement files.',
      holdTruncated: 'holdTruncatedFcfeWithTerminalValue — truncated at the hold year, WITH terminal value. Mandate-dependent, so it appears only here and never in a statement file.',
    },
    projects: projects.map((p) => {
      const minDscr = minDscrFrom(seriesById[p.id]);
      const out = {
        id: p.id,
        name: p.name,
        technology: p.tech,
        capexEURm: p.capex,
        capexPerKW: p.capexKw,
        // 25 of the 48 sit on their technology's cost-band floor and 3 on the
        // cap, so entry pricing determines capex for only 20 of them.
        capexPerKWClamp: capexClampOf(p),
        seniorDebtEURm: p.debt,
        equityEURm: p.equity,
        gearing: p.lev,
        debtSizingBasis: debtSizingBasisOf(p),
        // Raw minimum over the debt life EXCLUDING the ramp year, the value
        // the reference screens on (capped at 3.2 and rounded to 2dp), and the
        // year that binds. The binding year is the first full year for only 34
        // of the 48: contracted revenue escalates at 0.5% while solar
        // generation degrades at 0.5% and opex escalates at 2.1%, so EBITDA
        // falls through the PPA period and steps up at rolloff. A port that
        // assumes "sculpted therefore 1.40 in year one" is wrong on 14.
        minDSCRRaw: minDscr.raw,
        minDSCRBindingYearAge: minDscr.bindingAge,
        minDSCRExcludesRampYear: true,
        minDSCRCap: REF_MIN_DSCR_CAP,
        minDSCRReference: p.dscr,
        // The file carries the EFFECTIVE capture factor so that
        // §4.3's `capturePrice = countryBaseloadPrice x captureFactor` holds
        // exactly; the reference's nominal factor is recorded here.
        nominalCaptureFactor: REF.captureFactor[p.tech],
        effectiveCaptureFactor: effectiveCaptureFactor(p),
        capturePriceEURPerMWh: p.merchant,
        countryBaseloadPriceEURPerMWh: p.baseload,
        lcoeEURPerMWh: p.lcoe,
        lcoeDiscountRate: REF.lcoeDiscountRate,
        lcoeMethod: 'PV(capex undiscounted at t0 + unescalated opex) / PV(generation), 6% real, discounted from the model base year rather than from COD',
        p50GenerationGWh: p.gwh,
        irr30yNoTerminalValue: p.irr30,
        byExitMultipleBase: {},
      };
      for (const base of EXIT_MULTIPLE_BASES) {
        const r = byBase[base].ref;
        const rp = byBase[base].byId[p.id];
        const series = r.holdCf(rp, HOLD_YEARS);
        const irr = r.projIrr(rp, HOLD_YEARS);
        const inflow = series.reduce((a, x) => a + Math.max(0, x), 0);
        const outflow = -series.reduce((a, x) => a + Math.min(0, x), 0);
        out.byExitMultipleBase[String(base)] = {
          base,
          exitMultipleApplied: exitMultipleFor(p.tech, base),
          holdTruncatedFcfeWithTerminalValue: series,
          irrAtHold: irr,
          moicAtHold: outflow > 0 ? inflow / outflow : null,
        };
      }
      return out;
    }),
  };
}

// ---------------------------------------------------------------------------
// objective_cases.json
//
// The fitness function has three branches (empty, over-budget, normal), four
// rewards with clamps and five penalties.  A fixture that only samples random
// chromosomes at one mandate exercises perhaps half of that, so cases come in
// two tiers: handcrafted ones that each exist to hit a named branch or rail,
// and PRNG ones for breadth.  Coverage is then ASSERTED — the tool refuses to
// write a fixture whose own tally shows a branch uncovered.
// ---------------------------------------------------------------------------

// The nine terms, accumulated in the reference's own statement order. Summed
// this way the decomposition is bit-identical to fitness(); summed in any other
// order it is merely close, and the fixture would be decorative rather than a
// check. Asserted with Object.is below.
const TERM_ORDER = [
  'capacity', 'techMix', 'returns', 'utilisation',
  'leveragePenalty', 'merchantPenalty', 'riskPenalty',
  'countryConcentrationPenalty', 'projectConcentrationPenalty',
];

function decompose(ref, sel, mandate) {
  const a = ref.aggregate(sel, mandate);
  const riskCap = { Low: 2.4, Balanced: 3.2, High: 4.2 }[mandate.risk];

  // Accumulate in the reference's own key-insertion order. Float addition is
  // not associative, so summing the sorted keys instead can differ in the last
  // bit — which is exactly what the Object.is assertion below would catch.
  // The emitted map is sorted separately, for a stable diff.
  let countryTotal = 0;
  const countryExcessByInsertion = {};
  for (const cc of Object.keys(a.byC)) {
    const excess = Math.max(0, a.byC[cc] / a.capex - mandate.maxCountry / 100);
    countryExcessByInsertion[cc] = excess;
    countryTotal += excess;
  }
  const countryExcess = {};
  for (const cc of Object.keys(countryExcessByInsertion).sort()) countryExcess[cc] = countryExcessByInsertion[cc];
  const projectExcess = {};
  let projectTotal = 0;
  for (const p of sel) {
    const excess = Math.max(0, p.capex / a.capex - mandate.maxProject / 100);
    projectExcess[p.id] = excess;
    projectTotal += excess;
  }

  const capacityDeviation = Math.abs(a.mw - mandate.target) / mandate.target;
  const techDeviation = Math.abs(a.solarPct - mandate.solarShare) / 0.35;
  const returnsRaw = (a.irrApprox - mandate.hurdle / 100) / 0.05;

  const terms = {
    capacity: {
      deviation: capacityDeviation, clamped: Math.min(1.6, capacityDeviation),
      railed: capacityDeviation >= 1.6, weight: 3.2,
      contribution: 3.2 * (1 - Math.min(1.6, capacityDeviation)),
    },
    techMix: {
      deviation: techDeviation, clamped: Math.min(1.5, techDeviation),
      railed: techDeviation >= 1.5, weight: 3.0,
      contribution: 3.0 * (1 - Math.min(1.5, techDeviation)),
    },
    returns: {
      raw: returnsRaw, clamped: Math.max(-1.2, Math.min(1.2, returnsRaw)),
      railed: Math.abs(returnsRaw) >= 1.2, weight: 2.6,
      contribution: 2.6 * Math.max(-1.2, Math.min(1.2, returnsRaw)),
    },
    utilisation: {
      ratio: a.equity / mandate.capital, weight: 0.9,
      contribution: 0.9 * (a.equity / mandate.capital),
    },
    leveragePenalty: {
      shortfall: Math.max(0, mandate.minLev / 100 - a.lev), weight: -7,
      contribution: -7 * Math.max(0, mandate.minLev / 100 - a.lev),
    },
    merchantPenalty: {
      excess: Math.max(0, a.merchant - mandate.maxMerchant / 100), weight: -14,
      contribution: -14 * Math.max(0, a.merchant - mandate.maxMerchant / 100),
    },
    riskPenalty: {
      cap: riskCap, excess: Math.max(0, a.risk - riskCap), weight: -1.6,
      contribution: -1.6 * Math.max(0, a.risk - riskCap),
    },
    countryConcentrationPenalty: {
      perCountryExcess: countryExcess, total: countryTotal, weight: -8,
      contribution: -8 * countryTotal,
    },
    projectConcentrationPenalty: {
      perProjectExcess: projectExcess, total: projectTotal, weight: -8,
      contribution: -8 * projectTotal,
    },
  };

  // Reference order: f += capacity; += techMix; += returns; += utilisation;
  //                  -= leverage; -= merchant; -= risk; -= country; -= project
  // Penalty contributions are stored already-negated, so a plain += reproduces
  // the reference's `f -= weight * excess` bit for bit.
  let f = 0;
  for (const key of TERM_ORDER) f += terms[key].contribution;

  return { aggregate: a, terms, fitnessFromTerms: f };
}

const genesToString = (genes) => genes.map((g) => (g ? '1' : '0')).join('');

function mandateVariants(ref) {
  const base = { ...ref.state };
  // Only the fields eligible()/aggregate()/fitness() actually read.
  const keep = (m) => ({
    capital: m.capital, target: m.target, solarShare: m.solarShare, hurdle: m.hurdle,
    hold: m.hold, minLev: m.minLev, minDscr: m.minDscr, maxMerchant: m.maxMerchant,
    maxCountry: m.maxCountry, maxProject: m.maxProject, risk: m.risk,
    countries: [...m.countries], stages: [...m.stages],
    codFrom: m.codFrom, codTo: m.codTo,
    gridOnly: m.gridOnly, omOnly: m.omOnly, hedged: m.hedged,
    excluded: [...m.excluded],
    // `locked` is a GA operator input, not a screening or scoring input:
    // fitness() never reads it, run() forces those genes to 1. Emitted anyway
    // so a fixture describes the whole mandate rather than the part that
    // happens to reach the objective function.
    locked: [...(m.locked ?? [])],
  });

  return [
    { id: 'M0-default', purpose: 'the reference\'s own default mandate', mandate: keep(base) },
    { id: 'M1-leverage-floor', purpose: 'drives the minimum-leverage penalty positive', mandate: keep({ ...base, minLev: 85 }) },
    { id: 'M2-merchant-cap', purpose: 'drives the merchant-exposure penalty positive', mandate: keep({ ...base, maxMerchant: 8 }) },
    { id: 'M3-risk-low', purpose: 'Low risk appetite: tighter eligibility screen and a 2.4 penalty cap', mandate: keep({ ...base, risk: 'Low' }) },
    { id: 'M4-country-cap', purpose: 'drives the country-concentration penalty positive', mandate: keep({ ...base, maxCountry: 8 }) },
    { id: 'M5-project-cap', purpose: 'drives the single-project concentration penalty positive', mandate: keep({ ...base, maxProject: 3 }) },
    { id: 'M6-tight-capital', purpose: 'small budget, so most chromosomes take the over-budget branch', mandate: keep({ ...base, capital: 250 }) },
    {
      id: 'M7-screened-pool',
      purpose: 'a different, shorter eligible pool — a port that indexes genes against all 48 instead of the screened pool fails here',
      mandate: keep({ ...base, countries: ['ES', 'PT', 'IT', 'GR'], stages: ['Ready-to-build', 'Construction'], codFrom: 2027, codTo: 2029, gridOnly: true }),
    },
    {
      id: 'M8-short-hold',
      purpose: 'hold 5, where projIrr() returns null for some projects — the reference coalesces that to 0 inside aggregate()',
      mandate: keep({ ...base, hold: 5 }),
    },
    {
      id: 'M9-rail-capacity-high',
      purpose: 'tiny capacity target, so the capacity deviation saturates its 1.6 rail',
      mandate: keep({ ...base, target: 200 }),
    },
    {
      id: 'M10-rail-tech-mix',
      purpose: 'zero solar target, so the technology-mix deviation saturates its 1.5 rail',
      mandate: keep({ ...base, solarShare: 0 }),
    },
    {
      id: 'M11-rail-returns-low',
      purpose: 'unreachable hurdle, so the returns term pins to its -1.2 rail',
      mandate: keep({ ...base, hurdle: 40 }),
    },
    {
      id: 'M12-rail-returns-high',
      purpose: 'zero hurdle, so a high-IRR selection pins the returns term to its +1.2 rail',
      mandate: keep({ ...base, hurdle: 0 }),
    },
    {
      id: 'M13-excluded',
      purpose: 'three projects excluded by the user — a shorter pool with a gap in the middle',
      mandate: keep({ ...base, excluded: ['P01', 'P03', 'P09'] }),
    },
    {
      id: 'M14-hedged-only',
      purpose: 'EUR-denominated projects only, dropping the 14 with a non-EUR label',
      mandate: keep({ ...base, hedged: true }),
    },
    {
      id: 'M15-tight-dscr',
      purpose: 'a 1.60x minimum DSCR screen, which bites on the rounded reference value',
      mandate: keep({ ...base, minDscr: 1.6 }),
    },
    {
      id: 'M16-om-and-grid',
      purpose: 'contracted O&M and a secured grid connection required',
      mandate: keep({ ...base, omOnly: true, gridOnly: true }),
    },
  ];
}

const CASE_SEED_BASE = 1000000;
const DENSITIES = [0.05, 0.15, 0.35, 0.5, 0.75, 0.95];

function buildObjectiveCases(ref) {
  const variants = mandateVariants(ref);
  const cases = [];
  let caseIndex = 0;

  const record = (variant, pool, genes, origin, purpose, extra = {}) => {
    const sel = pool.filter((_, i) => genes[i]);
    const fitness = ref.fitness(genes, pool, variant.mandate);
    const id = `OBJ-${String(caseIndex++).padStart(3, '0')}`;

    let branch, terms = null, aggregate = null, fitnessFromTerms = null, rejectDetail = null;
    if (sel.length === 0) {
      branch = 'empty';
    } else {
      const a = ref.aggregate(sel, variant.mandate);
      if (a.equity > variant.mandate.capital) {
        branch = 'over-budget';
        aggregate = a;
        rejectDetail = {
          equityEURm: a.equity,
          capitalEURm: variant.mandate.capital,
          overshootRatio: a.equity / variant.mandate.capital,
          // What the reference computes, and what epic §6.2 amends it to. Both
          // are recorded so 2A has an oracle for the departure without 1C
          // silently changing what the reference actually does.
          fitnessReference: -20 - a.equity / variant.mandate.capital,
          fitnessAmendedPerEpic6_2: -1000 - 100 * (a.equity / variant.mandate.capital - 1),
        };
      } else {
        branch = 'normal';
        const d = decompose(ref, sel, variant.mandate);
        aggregate = d.aggregate;
        terms = d.terms;
        fitnessFromTerms = d.fitnessFromTerms;
        if (!Object.is(fitnessFromTerms, fitness)) {
          fail(`${id}: term decomposition is not bit-identical to fitness() (${fitnessFromTerms} vs ${fitness})`);
        }
      }
    }

    cases.push({
      caseId: id,
      mandateId: variant.id,
      purpose,
      origin,
      ...extra,
      // The pool is recorded per case so the fixture is self-contained: a port
      // can test fitness() without first having a correct eligible().
      pool: { size: pool.length, ids: pool.map((p) => p.id) },
      genesEncoding: "string of '0'/'1'; index i selects pool.ids[i]",
      genes: genesToString(genes),
      selectedIds: sel.map((p) => p.id),
      // Per-project IRR at this mandate's hold, with nulls left as null. Note
      // aggregate() coalesces these to 0 via `|| 0` before weighting — see
      // docs/decisions.md.
      perProjectIrr: Object.fromEntries(sel.map((p) => [p.id, ref.projIrr(p, variant.mandate.hold)])),
      branch,
      aggregate: aggregate === null ? null : {
        n: aggregate.n, capacityMw: aggregate.mw, capexEURm: aggregate.capex,
        seniorDebtEURm: aggregate.debt, equityEURm: aggregate.equity,
        p50GenerationGWh: aggregate.gwh, solarMw: aggregate.solarMw,
        solarShare: aggregate.solarPct, gearing: aggregate.lev,
        merchantShare: aggregate.merchant, lcoeEURPerMWh: aggregate.lcoe,
        devRiskCapexWeighted: aggregate.risk, irrEquityWeighted: aggregate.irrApprox,
        capexByCountry: Object.fromEntries(Object.keys(aggregate.byC).sort().map((k) => [k, aggregate.byC[k]])),
      },
      rejectDetail,
      // null, not zeros: the reference returns before computing any term, and
      // a zeroed block is a lie a port could accidentally satisfy.
      terms,
      fitness,
      fitnessFromTerms,
    });
  };

  for (const variant of variants) {
    const pool = ref.eligible(variant.mandate);
    if (pool.length === 0) fail(`mandate ${variant.id} screens out every project`);

    // Handcrafted: the branch and rail cases.
    record(variant, pool, pool.map(() => 0), 'handcrafted', 'empty portfolio: the reference returns exactly -50');
    record(variant, pool, pool.map(() => 1), 'handcrafted', 'every eligible project selected');
    record(variant, pool, pool.map((_, i) => (i === 0 ? 1 : 0)), 'handcrafted', 'single project: concentration penalties saturate');
    record(variant, pool, pool.map((_, i) => (i === pool.length - 1 ? 1 : 0)), 'handcrafted', 'single project at the end of the pool: pins gene indexing');

    // Rank by IRR at this mandate's hold, nulls last, ties broken by id so the
    // ordering is deterministic.
    const ranked = pool
      .map((p, i) => ({ i, irr: ref.projIrr(p, variant.mandate.hold), id: p.id }))
      .sort((a, b) => {
        const av = a.irr === null ? -Infinity : a.irr;
        const bv = b.irr === null ? -Infinity : b.irr;
        return bv - av || (a.id < b.id ? -1 : 1);
      });
    const pick = (idxs) => pool.map((_, i) => (idxs.includes(i) ? 1 : 0));
    const trio = Math.min(3, pool.length);
    record(variant, pool, pick(ranked.slice(0, trio).map((x) => x.i)), 'handcrafted',
      'the highest-IRR projects in the pool: drives the returns term toward its upper rail');
    record(variant, pool, pick(ranked.slice(-trio).map((x) => x.i)), 'handcrafted',
      'the lowest-IRR projects in the pool: drives the returns term toward its lower rail');

    // PRNG breadth, from the reference's own generator.
    for (const density of DENSITIES) {
      const seed = CASE_SEED_BASE + caseIndex;
      const rnd = ref.seeded(seed);
      const genes = pool.map(() => (rnd() < density ? 1 : 0));
      record(variant, pool, genes, 'prng', `density ${density} over the ${variant.id} pool`,
        { prng: { seed, density } });
    }
  }

  // The boundary: a mandate whose capital is exactly the equity of a chosen
  // chromosome. The reference rejects on `equity > capital`, so this must fall
  // through to the normal branch — and it only does so if the port accumulates
  // equity in the same order the reference does.
  const baseVariant = mandateVariants(ref)[0];
  const basePool = ref.eligible(baseVariant.mandate);
  const boundaryRnd = ref.seeded(CASE_SEED_BASE + 999);
  const boundaryGenes = basePool.map(() => (boundaryRnd() < 0.3 ? 1 : 0));
  const boundaryEquity = ref.aggregate(basePool.filter((_, i) => boundaryGenes[i]), baseVariant.mandate).equity;

  for (const [label, capital, purpose] of [
    ['M13-on-the-cap', boundaryEquity, 'capital is exactly the selection\'s equity: `equity > capital` is false, so this must NOT be rejected'],
    ['M14-one-ulp-under', prevDouble(boundaryEquity), 'capital is one ulp below the selection\'s equity: this MUST be rejected'],
  ]) {
    const variant = { id: label, purpose, mandate: { ...baseVariant.mandate, capital } };
    const pool = ref.eligible(variant.mandate);
    record(variant, pool, boundaryGenes, 'handcrafted', purpose);
  }

  const coverage = tallyCoverage(cases);
  assertCoverage(coverage);

  return {
    $schemaId: 'terrafolio/objective-cases/v1',
    weights: {
      capacity: 3.2, techMix: 3.0, returns: 2.6, utilisation: 0.9,
      leveragePenalty: -7, merchantPenalty: -14, riskPenalty: -1.6,
      countryConcentrationPenalty: -8, projectConcentrationPenalty: -8,
    },
    clamps: {
      capacityDeviation: 1.6, techMixDeviation: 1.5, returnsBand: 1.2,
      techMixScale: 0.35, returnsScale: 0.05,
      riskCapByAppetite: { Low: 2.4, Balanced: 3.2, High: 4.2 },
    },
    emptyPortfolioScore: -50,
    rejectRule: {
      reference: '-20 - equity / capital',
      amendedPerEpic6_2: '-1000 - 100 * (equity / capital - 1)',
      note: 'The reference rule can score an infeasible portfolio above a feasible one (the worst feasible score is about -44), which is why epic §6.2 amends it. Both values are recorded per case; 2A should assert the amended one.',
    },
    termAccumulationOrder: TERM_ORDER,
    coverage,
    cases,
  };
}

// One ulp below x, via the bit pattern — used to prove the budget comparison is
// a strict `>` on the exact double rather than a tolerance.
function prevDouble(x) {
  const buf = new ArrayBuffer(8);
  const view = new DataView(buf);
  view.setFloat64(0, x);
  const bits = view.getBigUint64(0);
  view.setBigUint64(0, bits - 1n);
  return view.getFloat64(0);
}

function tallyCoverage(cases) {
  const t = {
    branchEmpty: 0, branchOverBudget: 0, branchNormal: 0,
    capacityRailed: 0, capacityInterior: 0,
    techMixRailed: 0, techMixInterior: 0,
    returnsRailedHigh: 0, returnsRailedLow: 0, returnsInterior: 0,
    leveragePenaltyActive: 0, leveragePenaltyInactive: 0,
    merchantPenaltyActive: 0, merchantPenaltyInactive: 0,
    riskPenaltyActive: 0, riskPenaltyInactive: 0,
    countryPenaltyActive: 0, countryPenaltyInactive: 0,
    projectPenaltyActive: 0, projectPenaltyInactive: 0,
    nullProjectIrr: 0, distinctPools: 0,
  };
  const pools = new Set();
  for (const c of cases) {
    pools.add(c.pool.ids.join(','));
    if (c.branch === 'empty') t.branchEmpty++;
    else if (c.branch === 'over-budget') t.branchOverBudget++;
    else t.branchNormal++;
    if (Object.values(c.perProjectIrr).some((v) => v === null)) t.nullProjectIrr++;
    if (!c.terms) continue;
    const m = c.terms;
    m.capacity.railed ? t.capacityRailed++ : t.capacityInterior++;
    m.techMix.railed ? t.techMixRailed++ : t.techMixInterior++;
    if (m.returns.raw >= 1.2) t.returnsRailedHigh++;
    else if (m.returns.raw <= -1.2) t.returnsRailedLow++;
    else t.returnsInterior++;
    m.leveragePenalty.contribution < 0 ? t.leveragePenaltyActive++ : t.leveragePenaltyInactive++;
    m.merchantPenalty.contribution < 0 ? t.merchantPenaltyActive++ : t.merchantPenaltyInactive++;
    m.riskPenalty.contribution < 0 ? t.riskPenaltyActive++ : t.riskPenaltyInactive++;
    m.countryConcentrationPenalty.contribution < 0 ? t.countryPenaltyActive++ : t.countryPenaltyInactive++;
    m.projectConcentrationPenalty.contribution < 0 ? t.projectPenaltyActive++ : t.projectPenaltyInactive++;
  }
  t.distinctPools = pools.size;
  t.total = cases.length;
  return t;
}

const COVERAGE_MINIMUM = 2;

function assertCoverage(t) {
  const exempt = new Set(['total', 'distinctPools']);
  const thin = Object.entries(t).filter(([k, v]) => !exempt.has(k) && v < COVERAGE_MINIMUM);
  if (thin.length) {
    fail(
      `objective_cases.json does not cover every branch and rail — ` +
      `${thin.map(([k, v]) => `${k}=${v}`).join(', ')} (need >= ${COVERAGE_MINIMUM} each).\n` +
      `Refusing to write a fixture that claims coverage it does not have.`
    );
  }
  if (t.distinctPools < 5) fail(`objective cases only exercise ${t.distinctPools} distinct pools; need at least 5`);
}

// ---------------------------------------------------------------------------
// ga_trace_fast_seed42.json
//
// The reference's own run() is DRIVEN here, not transcribed: its timer is
// stubbed and its Date.now() seeding overridden, but the genetic algorithm
// executing is the bundle's, character for character.  That removes any risk
// of a transcription error in the most intricate code in the reference.
//
// This traces REFERENCE semantics — 35% inclusion at initialisation, no budget
// repair.  It is an oracle for a port of those mechanics, and explicitly NOT a
// target for the production GA, which follows the departures in epic §6.1.
// ---------------------------------------------------------------------------

const EFFORT = { Fast: [50, 35], Standard: [90, 60], Exhaustive: [160, 110] };
const SPEC_INCLUSION_PROBABILITY = 0.35;

function buildGaTrace(makeRef, harness, { locked = [], label = 'unlocked' } = {}) {
  const ref = makeRef({ algorithmEffort: 'Fast' });
  const mandate = { ...mandateVariants(ref)[0].mandate, locked: [...locked] };
  const pool = ref.eligible({ ...ref.state, ...mandate });
  const [popSize, generations] = EFFORT.Fast;

  const lockedIdx = pool.map((p, i) => (locked.includes(p.id) ? i : -1)).filter((i) => i >= 0);
  if (lockedIdx.length !== locked.length) {
    fail(`locked ids ${locked} are not all in the ${label} pool`);
  }
  // The reference forces locked genes to 1 at initialisation and on every
  // child. Mirror it when reconstructing the initial population, or the
  // reconstruction check below fails for the wrong reason.
  const force = (g) => { for (const i of lockedIdx) g[i] = 1; return g; };

  const perGeneration = [];
  const state = harness.driveGa(ref, mandate, GA_SEED, (inst) => {
    perGeneration.push({
      generation: inst.state.gen,
      bestFitness: inst.state.hist[inst.state.hist.length - 1][0],
      meanFitness: inst.state.hist[inst.state.hist.length - 1][1],
      bestSelectedIds: [...inst.state.best.ids],
    });
  });

  if (perGeneration.length !== generations) {
    fail(`GA produced ${perGeneration.length} generations, expected ${generations}`);
  }

  // Reconstruct the initial population so a port can check its PRNG
  // consumption before the first selection. Proven correct below against the
  // generation-1 figures the reference itself reported.
  const initRnd = ref.seeded(GA_SEED);
  const initialPopulation = [];
  for (let i = 0; i < popSize; i++) {
    initialPopulation.push(force(pool.map(() => (initRnd() < SPEC_INCLUSION_PROBABILITY ? 1 : 0))));
  }
  const initialFitness = initialPopulation.map((g) => ref.fitness(g, pool, mandate));
  // The reference takes its mean over the DESCENDING-SORTED population, and
  // float addition is not associative — summing in population order differs in
  // the last bit. Match the reference's order exactly.
  const sortedFitness = [...initialFitness].sort((a, b) => b - a);
  const initialBest = sortedFitness[0];
  const initialMean = sortedFitness.reduce((a, x) => a + x, 0) / sortedFitness.length;
  if (!Object.is(initialBest, perGeneration[0].bestFitness) || !Object.is(initialMean, perGeneration[0].meanFitness)) {
    fail(
      `[${label} trace] the reconstructed initial population does not reproduce generation 1 of the driven run ` +
      `(best ${initialBest} vs ${perGeneration[0].bestFitness}, mean ${initialMean} vs ${perGeneration[0].meanFitness}). ` +
      'Either the PRNG consumption order has changed, or the force operator no longer sets locked genes at ' +
      `initialisation (this trace locks [${locked.join(', ') || 'nothing'}]).`
    );
  }

  // Elitism of two is unconditional, so best fitness can never regress.
  for (let i = 1; i < perGeneration.length; i++) {
    if (perGeneration[i].bestFitness < perGeneration[i - 1].bestFitness) {
      fail(`GA best fitness regressed at generation ${perGeneration[i].generation}; 2-elitism makes that impossible`);
    }
  }

  // Locked projects must appear in the best selection of EVERY generation, and
  // in every chromosome of the initial population. This is the assertion that
  // gives the force operator an oracle: with an empty lock list, removing
  // force() from the reference changes nothing observable, so the locked trace
  // is the only thing standing between a broken port and a portfolio that
  // silently drops projects the user pinned.
  for (const id of locked) {
    for (const g of perGeneration) {
      if (!g.bestSelectedIds.includes(id)) {
        fail(`[${label} trace] locked project ${id} is missing from the best selection at generation ${g.generation}; the force operator is not holding locks through crossover`);
      }
    }
  }
  for (const i of lockedIdx) {
    if (!initialPopulation.every((g) => g[i] === 1)) {
      fail(`[${label} trace] locked gene at index ${i} is not set in every chromosome of the initial population`);
    }
  }

  return {
    $schemaId: 'terrafolio/ga-trace/v1',
    semantics: {
      source: "the reference's own run(), driven with its timer stubbed and its seeding overridden",
      initialisation: `independent Bernoulli draws at p = ${SPEC_INCLUSION_PROBABILITY} per gene (spec §10.1 as written)`,
      repair: 'none',
      note: 'This is a port-verification oracle for the REFERENCE. The production GA departs from it per epic §6.1 (capital-aware inclusion probability plus a budget repair operator) and §6.2 (the graded reject score), and must NOT be expected to reproduce this trace.',
    },
    effort: { name: 'Fast', popSize, generations },
    seed: GA_SEED,
    selection: {
      elitism: 2,
      tournament: 'two uniform draws over the fitness-sorted population; ties go to the second draw',
      crossover: 'uniform, per gene, at p = 0.5',
      mutation: 'per gene, at p = 0.025, after crossover',
      sort: 'descending by fitness; Array.prototype.sort is stable, so ties keep population order',
    },
    mandate,
    locked: {
      ids: [...locked],
      poolIndices: lockedIdx,
      operator: 'run() forces every locked gene to 1 at initialisation and on every child produced by crossover; the two elite chromosomes carry their locks forward from the previous generation',
      invariant: 'every locked id appears in the best selection of every generation, and every chromosome of the initial population has its locked genes set',
    },
    pool: { size: pool.length, ids: pool.map((p) => p.id) },
    genesEncoding: "string of '0'/'1'; index i selects pool.ids[i]",
    initialPopulation: initialPopulation.map(genesToString),
    initialBestFitness: initialBest,
    initialMeanFitness: initialMean,
    generations: perGeneration,
    result: {
      fitness: state.best.fitness,
      selectedIds: [...state.best.ids],
      selectedCount: state.best.ids.length,
    },
  };
}

// ===========================================================================
// 6. CANONICAL WRITER, SELF-CHECKS, CLI
// ===========================================================================

// Every number is emitted at full IEEE-754 precision. Rounding would make the
// oracle weaker than the model it is meant to check, and would put a slack term
// into every tie-out larger than the model's own 1e-12 residuals. Values the
// REFERENCE itself rounds (min DSCR to 2dp, LCOE to an integer) are stored as
// the reference produced them, with the raw companion alongside.
function assertEmittable(value, pathStr) {
  if (value === null) return value;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) fail(`non-finite number at ${pathStr}: ${value}`);
    return Object.is(value, -0) ? 0 : value;
  }
  if (typeof value === 'undefined') fail(`undefined at ${pathStr} — emit null instead`);
  if (Array.isArray(value)) return value.map((v, i) => assertEmittable(v, `${pathStr}[${i}]`));
  if (typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) {
      // projIrr() memoises onto the project objects as `irr10x9` and friends;
      // a key like that in an emitted file means something serialised a live
      // reference object by key enumeration.
      if (/^irr\d+(\.\d+)?x/.test(k)) fail(`memoised IRR key leaked into output at ${pathStr}.${k}`);
      out[k] = assertEmittable(v, `${pathStr}.${k}`);
    }
    return out;
  }
  return value;
}

function canonicalJson(value, name) {
  return `${JSON.stringify(assertEmittable(value, name), null, 2)}\n`;
}

function writeText(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text, 'utf8');
}

const slug = (name) => name
  .normalize('NFD').replace(/[̀-ͯ]/g, '')
  .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

// ---------------------------------------------------------------------------
// Pre-write checks. Everything is built in memory, asserted, and only then
// written — a broken extraction must fail rather than emit a wrong golden.
// ---------------------------------------------------------------------------

const PUBLISHED_TOTALS = { mw: 6300, capex: 7673.072, equity: 2956.812, gwh: 17159.720 };
const PUBLISHED_COMPOSITION = {
  // Counts are per the reference's own technology names; the expected enum
  // values are taken from the active contract, so this check does not have to
  // be edited when the contract changes.
  technologyCounts: { Solar: 22, Wind: 22, 'Offshore wind': 4 },
  stage: { ready_to_build: 20, greenfield: 17, construction: 11 },
  countries: 14,
  codFrom: 2027,
  codTo: 2032,
};

function checkPipeline(projects, files) {
  const report = { tieOuts: [], totals: {}, worstTieOut: { name: null, residual: 0 } };

  const templateGoverns = contractNameOf(CONTRACT) === TEMPLATE_CONTRACT;
  TEMPLATE_SHAPE = templateGoverns ? loadTemplateShape() : null;
  report.templateShapeChecked = TEMPLATE_SHAPE !== null;
  report.templateShapeSkippedReason = templateGoverns
    ? (TEMPLATE_SHAPE === null ? 'templates/project-template.json is absent (lands with #3)' : null)
    : `contract '${contractNameOf(CONTRACT)}' is not the one templates/project-template.json describes`;

  if (projects.length !== 48) fail(`expected 48 projects, got ${projects.length}`);
  const ids = projects.map((p) => p.id);
  const sorted = [...ids].sort();
  if (ids.join(',') !== sorted.join(',')) fail('projects are not in ascending id order');
  if (new Set(ids).size !== 48) fail('duplicate project ids');

  // Totals, computed from the EMITTED FILES rather than from the reference, so
  // this checks what actually lands on disk.
  const t = {
    mw: files.reduce((a, f) => a + f.asset.capacityMw, 0),
    capex: files.reduce((a, f) => a + f.capitalStructure.totalCapex, 0),
    // Equity is not stored (§9) — it is totalCapex - seniorDebt, which is the
    // point: one source for the number.
    equity: files.reduce((a, f) => a + (f.capitalStructure.totalCapex - f.capitalStructure.seniorDebt), 0),
    gwh: files.reduce((a, f) => a + f.asset.capacityMw * 8.760 * f.asset.netCapacityFactor, 0),
    debt: files.reduce((a, f) => a + f.capitalStructure.seniorDebt, 0),
  };
  report.totals = t;
  const r3 = (x) => Math.round(x * 1000) / 1000;
  if (t.mw !== PUBLISHED_TOTALS.mw) fail(`capacity total ${t.mw} != ${PUBLISHED_TOTALS.mw} MW`);
  for (const [k, want] of [['capex', PUBLISHED_TOTALS.capex], ['equity', PUBLISHED_TOTALS.equity], ['gwh', PUBLISHED_TOTALS.gwh]]) {
    if (r3(t[k]) !== want) fail(`${k} total ${r3(t[k])} != published ${want}`);
  }
  if (Math.abs(t.capex - (t.debt + t.equity)) > 1e-9) fail('capex != debt + equity across the pipeline');

  const tally = (key, get) => {
    const m = {};
    for (const f of files) m[get(f)] = (m[get(f)] || 0) + 1;
    return m;
  };
  const tech = tally('tech', (f) => f.asset.technology);
  const stage = tally('stage', (f) => f.asset.stage);
  for (const [refName, v] of Object.entries(PUBLISHED_COMPOSITION.technologyCounts)) {
    const k = CONTRACT.technology[refName];
    if (tech[k] !== v) fail(`technology split: ${k} is ${tech[k]}, expected ${v}`);
  }
  for (const [k, v] of Object.entries(PUBLISHED_COMPOSITION.stage)) {
    if (stage[k] !== v) fail(`stage split: ${k} is ${stage[k]}, expected ${v}`);
  }
  const countries = new Set(files.map((f) => f.location.countryCode));
  if (countries.size !== PUBLISHED_COMPOSITION.countries) fail(`expected ${PUBLISHED_COMPOSITION.countries} countries, got ${countries.size}`);
  const cods = files.map((f) => f.asset.codYear);
  if (Math.min(...cods) !== PUBLISHED_COMPOSITION.codFrom || Math.max(...cods) !== PUBLISHED_COMPOSITION.codTo) {
    fail(`COD range is ${Math.min(...cods)}-${Math.max(...cods)}, expected ${PUBLISHED_COMPOSITION.codFrom}-${PUBLISHED_COMPOSITION.codTo}`);
  }

  // Filenames must be unique after case folding: on a case-insensitive
  // filesystem a collision would silently overwrite a fixture.
  const folded = new Set();
  for (const f of files) {
    const name = `${f.id}-${slug(f.name)}.json`.toLowerCase();
    if (folded.has(name)) fail(`filename collision after case folding: ${name}`);
    folded.add(name);
  }

  // The tie-outs themselves.
  for (const tie of TIE_OUTS) {
    let worstResidual = 0;
    for (const f of files) {
      const res = tie.check(f);
      if (!res.ok) {
        const detail = res.detail ? `\n  ${res.detail.join('\n  ')}` : '';
        fail(`tie-out failed for ${f.id}: ${tie.name} (residual ${res.residual})${detail}`);
      }
      worstResidual = Math.max(worstResidual, res.residual);
    }
    report.tieOuts.push({ name: tie.name, worstResidual });
    if (worstResidual > report.worstTieOut.residual) report.worstTieOut = { name: tie.name, residual: worstResidual };
  }

  return report;
}

function checkReconstruction(projects, seriesById) {
  // The strongest assertion available: the reconstruction must reproduce the
  // reference's own arrays EXACTLY, not within a tolerance. 1,440 doubles
  // matching bit for bit catches any drift in the model.
  for (const p of projects) {
    const s = seriesById[p.id];
    for (let t = 0; t < REF.years; t++) {
      if (!Object.is(s.fcfe[t], p.cfEquity[t])) fail(`${p.id} year ${t}: reconstructed FCFE ${s.fcfe[t]} != reference ${p.cfEquity[t]}`);
      if (!Object.is(s.ebitda[t], p.ebitda[t])) fail(`${p.id} year ${t}: reconstructed EBITDA ${s.ebitda[t]} != reference ${p.ebitda[t]}`);
      if (!Object.is(s.generationGwh[t], p.gen[t])) fail(`${p.id} year ${t}: reconstructed generation ${s.generationGwh[t]} != reference ${p.gen[t]}`);
    }
    // The reference's own rounded min DSCR must fall out of our raw one.
    const { raw } = minDscrFrom(s);
    if (referenceMinDscr(raw) !== p.dscr) {
      fail(`${p.id}: round(min(${raw}, ${REF_MIN_DSCR_CAP}), 2) = ${referenceMinDscr(raw)} != reference p.dscr ${p.dscr}`);
    }
    // Exactly one negative post-COD FCFE year per project, and it is the ramp:
    // 55% generation against a full year of debt service. Under the schema's
    // funding convention the COD year's capex and drawdown cancel, so a
    // negative there is operating, not funding.
    const negatives = s.fcfe.map((v, i) => (v < 0 && s.age[i] >= 0 ? s.age[i] : null)).filter((x) => x !== null);
    if (negatives.length !== 1 || negatives[0] !== 0) {
      fail(`${p.id}: expected exactly one negative post-COD FCFE year at age 0, got ages [${negatives}]`);
    }
  }
}

// ---------------------------------------------------------------------------
// Build everything in memory, check it, then write.
// ---------------------------------------------------------------------------

async function build(outDir) {
  const harness = await loadReference(outDir);
  const makeRef = (props) => harness.createReference(props);

  const ref = makeRef();
  const projects = ref.projects;

  const seriesById = {};
  for (const p of projects) seriesById[p.id] = buildSeries(p);
  checkReconstruction(projects, seriesById);

  const files = projects.map((p) => toProjectFile(p, seriesById[p.id]));
  const report = checkPipeline(projects, files);

  const artefacts = new Map();
  for (const f of files) {
    artefacts.set(path.join('pipeline', `${f.id}-${slug(f.name)}.json`), canonicalJson(f, f.id));
  }
  artefacts.set('js_prng.json', canonicalJson(buildPrngFixture(ref, projects), 'js_prng'));
  artefacts.set('derived_expectations.json', canonicalJson(buildDerivedExpectations(makeRef, projects, seriesById), 'derived_expectations'));

  const objective = buildObjectiveCases(makeRef());
  artefacts.set('objective_cases.json', canonicalJson(objective, 'objective_cases'));

  const gaTrace = buildGaTrace(makeRef, harness);
  artefacts.set('ga_trace_fast_seed42.json', canonicalJson(gaTrace, 'ga_trace'));

  // A second trace with projects locked in. Locks are chosen from the projects
  // the UNLOCKED run rejected, worst IRR first — so they are demonstrably
  // projects the objective does not want, and a GA that quietly drops its locks
  // produces a visibly different trace rather than the same one.
  const lockRef = makeRef({ algorithmEffort: 'Fast' });
  const lockMandate = mandateVariants(lockRef)[0].mandate;
  const lockPool = lockRef.eligible({ ...lockRef.state, ...lockMandate });
  const rejected = lockPool.filter((p) => !gaTrace.result.selectedIds.includes(p.id));
  const lockedIds = rejected
    .map((p) => ({ id: p.id, irr: lockRef.projIrr(p, lockMandate.hold) }))
    .sort((a, b) => {
      const av = a.irr === null ? -Infinity : a.irr;
      const bv = b.irr === null ? -Infinity : b.irr;
      return av - bv || (a.id < b.id ? -1 : 1);
    })
    .slice(0, 2)
    .map((x) => x.id)
    .sort();
  if (lockedIds.length !== 2) fail('could not choose two locked projects from the rejected set');

  const gaTraceLocked = buildGaTrace(makeRef, harness, { locked: lockedIds, label: 'locked' });
  if (!lockedIds.every((id) => gaTraceLocked.result.selectedIds.includes(id))) {
    fail(`locked projects ${lockedIds} are missing from the locked run's result`);
  }
  if (gaTraceLocked.result.fitness >= gaTrace.result.fitness) {
    fail('locking projects the unlocked run rejected did not reduce the achievable fitness; the locks are not binding and the trace proves nothing');
  }
  artefacts.set('ga_trace_fast_seed42_locked.json', canonicalJson(gaTraceLocked, 'ga_trace_locked'));

  // Manifest last: it hashes everything else, including the extracted
  // reference module, so a changed extraction shows up as a changed manifest.
  const refFiles = ['reference/component.mjs', 'reference/harness.mjs'];
  for (const rel of refFiles) artefacts.set(rel, fs.readFileSync(path.join(outDir, rel), 'utf8'));

  const manifest = {
    $schemaId: 'terrafolio/golden-manifest/v1',
    tool: `tools/extract_reference.mjs@${TOOL_VERSION}`,
    schemaVersion: SCHEMA_VERSION,
    contract: contractNameOf(CONTRACT),
    bundle: { name: BUNDLE_NAME, sha256: BUNDLE_SHA256 },
    // Deliberately no timestamp and no runtime version: this file must be
    // byte-identical across machines and CI images.
    files: Object.fromEntries([...artefacts.keys()].sort().map((k) => [k, sha256(artefacts.get(k))])),
  };
  artefacts.set('MANIFEST.json', canonicalJson(manifest, 'manifest'));

  return { artefacts, report, objective, gaTrace, gaTraceLocked, projects, files };
}

function writeArtefacts(outDir, artefacts) {
  for (const [rel, text] of artefacts) writeText(path.join(outDir, rel), text);
}

// Replace the fixture tree in one step.
//
// Building straight into the live directory is wrong twice over. loadReference()
// writes the extracted modules before anything has been validated, so a later
// assertion failure would leave new modules paired with old fixtures and an old
// manifest — a tree that is internally inconsistent and whose manifest says it
// is fine. And writing is overwrite-only, so a renamed or removed artefact
// lingers: a project whose slug changed would appear twice, and a directory
// scan downstream would load both.
//
// So: stage the whole set beside the target, validate it there, and only then
// swap. A failure leaves the previous tree untouched; a success leaves no stale
// files. Staging is a sibling directory rather than os.tmpdir() so the rename
// stays on one filesystem and cannot fail with EXDEV.
function stagingPathFor(target) {
  return path.join(path.dirname(target), `.${path.basename(target)}.staging`);
}

function commitStaged(target, staging) {
  const backup = path.join(path.dirname(target), `.${path.basename(target)}.previous`);
  fs.rmSync(backup, { recursive: true, force: true });

  const hadPrevious = fs.existsSync(target);
  if (hadPrevious) fs.renameSync(target, backup);
  try {
    fs.renameSync(staging, target);
  } catch (err) {
    // Put the previous tree back before surfacing the failure.
    if (hadPrevious && !fs.existsSync(target)) fs.renameSync(backup, target);
    throw err;
  }
  fs.rmSync(backup, { recursive: true, force: true });
}

function summarise(built) {
  const { report, objective, gaTrace, gaTraceLocked, artefacts } = built;
  const t = report.totals;
  const lines = [];
  lines.push('Published totals, recomputed from the emitted files:');
  lines.push(`  capacity   ${t.mw} MW`);
  lines.push(`  capex      EUR ${t.capex.toFixed(3)}m`);
  lines.push(`  equity     EUR ${t.equity.toFixed(3)}m`);
  lines.push(`  senior debt EUR ${t.debt.toFixed(3)}m`);
  lines.push(`  generation ${t.gwh.toFixed(3)} GWh/y`);
  lines.push('');
  lines.push(`Tie-outs: ${report.tieOuts.length} checks x 48 files, all pass.`);
  lines.push(`Contract: ${contractNameOf(CONTRACT)} - ${CONTRACT.describe}`);
  lines.push(report.templateShapeChecked
    ? '  closed-template shape: ENFORCED against templates/project-template.json'
    : `  closed-template shape: NOT CHECKED - ${report.templateShapeSkippedReason}`);
  lines.push(`  worst residual: ${report.worstTieOut.residual.toExponential(3)}  (${report.worstTieOut.name})`);
  const top = [...report.tieOuts].sort((a, b) => b.worstResidual - a.worstResidual).slice(0, 5);
  for (const x of top) lines.push(`  ${x.worstResidual.toExponential(3).padStart(10)}  ${x.name}`);
  lines.push('');
  lines.push(`Objective cases: ${objective.cases.length} across ${objective.coverage.distinctPools} distinct pools.`);
  lines.push(`  branches: empty ${objective.coverage.branchEmpty}, over-budget ${objective.coverage.branchOverBudget}, normal ${objective.coverage.branchNormal}`);
  lines.push(`  penalties active: leverage ${objective.coverage.leveragePenaltyActive}, merchant ${objective.coverage.merchantPenaltyActive}, risk ${objective.coverage.riskPenaltyActive}, country ${objective.coverage.countryPenaltyActive}, project ${objective.coverage.projectPenaltyActive}`);
  lines.push(`  rails: capacity ${objective.coverage.capacityRailed}, tech mix ${objective.coverage.techMixRailed}, returns high ${objective.coverage.returnsRailedHigh} / low ${objective.coverage.returnsRailedLow}`);
  lines.push(`  cases with a null per-project IRR: ${objective.coverage.nullProjectIrr}`);
  lines.push('');
  lines.push(`GA trace: Fast ${gaTrace.effort.popSize}x${gaTrace.effort.generations}, seed ${gaTrace.seed}, pool ${gaTrace.pool.size}.`);
  lines.push(`  unlocked: final fitness ${gaTrace.result.fitness}, ${gaTrace.result.selectedCount} projects`);
  lines.push(`  locked ${gaTraceLocked.locked.ids.join(', ')}: final fitness ${gaTraceLocked.result.fitness}, ${gaTraceLocked.result.selectedCount} projects`);
  lines.push(`  locked projects held through all ${gaTraceLocked.generations.length} generations`);
  lines.push('');
  lines.push(`${artefacts.size} files.`);
  return lines.join('\n');
}

// ---------------------------------------------------------------------------

const FIXTURES_DIR = path.join(ROOT, 'tests', 'golden', 'fixtures');

async function main(argv) {
  const major = Number(process.versions.node.split('.')[0]);
  if (!Number.isFinite(major) || major < MIN_NODE_MAJOR) {
    fail(`this tool needs Node ${MIN_NODE_MAJOR} or newer; running on ${process.versions.node}`);
  }

  if (argv.includes('--help') || argv.includes('-h')) {
    process.stdout.write(usage());
    return 0;
  }

  const contractArg = argv.find((a) => a.startsWith('--contract='));
  const contractName = selectContract(contractArg ? contractArg.slice('--contract='.length) : DEFAULT_CONTRACT);

  // --out lets a non-default contract be emitted somewhere else for
  // validation, so the committed fixtures only ever carry the default.
  const outArg = argv.find((a) => a.startsWith('--out='));
  if (outArg && contractName === DEFAULT_CONTRACT && !argv.includes('--force-default-out')) {
    // Guard against a stray --out quietly writing the default set elsewhere
    // and leaving the committed one stale.
    process.stderr.write('note: --out with the default contract writes a copy, not the committed set\n');
  }

  const check = argv.includes('--check');
  const manifestOnly = argv.includes('--stdout-manifest');

  if (manifestOnly) {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'terrafolio-ref-'));
    try {
      const built = await build(tmp);
      process.stdout.write(built.artefacts.get('MANIFEST.json'));
    } finally {
      fs.rmSync(tmp, { recursive: true, force: true });
    }
    return 0;
  }

  if (check) {
    // Re-extract into a temp directory and byte-compare with what is committed.
    // This is the determinism gate: it proves a second run produces identical
    // bytes, and that the committed fixtures are the ones this tool emits.
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'terrafolio-ref-'));
    let problems = [];
    try {
      const built = await build(tmp);
      for (const [rel, text] of built.artefacts) {
        const committed = path.join(FIXTURES_DIR, rel);
        if (!fs.existsSync(committed)) { problems.push(`missing: ${rel}`); continue; }
        if (fs.readFileSync(committed, 'utf8') !== text) problems.push(`differs: ${rel}`);
      }
      const onDisk = listFixtureFiles(FIXTURES_DIR);
      for (const rel of onDisk) if (!built.artefacts.has(rel)) problems.push(`unexpected file on disk: ${rel}`);
      process.stdout.write(`${summarise(built)}\n\n`);
    } finally {
      fs.rmSync(tmp, { recursive: true, force: true });
    }
    if (problems.length) {
      process.stderr.write(`--check FAILED\n${problems.map((p) => `  ${p}`).join('\n')}\n`);
      return 1;
    }
    process.stdout.write('--check OK: committed fixtures are byte-identical to a fresh extraction.\n');
    return 0;
  }

  const target = outArg ? path.resolve(ROOT, outArg.slice('--out='.length)) : FIXTURES_DIR;
  if (contractName !== DEFAULT_CONTRACT && target === FIXTURES_DIR) {
    fail(
      `refusing to write the committed fixtures under contract '${contractName}'.\n` +
      `The committed set is emitted under '${DEFAULT_CONTRACT}'. To try another contract, ` +
      `pass --out=<dir> as well; to change what is committed, change DEFAULT_CONTRACT.`
    );
  }

  // Build and validate in a staging directory, then swap it in. Nothing touches
  // the committed fixtures until every assertion has passed.
  const staging = stagingPathFor(target);
  fs.rmSync(staging, { recursive: true, force: true });
  let built;
  try {
    built = await build(staging);
    writeArtefacts(staging, built.artefacts);
  } catch (err) {
    fs.rmSync(staging, { recursive: true, force: true });
    throw err;
  }

  const before = new Set(listFixtureFiles(target));
  commitStaged(target, staging);
  const removed = [...before].filter((rel) => !built.artefacts.has(rel)).sort();

  process.stdout.write(`${summarise(built)}\n`);
  if (removed.length) {
    process.stdout.write(`Removed ${removed.length} stale file(s):\n${removed.map((r) => `  ${r}`).join('\n')}\n`);
  }
  process.stdout.write(`Written to ${path.relative(ROOT, target)}/ under contract '${contractName}'\n`);
  return 0;
}

function usage() {
  const contracts = Object.entries(CONTRACTS)
    .map(([k, v]) => `    ${k.padEnd(22)}${v.describe}${k === DEFAULT_CONTRACT ? '  [default]' : ''}`)
    .join('\n');
  return [
    'Usage: node tools/extract_reference.mjs [options]',
    '',
    '  (no options)          emit every fixture under the default contract',
    '  --check               re-extract and byte-compare against the committed fixtures',
    '  --stdout-manifest     print the manifest only, for cross-process determinism checks',
    '  --contract=<name>     emit under a different project-file contract (needs --out)',
    '  --out=<dir>           write somewhere other than tests/golden/fixtures',
    '',
    '  Contracts:',
    contracts,
    '',
  ].join('\n');
}

function listFixtureFiles(dir, prefix = '') {
  if (!fs.existsSync(dir)) return [];
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : 1))) {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) out.push(...listFixtureFiles(path.join(dir, entry.name), rel));
    else out.push(rel);
  }
  return out;
}

main(process.argv.slice(2))
  .then((code) => { process.exitCode = code; })
  .catch((err) => {
    process.stderr.write(`\nextract_reference failed:\n  ${err.message}\n`);
    process.exitCode = 1;
  });
