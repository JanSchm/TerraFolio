/**
 * feasibility.js — the §5.4 mandate feasibility computation.
 *
 * Nine screens over a /pipeline payload, four footer figures, six warnings. It runs
 * on every keystroke in the mandate, which is why it is here rather than on the
 * server: epic §7 budgets the feedback at under 100ms, and a round trip cannot make
 * that. It is also why it is a pure function of (payload, mandate) with no DOM, no
 * Alpine and no import but format.js — issue #12 requires it in bare node to check
 * it against issue #6's Python implementation, and two implementations only agree
 * if one of them is easy to call.
 *
 * Nothing here is a constant that belongs in the assumption set. The risk-appetite
 * caps in particular come off the payload, because epic §5 requires every band to be
 * an auditable configuration change rather than an edit to this file. The reference
 * implementation hard-codes them; we do not.
 *
 * The candidate field names are the proposal posted to issue #1, pending docs/api.md.
 * They are read in exactly one place — `candidate()` — so reconciling with the real
 * wire contract is a single edit rather than a search across this file.
 *
 * Classic script in the browser, CommonJS under node. See docs/decisions.md D1.
 */
(function (root) {
  'use strict';

  var fmt = (typeof require === 'function')
    ? require('./format.js')
    : root.TerraFolio && root.TerraFolio.format;
  if (!fmt) throw new Error('feasibility.js requires format.js');

  /* ── The wire adapter ───────────────────────────────────────────────────────
     The one place /pipeline field names appear. Money arrives in €m with an
     explicit _m suffix and generation in GWh, per epic §5. */
  function candidate(raw) {
    return {
      id: raw.id,
      country: raw.country,
      stage: raw.stage,
      technology: raw.technology,
      mw: raw.mw,
      cod: raw.cod,
      minDscr: raw.min_dscr,
      devRisk: raw.dev_risk,
      gridSecured: raw.grid_secured,
      omPartner: raw.om_partner,
      currency: raw.currency,
      capexM: raw.capex_m,
      seniorDebtM: raw.senior_debt_m,
      equityM: raw.equity_m,
    };
  }

  /** Candidates in canonical order: by id ascending, everywhere (epic §5). */
  function candidates(payload) {
    return ((payload && payload.candidates) || [])
      .map(candidate)
      .sort(function (a, b) { return a.id < b.id ? -1 : a.id > b.id ? 1 : 0; });
  }

  /**
   * The development-risk ceiling for a risk appetite.
   * Read from the payload's resolved assumption set; a mandate whose appetite the
   * assumption set does not define is a configuration error, not a silent pass.
   */
  function riskCap(payload, appetite) {
    var caps = (payload && payload.assumptions && payload.assumptions.risk_caps) || {};
    var cap = caps[appetite];
    if (typeof cap !== 'number') {
      throw new Error('no risk cap configured for appetite "' + appetite + '"');
    }
    return cap;
  }

  /* ── The nine screens ───────────────────────────────────────────────────────
     Each is exported on its own so each can be tested on its own. Every one takes
     (candidate, mandate) and returns a boolean; none of them looks at any other. */

  var screens = {
    /** 1. The project's country is on the mandate's eligible list. */
    country: function (c, m) {
      return (m.countries || []).indexOf(c.country) !== -1;
    },

    /** 2. The project's development stage is in scope. */
    stage: function (c, m) {
      return (m.stages || []).indexOf(c.stage) !== -1;
    },

    /** 3. Commercial operation falls inside the mandate's COD window, inclusive. */
    codWindow: function (c, m) {
      return c.cod >= m.codFrom && c.cod <= m.codTo;
    },

    /** 4. The project's own minimum DSCR clears the mandate floor. */
    minDscr: function (c, m) {
      return c.minDscr >= m.minDscr;
    },

    /** 5. Development risk is at or under the appetite's ceiling. */
    riskCap: function (c, m, cap) {
      return c.devRisk <= cap;
    },

    /** 6. Grid connection is secured, when the mandate demands it. */
    gridSecured: function (c, m) {
      return !m.gridOnly || c.gridSecured === true;
    },

    /** 7. An O&M partner is contracted, when the mandate demands it. */
    omPartner: function (c, m) {
      return !m.omOnly || c.omPartner === true;
    },

    /**
     * 8. Revenue is euro-denominated, when the mandate demands it.
     * A non-EUR file's statements are in that currency and are only ever screened
     * out, never converted here (epic §12 open question 8).
     */
    currency: function (c, m) {
      return !m.hedged || c.currency === 'EUR';
    },

    /** 9. The user has not excluded the project by hand. */
    notExcluded: function (c, m) {
      return (m.excluded || []).indexOf(c.id) === -1;
    },
  };

  var SCREEN_ORDER = [
    'country', 'stage', 'codWindow', 'minDscr', 'riskCap',
    'gridSecured', 'omPartner', 'currency', 'notExcluded',
  ];

  /** True when a candidate passes all nine. */
  function passes(c, m, cap) {
    for (var i = 0; i < SCREEN_ORDER.length; i++) {
      if (!screens[SCREEN_ORDER[i]](c, m, cap)) return false;
    }
    return true;
  }

  /** Which screens a candidate fails, in order. Drives the "why was this dropped" case. */
  function failedScreens(c, m, cap) {
    return SCREEN_ORDER.filter(function (name) { return !screens[name](c, m, cap); });
  }

  /* ── The four footer figures ─────────────────────────────────────────────── */

  /**
   * What the eligible pool is, in aggregate.
   *
   * Solar mix is capacity-weighted and leverage is cost-weighted, matching the
   * portfolio aggregates the optimiser reports, so the mandate and the result
   * cannot disagree about what "62% solar" means. Both are undefined rather than
   * zero when the pool is empty — there is no mix without a pool.
   */
  function aggregate(pool) {
    var mw = 0, solarMw = 0, capexM = 0, debtM = 0, equityM = 0;
    pool.forEach(function (c) {
      mw += c.mw;
      if (c.technology === 'solar') solarMw += c.mw;
      capexM += c.capexM;
      debtM += c.seniorDebtM;
      equityM += c.equityM;
    });
    return {
      count: pool.length,
      mw: mw,
      capexM: capexM,
      equityM: equityM,
      seniorDebtM: debtM,
      solarMix: mw > 0 ? solarMw / mw : NaN,
      leverage: capexM > 0 ? debtM / capexM : NaN,
    };
  }

  /* ── The six warnings, in severity order ─────────────────────────────────────
     Each carries a severity and a mark as well as its text, so nothing is
     conveyed by colour alone (epic §5). The breach colour is accent-800: there
     is no red in this system. */

  function warnings(pool, agg, mandate, total) {
    var out = [];
    var empty = pool.length === 0;

    if (empty) {
      out.push({
        id: 'no-candidates',
        severity: 'breach',
        mark: '×',
        text: 'No candidates pass the current screens. '
            + 'Widen countries, stages or the COD window.',
      });
    }

    if (!empty && agg.mw < mandate.target) {
      out.push({
        id: 'below-target',
        severity: 'breach',
        mark: '!',
        text: 'Eligible pipeline is ' + fmt.mw(agg.mw)
            + ' — below the ' + fmt.mw(mandate.target) + ' target.',
      });
    }

    if (!empty && agg.mw >= mandate.target && agg.equityM < mandate.capital * 0.9) {
      out.push({
        id: 'under-absorbed',
        severity: 'note',
        mark: '!',
        text: 'Full pipeline absorbs only ' + fmt.eurM(agg.equityM)
            + ' of the ' + fmt.eurM(mandate.capital) + ' available.',
      });
    }

    if (!empty && fmt.defined(agg.solarMix) && agg.solarMix < mandate.solarShare - 0.2) {
      out.push({
        id: 'solar-unreachable',
        severity: 'note',
        mark: '!',
        text: 'Solar target of ' + fmt.percent(mandate.solarShare)
            + ' may be unreachable: eligible pool is ' + fmt.percent(agg.solarMix) + ' solar.',
      });
    }

    if (!empty && fmt.defined(agg.leverage) && agg.leverage < mandate.minLev) {
      out.push({
        id: 'leverage-unsupported',
        severity: 'breach',
        mark: '!',
        text: 'Minimum leverage of ' + fmt.percent(mandate.minLev)
            + ' exceeds what the eligible pool supports ('
            + fmt.percent(agg.leverage) + ').',
      });
    }

    var locked = (mandate.locked || []).length;
    if (locked > 0) {
      out.push({
        id: 'locked-excluded',
        severity: 'note',
        mark: '•',
        text: locked + ' project(s) locked in; ' + (mandate.excluded || []).length + ' excluded.',
      });
    }

    return out;
  }

  /**
   * The whole §5.4 computation.
   *
   * Returns the eligible pool, the four footer figures pre-formatted through
   * format.js, and the warnings. `total` is the unscreened pipeline size, which
   * the "N of M" figure needs and the pool alone cannot give.
   */
  function feasibility(payload, mandate) {
    var all = candidates(payload);
    var cap = riskCap(payload, mandate.risk);
    var pool = all.filter(function (c) { return passes(c, mandate, cap); });
    var agg = aggregate(pool);

    return {
      pool: pool,
      total: all.length,
      aggregate: agg,
      figures: {
        candidates: { value: agg.count, of: all.length,
          display: fmt.count(agg.count), ofDisplay: fmt.count(all.length) },
        capacity: { value: agg.mw, display: fmt.mw(agg.mw) },
        equity: { value: agg.equityM, display: fmt.eurM(agg.equityM) },
        mixAndLeverage: {
          solarMix: agg.solarMix,
          leverage: agg.leverage,
          solarDisplay: fmt.percent(agg.solarMix),
          leverageDisplay: fmt.percent(agg.leverage),
        },
      },
      warnings: warnings(pool, agg, mandate, all.length),
    };
  }

  var api = {
    feasibility: feasibility,
    screens: screens,
    SCREEN_ORDER: SCREEN_ORDER,
    passes: passes,
    failedScreens: failedScreens,
    aggregate: aggregate,
    warnings: warnings,
    candidates: candidates,
    riskCap: riskCap,
  };

  root.TerraFolio = root.TerraFolio || {};
  root.TerraFolio.feasibility = api;

  if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
