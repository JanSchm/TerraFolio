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
const SCHEMA_VERSION = '0.1.0-interim';

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
  ppaEscalation: 1.005,
  merchantEscalation: 1.021,
  opexEscalation: 1.021,
  lcoeDiscountRate: 0.06,
  degradation: { Solar: 0.005, default: 0.002 },
  captureFactor: { Solar: 0.68, Wind: 0.88, 'Offshore wind': 0.86 },
  irr: { method: 'bisection', low: -0.5, high: 1.2, iterations: 70, firstPeriod: 1 },
});

const ANNUITY_FACTOR = REF.debtRate / (1 - Math.pow(1 + REF.debtRate, -REF.debtTenorYears));

const degradationFor = (tech) => (tech === 'Solar' ? REF.degradation.Solar : REF.degradation.default);
const buildYearsFor = (p) => Math.max(1, p.cod - REF.firstYear);

// ===========================================================================
// 3. STATEMENT RECONSTRUCTION
//
// The reference stores only EBITDA, generation and signed equity cash flow per
// year.  Everything else in a statement file — revenue, opex, depreciation,
// interest, principal, tax, and the balance-sheet roll-forward — is recomputed
// here from the same inputs and the same formulas, then asserted against the
// reference's own arrays before use.
//
// Two constructions are ours, both recorded in docs/decisions.md:
//
//  * Construction funding.  The reference draws equity over the build years but
//    never books capex or a debt drawdown at all, so `Σ drawdown = seniorDebt`
//    and the debt roll-forward cannot both hold.  We book capex pro-rata over
//    exactly the years the reference draws equity, making
//    `drawdown_t = capex_t − equityDrawdown_t = seniorDebt / buildYears`.  The
//    signed equity cash flow is untouched.  No interest during construction,
//    matching the reference (zero IDC).
//
//  * Ramp-year shortfall.  Post-COD operating FCFE is negative in exactly the
//    ramp year, for every project: 55% generation against a full year of debt
//    service.  We split it into `distributions >= 0` and `equitySupport >= 0`,
//    so no statement line carries an implausible sign and
//    `fcfe = distributions − equityDrawdown − equitySupport` still reproduces
//    the reference exactly.
// ===========================================================================

function buildSeries(p) {
  const N = REF.years;
  const deg = degradationFor(p.tech);
  const build = buildYearsFor(p);
  const annuity = p.debt * ANNUITY_FACTOR;
  const depreciationCharge = p.capex / REF.depreciationYears;

  const s = {
    year: [], age: [],
    generationGWh: [], achievedPriceEURPerMWh: [],
    revenue: [], opex: [], ebitda: [], depreciation: [], ebit: [],
    interest: [], profitBeforeTax: [], tax: [], netIncome: [],
    capex: [], debtDrawdown: [], equityDrawdown: [], equitySupport: [],
    distributions: [], operatingFcfe: [], fcfe: [],
    taxPaid: [], interestPaid: [], principalRepaid: [],
    debtOpening: [], debtClosing: [],
    ppeGross: [], accumulatedDepreciation: [], ppeNet: [], cash: [], totalAssets: [],
    seniorDebtOutstanding: [], shareCapital: [], retainedEarnings: [],
    totalEquity: [], totalLiabilitiesAndEquity: [],
    dscr: [], gearing: [], interestCoverage: [], ebitdaMargin: [],
  };

  let outstanding = p.debt;
  let ppeGross = 0, accDep = 0, shareCapital = 0, retainedEarnings = 0, debtBalance = 0;

  for (let t = 0; t < N; t++) {
    const yr = REF.firstYear + t;
    const age = yr - p.cod;

    // ── funding ───────────────────────────────────────────────────────────
    // The reference draws equity in the years before COD, or wholly in year 0
    // when COD is the first modelled year.  We mirror that split exactly.
    let equityDrawdown = 0;
    if (yr < p.cod) equityDrawdown = p.equity / build;
    else if (t === 0 && p.cod <= REF.firstYear) equityDrawdown = p.equity;
    const fundingShare = equityDrawdown === 0 ? 0 : (p.equity === 0 ? 0 : equityDrawdown / p.equity);
    const capexSpend = p.capex * fundingShare;
    const debtDrawdown = capexSpend - equityDrawdown;

    // ── operations ────────────────────────────────────────────────────────
    let generationGWh = 0, price = 0, revenue = 0, opex = 0, ebitda = 0;
    let interest = 0, principal = 0, depreciation = 0, tax = 0;

    if (age >= 0) {
      generationGWh = p.mw * 8.760 * p.cf * Math.pow(1 - deg, age) * (age === 0 ? REF.rampYearFactor : 1);
      const underPpa = p.ppaTenor > 0 && age < p.ppaTenor;
      price = underPpa
        ? p.ppaShare * p.ppaPrice * Math.pow(REF.ppaEscalation, age)
          + (1 - p.ppaShare) * p.merchant * Math.pow(REF.merchantEscalation, age)
        : p.merchant * Math.pow(REF.merchantEscalation, age);
      revenue = generationGWh * 1000 * price / 1e6;
      opex = p.mw * 1000 * p.opexKw * Math.pow(REF.opexEscalation, age) / 1e6;
      ebitda = revenue - opex;
      interest = age < REF.debtTenorYears ? outstanding * REF.debtRate : 0;
      principal = age < REF.debtTenorYears ? Math.max(0, annuity - interest) : 0;
      if (age < REF.debtTenorYears) outstanding = Math.max(0, outstanding - principal);
      depreciation = age < REF.depreciationYears ? depreciationCharge : 0;
      // The reference takes no tax-loss carryforward: a loss year is simply
      // untaxed and never relieved later.  Preserved as-is (decision 3).
      tax = Math.max(0, (ebitda - interest - depreciation) * REF.taxRate);
    }

    const operatingFcfe = ebitda - interest - principal - tax;
    const distributions = Math.max(0, operatingFcfe);
    const equitySupport = Math.max(0, -operatingFcfe);
    const fcfe = distributions - equityDrawdown - equitySupport;
    const netIncome = ebitda - depreciation - interest - tax;

    // ── balance-sheet roll-forward ────────────────────────────────────────
    ppeGross += capexSpend;
    accDep += depreciation;
    shareCapital += equityDrawdown + equitySupport;
    retainedEarnings += netIncome - distributions;
    debtBalance += debtDrawdown - principal;
    const ppeNet = ppeGross - accDep;

    const debtService = interest + principal;

    s.year.push(yr);
    s.age.push(age);
    s.generationGWh.push(generationGWh);
    s.achievedPriceEURPerMWh.push(age >= 0 ? price : 0);
    s.revenue.push(revenue);
    s.opex.push(opex);
    s.ebitda.push(ebitda);
    s.depreciation.push(depreciation);
    s.ebit.push(ebitda - depreciation);
    s.interest.push(interest);
    s.profitBeforeTax.push(ebitda - depreciation - interest);
    s.tax.push(tax);
    s.netIncome.push(netIncome);
    s.capex.push(capexSpend);
    s.debtDrawdown.push(debtDrawdown);
    s.equityDrawdown.push(equityDrawdown);
    s.equitySupport.push(equitySupport);
    s.distributions.push(distributions);
    s.operatingFcfe.push(operatingFcfe);
    s.fcfe.push(fcfe);
    s.taxPaid.push(tax);
    s.interestPaid.push(interest);
    s.principalRepaid.push(principal);
    s.debtOpening.push(debtBalance + principal - debtDrawdown);
    s.debtClosing.push(debtBalance);
    s.ppeGross.push(ppeGross);
    s.accumulatedDepreciation.push(accDep);
    s.ppeNet.push(ppeNet);
    s.cash.push(0);
    s.totalAssets.push(ppeNet);
    s.seniorDebtOutstanding.push(debtBalance);
    s.shareCapital.push(shareCapital);
    s.retainedEarnings.push(retainedEarnings);
    s.totalEquity.push(shareCapital + retainedEarnings);
    s.totalLiabilitiesAndEquity.push(debtBalance + shareCapital + retainedEarnings);
    // Undefined where there is no debt service: null, never 0 — a 0 here would
    // read as a catastrophic cover ratio.
    s.dscr.push(debtService > 0 ? ebitda / debtService : null);
    s.gearing.push(p.capex > 0 ? debtBalance / p.capex : null);
    s.interestCoverage.push(interest > 0 ? ebitda / interest : null);
    s.ebitdaMargin.push(revenue > 0 ? ebitda / revenue : null);
  }

  return s;
}

// Minimum DSCR over the debt life, excluding the ramp year.
//
// The binding year is NOT always the first full year: contracted revenue
// escalates at 0.5% while solar generation degrades at 0.5% and opex escalates
// at 2.1%, so EBITDA declines through the PPA period for high-PPA-share solar
// and then steps up at rolloff.  Measured across the 48: age 1 binds for 34,
// and ages 9, 11, 14 or 17 bind for the other 14.  We record which, so a port
// that assumes "sculpted therefore 1.40 in year one" fails loudly.
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

// The reference screens eligibility on a capped, 2dp-rounded min DSCR
// (`p.dscr`).  Files carry both: the raw value ties out against the debt
// schedule, the rounded one is what the screens actually compare.
const REF_MIN_DSCR_CAP = 3.2;
const referenceMinDscr = (raw) => Math.round(Math.min(raw, REF_MIN_DSCR_CAP) * 100) / 100;

// Entry-pricing bands the reference clamps capex/kW into.  25 of the 48 sit on
// the floor and 3 on the cap, so "capex falls out of the revenue case" is only
// literally true for the other 20 — worth recording per file.
const CAPEX_BANDS = { Solar: [560, 950], Wind: [1050, 1700], 'Offshore wind': [2200, 3400] };

function capexClampOf(p) {
  const [lo, hi] = CAPEX_BANDS[p.tech];
  if (Math.abs(p.capexKw - lo) < 1e-9) return 'floor';
  if (Math.abs(p.capexKw - hi) < 1e-9) return 'cap';
  return 'none';
}

// Debt is the lesser of a gearing cap and a DSCR sculpt; which one bound tells
// a reader why this project's leverage is what it is.  27 capped, 21 sculpted.
const debtSizingBasisOf = (p) =>
  Math.abs(p.lev - p.maxGear) < 1e-12 ? 'max-gearing-cap' : 'dscr-sculpt';

// ===========================================================================
// 4. THE SCHEMA BOUNDARY
//
// ***  This section is the ONLY place the emitted file shape is decided.  ***
//
// `templates/project-template.json` and the tie-out table in
// `docs/pipeline-schema.md` are issue 1B's (#3) deliverables and did not exist
// when 1C was built.  Rather than guess at a shared contract, the shape below
// is an interim one derived from what the reference actually carries, posted to
// the epic for 1B to build around:
//   https://github.com/JanSchm/TerraFolio/issues/1#issuecomment-5762739466
//
// When #3's template lands, conforming to it should be a change to
// toProjectFile() and TIE_OUTS and nothing else.  Everything above and below
// works in reference-native terms.
//
// Invariants honoured here, from the epic §5:
//   * files are in €m and GWh
//   * nothing mandate-dependent is stored — no IRR, MOIC, terminal value or
//     payback appears in any file; LCOE is also excluded because it depends on
//     the assumption set's discount rate
//   * the in-file cash-flow series is `fcfe`: 30 years, NO terminal value.  The
//     hold-truncated series that does carry terminal value is named
//     `holdTruncatedFcfeWithTerminalValue` and lives only in
//     derived_expectations.json
//   * undefined ratios are null, never 0
// ===========================================================================

function toProjectFile(p, series) {
  const minDscr = minDscrFrom(series);
  return {
    schemaVersion: SCHEMA_VERSION,
    id: p.id,
    identity: {
      name: p.name,
      countryCode: p.cc,
      country: p.country,
      iso3: p.iso3,
      latitude: p.lat,
      longitude: p.lon,
      technology: p.tech,
      stage: p.stage,
      commercialOperationYear: p.cod,
      currency: p.ccy,
    },
    capacity: {
      nameplateMW: p.mw,
      netCapacityFactor: p.cf,
      p50GenerationGWh: p.gwh,
      degradationRate: degradationFor(p.tech),
      rampYearFactor: REF.rampYearFactor,
    },
    capitalStructure: {
      totalProjectCostEURm: p.capex,
      capexPerKW: p.capexKw,
      seniorDebtEURm: p.debt,
      equityEURm: p.equity,
      gearing: p.lev,
      maxGearing: p.maxGear,
      debtRate: REF.debtRate,
      debtTenorYears: REF.debtTenorYears,
      sizingDSCR: REF.sizingDSCR,
      // Raw minimum over the debt life, excluding the ramp year. Ties out
      // against the debt schedule exactly.
      minDSCR: minDscr.raw,
      // What the reference's screens actually compare: min(raw, 3.2) to 2dp.
      minDSCRReference: referenceMinDscr(minDscr.raw),
      minDSCRBindingYearAge: minDscr.bindingAge,
      minDSCRExcludesRampYear: true,
      minDSCRCap: REF_MIN_DSCR_CAP,
      debtSizingBasis: debtSizingBasisOf(p),
      capexPerKWClamp: capexClampOf(p),
      constructionYears: buildYearsFor(p),
    },
    contracts: {
      ppaShare: p.ppaShare,
      ppaPriceEURPerMWh: p.ppaPrice,
      ppaTenorYears: p.ppaTenor,
      capturePriceEURPerMWh: p.merchant,
      baseloadEURPerMWh: p.baseload,
      captureFactor: REF.captureFactor[p.tech],
      opexPerKWYear: p.opexKw,
      ppaEscalation: REF.ppaEscalation,
      merchantEscalation: REF.merchantEscalation,
      opexEscalation: REF.opexEscalation,
    },
    risk: {
      developmentRiskScore: p.devRisk,
      gridSecured: p.gridSecured,
      omPartner: p.omPartner,
    },
    statements: {
      firstYear: REF.firstYear,
      years: REF.years,
      taxRate: REF.taxRate,
      depreciationYears: REF.depreciationYears,
      year: series.year,
      incomeStatement: {
        revenue: series.revenue,
        opex: series.opex,
        ebitda: series.ebitda,
        depreciation: series.depreciation,
        ebit: series.ebit,
        interest: series.interest,
        profitBeforeTax: series.profitBeforeTax,
        tax: series.tax,
        netIncome: series.netIncome,
      },
      cashFlow: {
        ebitda: series.ebitda,
        taxPaid: series.taxPaid,
        interestPaid: series.interestPaid,
        principalRepaid: series.principalRepaid,
        operatingFcfe: series.operatingFcfe,
        capex: series.capex,
        debtDrawdown: series.debtDrawdown,
        equityDrawdown: series.equityDrawdown,
        equitySupport: series.equitySupport,
        distributions: series.distributions,
        fcfe: series.fcfe,
      },
      balanceSheet: {
        ppeGross: series.ppeGross,
        accumulatedDepreciation: series.accumulatedDepreciation,
        ppeNet: series.ppeNet,
        cash: series.cash,
        totalAssets: series.totalAssets,
        seniorDebtOutstanding: series.seniorDebtOutstanding,
        shareCapital: series.shareCapital,
        retainedEarnings: series.retainedEarnings,
        totalEquity: series.totalEquity,
        totalLiabilitiesAndEquity: series.totalLiabilitiesAndEquity,
      },
      debtSchedule: {
        opening: series.debtOpening,
        drawdown: series.debtDrawdown,
        interest: series.interest,
        principal: series.principalRepaid,
        closing: series.debtClosing,
      },
      // Blanket policy: every ratio is null when its denominator is zero.
      // Never 0 (which reads as a catastrophic cover) and never Infinity
      // (which JSON.stringify would quietly turn into null anyway, leaving the
      // two cases indistinguishable).
      ratios: {
        dscr: series.dscr,
        gearing: series.gearing,
        interestCoverage: series.interestCoverage,
        ebitdaMargin: series.ebitdaMargin,
      },
      generationGWh: series.generationGWh,
      achievedPriceEURPerMWh: series.achievedPriceEURPerMWh,
    },
    provenance: {
      source: 'javascript-reference',
      bundle: BUNDLE_NAME,
      bundleSha256: BUNDLE_SHA256,
      extractedBy: `tools/extract_reference.mjs@${TOOL_VERSION}`,
      // How much of this file is contract and how much is prediction. The
      // earlier the stage, the more of it is prediction (epic §2).
      basis: {
        capex: p.stage === 'Construction' ? 'contracted' : 'modelled',
        generation: 'modelled-p50',
        contractedRevenue: p.ppaTenor > 0 ? 'contracted' : 'none',
        merchantRevenue: 'modelled',
        opex: p.omPartner ? 'contracted' : 'modelled',
        seniorDebt: 'modelled',
        gridConnection: p.gridSecured ? 'secured' : 'application-pending',
      },
      contractedRevenueShare: p.ppaShare,
      notes: [
        'Construction funding: capex and the debt drawdown are booked pro-rata across the years the reference draws equity, so the debt roll-forward and the funding totals tie. Zero interest during construction, matching the reference. See docs/decisions.md.',
        'Ramp-year shortfall: operating FCFE is negative in the ramp year, split into distributions and equitySupport so no statement line carries an implausible sign. Signed FCFE is unchanged. See docs/decisions.md.',
        'No tax-loss carryforward: the reference leaves a loss year untaxed and never relieves it later. Preserved as-is.',
        'Statements are EUR-denominated regardless of identity.currency; the reference carries no FX. See docs/decisions.md and epic open question 8.',
        'ppaTenorYears is one of 10, 12, 15 or 20 across all 48 projects, so the reference\'s merchant-only pricing branch (ppaTenor <= 1) is never exercised by this corpus. A port must not treat these files as evidence that branch works.',
      ],
    },
  };
}

// ---------------------------------------------------------------------------
// Tie-outs.  Each is a named predicate over an emitted file, so 1B's table in
// docs/pipeline-schema.md maps onto this list one for one.  Tolerance is the
// epic's: €0.01m absolute or 0.1% relative.
// ---------------------------------------------------------------------------

const ABS_TOL = 0.01;   // €m
const REL_TOL = 0.001;  // 0.1%

function within(a, b) {
  const d = Math.abs(a - b);
  return d <= ABS_TOL || d <= REL_TOL * Math.max(Math.abs(a), Math.abs(b));
}

const sum = (xs) => xs.reduce((a, x) => a + x, 0);

// Each tie-out returns the worst absolute residual it saw (0 when it holds
// exactly), so the run can report actual numbers rather than a pass/fail bit.
function worst(pairs) {
  let w = 0;
  for (const [a, b] of pairs) {
    if (!within(a, b)) return { ok: false, residual: Math.abs(a - b) };
    w = Math.max(w, Math.abs(a - b));
  }
  return { ok: true, residual: w };
}

const zip = (xs, f) => xs.map((_, i) => f(i));

// Project age per year, derived from what the file carries rather than from a
// private key — the tie-outs must be checkable against an emitted file alone.
const agesOf = (f) => f.statements.year.map((y) => y - f.identity.commercialOperationYear);

const TIE_OUTS = [
  { name: 'income: ebitda = revenue - opex', check: (f) => {
    const is = f.statements.incomeStatement;
    return worst(zip(is.revenue, (i) => [is.ebitda[i], is.revenue[i] - is.opex[i]])); } },

  { name: 'income: ebit = ebitda - depreciation', check: (f) => {
    const is = f.statements.incomeStatement;
    return worst(zip(is.ebit, (i) => [is.ebit[i], is.ebitda[i] - is.depreciation[i]])); } },

  { name: 'income: profitBeforeTax = ebit - interest', check: (f) => {
    const is = f.statements.incomeStatement;
    return worst(zip(is.ebit, (i) => [is.profitBeforeTax[i], is.ebit[i] - is.interest[i]])); } },

  { name: 'income: netIncome = profitBeforeTax - tax', check: (f) => {
    const is = f.statements.incomeStatement;
    return worst(zip(is.ebit, (i) => [is.netIncome[i], is.profitBeforeTax[i] - is.tax[i]])); } },

  { name: 'income: tax = max(0, profitBeforeTax x taxRate)', check: (f) => {
    const is = f.statements.incomeStatement, r = f.statements.taxRate;
    return worst(zip(is.tax, (i) => [is.tax[i], Math.max(0, is.profitBeforeTax[i] * r)])); } },

  { name: 'cash flow: fcfe = ebitda - interest - principal - tax - equityDrawdown', check: (f) => {
    const c = f.statements.cashFlow;
    return worst(zip(c.fcfe, (i) => [
      c.fcfe[i],
      c.ebitda[i] - c.interestPaid[i] - c.principalRepaid[i] - c.taxPaid[i] - c.equityDrawdown[i],
    ])); } },

  { name: 'cash flow: fcfe = distributions - equityDrawdown - equitySupport', check: (f) => {
    const c = f.statements.cashFlow;
    return worst(zip(c.fcfe, (i) => [
      c.fcfe[i], c.distributions[i] - c.equityDrawdown[i] - c.equitySupport[i],
    ])); } },

  { name: 'cash flow: capex_t = debtDrawdown_t + equityDrawdown_t', check: (f) => {
    const c = f.statements.cashFlow;
    return worst(zip(c.capex, (i) => [c.capex[i], c.debtDrawdown[i] + c.equityDrawdown[i]])); } },

  { name: 'funding: sum capex = totalProjectCost', check: (f) =>
    worst([[sum(f.statements.cashFlow.capex), f.capitalStructure.totalProjectCostEURm]]) },

  { name: 'funding: sum debtDrawdown = seniorDebt', check: (f) =>
    worst([[sum(f.statements.cashFlow.debtDrawdown), f.capitalStructure.seniorDebtEURm]]) },

  { name: 'funding: sum equityDrawdown = equity', check: (f) =>
    worst([[sum(f.statements.cashFlow.equityDrawdown), f.capitalStructure.equityEURm]]) },

  { name: 'capital: equity = totalProjectCost - seniorDebt', check: (f) => {
    const k = f.capitalStructure;
    return worst([[k.equityEURm, k.totalProjectCostEURm - k.seniorDebtEURm]]); } },

  { name: 'capital: gearing = seniorDebt / totalProjectCost', check: (f) => {
    const k = f.capitalStructure;
    return worst([[k.gearing, k.seniorDebtEURm / k.totalProjectCostEURm]]); } },

  { name: 'capital: capexPerKW = totalProjectCost / nameplateMW', check: (f) =>
    worst([[f.capitalStructure.capexPerKW,
            f.capitalStructure.totalProjectCostEURm * 1e6 / (f.capacity.nameplateMW * 1000)]]) },

  { name: 'debt: closing = opening + drawdown - principal', check: (f) => {
    const d = f.statements.debtSchedule;
    return worst(zip(d.closing, (i) => [d.closing[i], d.opening[i] + d.drawdown[i] - d.principal[i]])); } },

  { name: 'debt: opening_t = closing_{t-1}', check: (f) => {
    const d = f.statements.debtSchedule;
    return worst(zip(d.opening, (i) => [d.opening[i], i === 0 ? 0 : d.closing[i - 1]])); } },

  { name: 'debt: sum principal = seniorDebt and closes at zero', check: (f) => {
    const d = f.statements.debtSchedule;
    return worst([
      [sum(d.principal), f.capitalStructure.seniorDebtEURm],
      [d.closing[d.closing.length - 1], 0],
    ]); } },

  // Interest accrues on the balance AFTER that year's drawdown. It matters
  // only in the COD year of a project whose COD is the first modelled year:
  // the reference draws the facility and charges a full year's interest on it
  // in the same year. In every other year the drawdown is zero and this
  // reduces to interest on the opening balance.
  { name: 'debt: interest = (opening + drawdown) x debtRate over the debt life', check: (f) => {
    const d = f.statements.debtSchedule, r = f.capitalStructure.debtRate, age = agesOf(f);
    return worst(zip(d.interest, (i) =>
      age[i] >= 0 && age[i] < f.capitalStructure.debtTenorYears
        ? [d.interest[i], (d.opening[i] + d.drawdown[i]) * r]
        : [d.interest[i], 0])); } },

  // Zero interest during construction: the reference models no interest during
  // construction at all, so no IDC is capitalised into capex. Recorded in
  // docs/decisions.md.
  { name: 'debt: no interest before COD', check: (f) => {
    const d = f.statements.debtSchedule, age = agesOf(f);
    return worst(zip(d.interest, (i) => [age[i] < 0 ? d.interest[i] : 0, 0])); } },

  { name: 'balance sheet: totalAssets = totalLiabilitiesAndEquity', check: (f) => {
    const b = f.statements.balanceSheet;
    return worst(zip(b.totalAssets, (i) => [b.totalAssets[i], b.totalLiabilitiesAndEquity[i]])); } },

  { name: 'balance sheet: ppeNet = ppeGross - accumulatedDepreciation', check: (f) => {
    const b = f.statements.balanceSheet;
    return worst(zip(b.ppeNet, (i) => [b.ppeNet[i], b.ppeGross[i] - b.accumulatedDepreciation[i]])); } },

  { name: 'balance sheet: totalEquity = shareCapital + retainedEarnings', check: (f) => {
    const b = f.statements.balanceSheet;
    return worst(zip(b.totalEquity, (i) => [b.totalEquity[i], b.shareCapital[i] + b.retainedEarnings[i]])); } },

  { name: 'balance sheet: seniorDebtOutstanding matches the debt schedule closing', check: (f) => {
    const b = f.statements.balanceSheet, d = f.statements.debtSchedule;
    return worst(zip(b.seniorDebtOutstanding, (i) => [b.seniorDebtOutstanding[i], d.closing[i]])); } },

  { name: 'depreciation: sum = totalProjectCost over the depreciation life', check: (f) =>
    worst([[sum(f.statements.incomeStatement.depreciation), f.capitalStructure.totalProjectCostEURm]]) },

  { name: 'ratios: dscr = ebitda / (interest + principal)', check: (f) => {
    const r = f.statements.ratios, d = f.statements.debtSchedule, is = f.statements.incomeStatement;
    const pairs = [];
    for (let i = 0; i < r.dscr.length; i++) {
      const service = d.interest[i] + d.principal[i];
      if (r.dscr[i] === null) { if (service > 0) return { ok: false, residual: service }; continue; }
      pairs.push([r.dscr[i], is.ebitda[i] / service]);
    }
    return worst(pairs); } },

  { name: 'ratios: minDSCR is the minimum over the debt life excluding the ramp year', check: (f) => {
    const age = agesOf(f), r = f.statements.ratios;
    let min = Infinity;
    for (let i = 0; i < r.dscr.length; i++) {
      if (age[i] <= 0 || age[i] >= f.capitalStructure.debtTenorYears || r.dscr[i] === null) continue;
      min = Math.min(min, r.dscr[i]);
    }
    return worst([[f.capitalStructure.minDSCR, min]]); } },

  { name: 'capacity: p50GenerationGWh = nameplateMW x 8.760 x netCapacityFactor', check: (f) =>
    worst([[f.capacity.p50GenerationGWh, f.capacity.nameplateMW * 8.760 * f.capacity.netCapacityFactor]]) },

  { name: 'generation: first operating year is the ramp year at 55%', check: (f) => {
    const age = agesOf(f), g = f.statements.generationGWh;
    const i = age.indexOf(0);
    if (i < 0) return { ok: false, residual: NaN };
    return worst([[g[i], f.capacity.p50GenerationGWh * (1 - f.capacity.degradationRate) ** 0 * f.capacity.rampYearFactor]]); } },
];

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

function buildDerivedExpectations(makeRef, projects) {
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
    seriesNaming: {
      thirtyYear: 'fcfe — 30 years, NO terminal value. Lives in the pipeline statement files.',
      holdTruncated: 'holdTruncatedFcfeWithTerminalValue — truncated at the hold year, WITH terminal value. Mandate-dependent, so it appears only here and never in a statement file.',
    },
    projects: projects.map((p) => {
      const out = {
        id: p.id,
        name: p.name,
        technology: p.tech,
        capexEURm: p.capex,
        capexPerKW: p.capexKw,
        capexPerKWClamp: capexClampOf(p),
        seniorDebtEURm: p.debt,
        equityEURm: p.equity,
        gearing: p.lev,
        debtSizingBasis: debtSizingBasisOf(p),
        minDSCRReference: p.dscr,
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
  technology: { Solar: 22, Wind: 22, 'Offshore wind': 4 },
  stage: { 'Ready-to-build': 20, Greenfield: 17, Construction: 11 },
  countries: 14,
  codFrom: 2027,
  codTo: 2032,
};

function checkPipeline(projects, files) {
  const report = { tieOuts: [], totals: {}, worstTieOut: { name: null, residual: 0 } };

  if (projects.length !== 48) fail(`expected 48 projects, got ${projects.length}`);
  const ids = projects.map((p) => p.id);
  const sorted = [...ids].sort();
  if (ids.join(',') !== sorted.join(',')) fail('projects are not in ascending id order');
  if (new Set(ids).size !== 48) fail('duplicate project ids');

  // Totals, computed from the EMITTED FILES rather than from the reference, so
  // this checks what actually lands on disk.
  const t = {
    mw: files.reduce((a, f) => a + f.capacity.nameplateMW, 0),
    capex: files.reduce((a, f) => a + f.capitalStructure.totalProjectCostEURm, 0),
    equity: files.reduce((a, f) => a + f.capitalStructure.equityEURm, 0),
    gwh: files.reduce((a, f) => a + f.capacity.p50GenerationGWh, 0),
    debt: files.reduce((a, f) => a + f.capitalStructure.seniorDebtEURm, 0),
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
  const tech = tally('tech', (f) => f.identity.technology);
  const stage = tally('stage', (f) => f.identity.stage);
  for (const [k, v] of Object.entries(PUBLISHED_COMPOSITION.technology)) {
    if (tech[k] !== v) fail(`technology split: ${k} is ${tech[k]}, expected ${v}`);
  }
  for (const [k, v] of Object.entries(PUBLISHED_COMPOSITION.stage)) {
    if (stage[k] !== v) fail(`stage split: ${k} is ${stage[k]}, expected ${v}`);
  }
  const countries = new Set(files.map((f) => f.identity.countryCode));
  if (countries.size !== PUBLISHED_COMPOSITION.countries) fail(`expected ${PUBLISHED_COMPOSITION.countries} countries, got ${countries.size}`);
  const cods = files.map((f) => f.identity.commercialOperationYear);
  if (Math.min(...cods) !== PUBLISHED_COMPOSITION.codFrom || Math.max(...cods) !== PUBLISHED_COMPOSITION.codTo) {
    fail(`COD range is ${Math.min(...cods)}-${Math.max(...cods)}, expected ${PUBLISHED_COMPOSITION.codFrom}-${PUBLISHED_COMPOSITION.codTo}`);
  }

  // Filenames must be unique after case folding: on a case-insensitive
  // filesystem a collision would silently overwrite a fixture.
  const folded = new Set();
  for (const f of files) {
    const name = `${f.id}-${slug(f.identity.name)}.json`.toLowerCase();
    if (folded.has(name)) fail(`filename collision after case folding: ${name}`);
    folded.add(name);
  }

  // The tie-outs themselves.
  for (const tie of TIE_OUTS) {
    let worstResidual = 0;
    for (const f of files) {
      const res = tie.check(f);
      if (!res.ok) fail(`tie-out failed for ${f.id}: ${tie.name} (residual ${res.residual})`);
      worstResidual = Math.max(worstResidual, res.residual);
    }
    report.tieOuts.push({ name: tie.name, worstResidual });
    if (worstResidual > report.worstTieOut.residual) report.worstTieOut = { name: tie.name, residual: worstResidual };
  }

  // Nothing mandate-dependent may appear anywhere in a statement file.
  const forbidden = /"(irr|moic|payback|terminalValue|holdYears|hold|exitMultiple|hurdle|capital|target)"/i;
  for (const f of files) {
    const hit = canonicalJson(f, f.id).match(forbidden);
    if (hit) fail(`mandate-dependent key ${hit[0]} found in ${f.id} — files must carry nothing that turns on the mandate`);
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
      if (!Object.is(s.generationGWh[t], p.gen[t])) fail(`${p.id} year ${t}: reconstructed generation ${s.generationGWh[t]} != reference ${p.gen[t]}`);
    }
    // The reference's own rounded min DSCR must fall out of our raw one.
    const { raw } = minDscrFrom(s);
    if (referenceMinDscr(raw) !== p.dscr) {
      fail(`${p.id}: round(min(${raw}, ${REF_MIN_DSCR_CAP}), 2) = ${referenceMinDscr(raw)} != reference p.dscr ${p.dscr}`);
    }
    // Exactly one negative operating-FCFE year per project, and it is the ramp.
    const negatives = s.operatingFcfe.map((v, i) => (v < 0 && s.age[i] >= 0 ? s.age[i] : null)).filter((x) => x !== null);
    if (negatives.length !== 1 || negatives[0] !== 0) {
      fail(`${p.id}: expected exactly one negative operating-FCFE year at age 0, got ages [${negatives}]`);
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
    artefacts.set(path.join('pipeline', `${f.id}-${slug(f.identity.name)}.json`), canonicalJson(f, f.id));
  }
  artefacts.set('js_prng.json', canonicalJson(buildPrngFixture(ref, projects), 'js_prng'));
  artefacts.set('derived_expectations.json', canonicalJson(buildDerivedExpectations(makeRef, projects), 'derived_expectations'));

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

  // Build and validate in a staging directory, then swap it in. Nothing touches
  // the committed fixtures until every assertion has passed.
  const staging = stagingPathFor(FIXTURES_DIR);
  fs.rmSync(staging, { recursive: true, force: true });
  let built;
  try {
    built = await build(staging);
    writeArtefacts(staging, built.artefacts);
  } catch (err) {
    fs.rmSync(staging, { recursive: true, force: true });
    throw err;
  }

  const before = new Set(listFixtureFiles(FIXTURES_DIR));
  commitStaged(FIXTURES_DIR, staging);
  const removed = [...before].filter((rel) => !built.artefacts.has(rel)).sort();

  process.stdout.write(`${summarise(built)}\n`);
  if (removed.length) {
    process.stdout.write(`Removed ${removed.length} stale file(s):\n${removed.map((r) => `  ${r}`).join('\n')}\n`);
  }
  process.stdout.write(`Written to ${path.relative(ROOT, FIXTURES_DIR)}/\n`);
  return 0;
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
