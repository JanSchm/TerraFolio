/**
 * Three holdings rows, one per state ui-contract.md §5.4 gives a row.
 *
 * Fixture data, which the repository's no-canned-data rule exempts: the pages ship
 * with `rows: []` and issue #11 assigns the run's own holdings array. These exist so
 * the row template can be rendered and audited before that lands — a template that
 * nothing ever renders is a template nothing checks.
 *
 * Field names and units are api.md §8.2's: euros in €m, shares and rates as
 * fractions of one, `null` for an undefined IRR or an unlevered project's DSCR.
 */
const SELECTED = {
  id: 'P01', name: 'Almonte Solar', country: 'ES', technology: 'solar', stage: 'greenfield',
  capacityMw: 180, codYear: 2028, totalCapex_m: 141, equity_m: 56, gearing: 0.6,
  netCapacityFactor: 0.2461, annualGenerationGwh: 388, lcoe: 41, ppaShare: 0.72,
  equityIrr: 0.1241, minDscr: 1.38, developmentRiskScore: 2.7,
  locked: false, selected: true,
};

/** Locked into the next run: accent-100 ground, so it owes a filled square. */
const LOCKED = {
  ...SELECTED, id: 'P23', name: 'Nordjylland Vind', country: 'DK',
  technology: 'onshore_wind', stage: 'ready_to_build', equityIrr: 0.131, minDscr: 1.52,
  locked: true, selected: true,
};

/**
 * Rejected by the optimiser and only visible under "Show all candidates": a
 * neutral-200 ground, so it owes `Not selected` in the row's name. Its Min DSCR is
 * under the 1.25× default floor, and its IRR is undefined — an em dash, never a zero.
 */
const NOT_SELECTED = {
  ...SELECTED, id: 'P12', name: 'Thessaly Solar', country: 'GR',
  technology: 'offshore_wind', stage: 'construction', equityIrr: null, minDscr: 1.18,
  locked: false, selected: false,
};

module.exports = { SELECTED, LOCKED, NOT_SELECTED, ROWS: [SELECTED, LOCKED, NOT_SELECTED] };
