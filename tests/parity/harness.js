/**
 * Run web/js/feasibility.js over one committed pair and print the parity view.
 *
 *   node tests/parity/harness.js tests/parity/pairs/01-baseline.json
 *
 * The view is deliberately narrow: counts, codes, flags, the eligible ids and the
 * per-project failed screens, plus the six footer figures. Message strings and
 * `detail` payloads are left out — the Python core emits no strings at all (they live
 * in api/messages.py, pinned to ui-contract.md by its own test) and the two `detail`
 * vocabularies differ in units and keys, so comparing them would compare conventions
 * rather than answers.
 *
 * CommonJS on purpose. `feasibility.js` is a dual IIFE because §12 requires the pages
 * to open from `file://`, where ES module imports are CORS-blocked (decisions A-15), so
 * `require` is the only way in. There is no package.json above this directory, so node
 * treats this file as CommonJS without one.
 */

'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..', '..');
const feasibility = require(path.join(ROOT, 'web', 'js', 'feasibility.js'));

/** feasibility.js's screen names, mapped onto the ones docs/api.md §5 puts on the wire. */
const SCREEN_NAMES = {
  country: 'countries',
  stage: 'stages',
  codWindow: 'codWindow',
  minDscr: 'minDscr',
  riskCap: 'riskScore',
  gridSecured: 'gridSecured',
  omContracted: 'omContracted',
  currency: 'eurRevenue',
  notExcluded: 'exclusions',
};

/** The wire order, so a failed-screen list can be compared element by element. */
const WIRE_ORDER = [
  'countries', 'stages', 'codWindow', 'minDscr', 'gridSecured',
  'eurRevenue', 'omContracted', 'riskScore', 'exclusions',
];

function view(payload, mandate, locks) {
  const answer = feasibility.feasibility(payload, mandate, locks);
  const cap = feasibility.riskCap(payload, mandate.riskAppetite);
  const excluded = (locks && locks.excludedIds) || [];

  const failed = {};
  feasibility.projects(payload).forEach(function (project) {
    const names = feasibility
      .failedScreens(project, mandate, cap, excluded)
      .map(function (name) {
        if (!(name in SCREEN_NAMES)) throw new Error('unmapped screen name: ' + name);
        return SCREEN_NAMES[name];
      })
      .sort(function (a, b) { return WIRE_ORDER.indexOf(a) - WIRE_ORDER.indexOf(b); });
    if (names.length) failed[project.id] = names;
  });

  return {
    eligibleCount: answer.eligibleCount,
    totalCount: answer.totalCount,
    eligibleCapacityMw: answer.eligibleCapacityMw,
    eligibleEquity_m: answer.eligibleEquity_m,
    eligibleSolarShare: answer.eligibleSolarShare,
    eligibleGearing: answer.eligibleGearing,
    lockedEquity_m: answer.lockedEquity_m,
    runnable: answer.runnable,
    warnings: answer.warnings.map(function (warning) { return warning.code; }),
    eligibleIds: answer.pool.map(function (project) { return project.id; }).sort(),
    failedScreens: failed,
  };
}

function main(argv) {
  if (argv.length !== 1) {
    process.stderr.write('usage: node harness.js <pair.json>\n');
    return 2;
  }
  const pairPath = path.resolve(argv[0]);
  const pair = JSON.parse(fs.readFileSync(pairPath, 'utf8'));
  const payload = JSON.parse(
    fs.readFileSync(path.join(path.dirname(pairPath), pair.payload), 'utf8')
  );
  const locks = pair.locks || { lockedIds: [], excludedIds: [] };
  process.stdout.write(JSON.stringify(view(payload, pair.mandate, locks)) + '\n');
  return 0;
}

if (require.main === module) process.exitCode = main(process.argv.slice(2));
module.exports = { view: view, SCREEN_NAMES: SCREEN_NAMES };
