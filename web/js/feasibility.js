/**
 * feasibility.js — the §5.4 mandate feasibility computation.
 *
 * Nine screens over a GET /pipeline payload, the eligible-pool aggregates, and the
 * warnings, returned in the shape POST /mandate/preview answers with. api.md §5 says
 * the two must agree field for field, and that if they diverge the client is wrong —
 * so this is written against that response rather than against a convenient local shape.
 *
 * It runs on every keystroke in the mandate, which is why it is here rather than on the
 * server: ui-contract.md §3.4 budgets the footer under 100 ms and a round trip cannot
 * make that. It is a pure function with no DOM, no Alpine and no import but format.js,
 * because issue #12 has to call it in bare node to check it against issue #6's Python.
 *
 * Nothing here is a constant that belongs in the assumption set. The risk-appetite caps
 * in particular come off the payload: epic §5 requires every band to be an auditable
 * configuration change rather than an edit to this file.
 *
 * Classic script in the browser, CommonJS under node. See docs/decisions.md A-15.
 */
(function (root) {
  'use strict';

  var fmt = (typeof require === 'function')
    ? require('./format.js')
    : root.TerraFolio && root.TerraFolio.format;
  if (!fmt) throw new Error('feasibility.js requires format.js');

  /**
   * Collapse the `UK` alias onto the ISO `GB` the rest of the system uses.
   *
   * pipeline-schema.md §4.1 keeps `UK` as an accepted alias and the assumption set's
   * market tables key on `GB`, so `normalise_country_code` is applied at both ends in
   * Python: to a file's declared country on load, and to the mandate's country chips
   * before they are compared. This side compared raw strings, and mandate.html's chip
   * group emits `UK` — so a British project was screened out of every mandate that
   * asked for the United Kingdom, while the server admitted it. #12's parity harness
   * found it; see docs/decisions.md 4B-6.
   */
  function normaliseCountry(code) {
    return code === 'UK' ? 'GB' : code;
  }

  /* ── The wire adapter ───────────────────────────────────────────────────────
     The one place GET /pipeline field names appear (api.md §2). Money is €m with an
     explicit _m suffix and the wire never carries a percentage, per api.md §1. */
  function project(raw) {
    return {
      id: raw.id,
      countryCode: normaliseCountry(raw.countryCode),
      stage: raw.stage,
      technology: raw.technology,
      capacityMw: raw.capacityMw,
      codYear: raw.codYear,
      minDscr: raw.minDscr,
      developmentRiskScore: raw.developmentRiskScore,
      gridSecured: raw.gridSecured,
      omContracted: raw.omContracted,
      currency: raw.currency,
      totalCapex_m: raw.totalCapex_m,
      equity_m: raw.equity_m,
      seniorDebt_m: raw.seniorDebt_m,
    };
  }

  /** Projects in canonical order: by id ascending, everywhere (epic §5). */
  function projects(payload) {
    return ((payload && payload.projects) || [])
      .map(project)
      .sort(function (a, b) { return a.id < b.id ? -1 : a.id > b.id ? 1 : 0; });
  }

  /**
   * The per-project development-risk ceiling for a risk appetite (2.6 / 3.6 / 5.0).
   *
   * ui-contract.md §3.3 notes the appetite sets two caps; this is the pre-screen. The
   * portfolio-average penalty at 2.4 / 3.2 / 4.2 is the objective's, not a screen.
   *
   * Read from the payload's resolved assumption set. An appetite it does not define is
   * a configuration error, not a silent pass: failing open would admit the whole pipeline.
   */
  function riskCap(payload, appetite) {
    var caps = (payload && payload.assumptions && payload.assumptions.riskCaps) || {};
    var cap = caps[appetite];
    if (typeof cap !== 'number') {
      throw new Error('no risk cap configured for appetite "' + appetite + '"');
    }
    return cap;
  }

  /* ── The nine screens ───────────────────────────────────────────────────────
     Each is exported on its own so each can be tested on its own. Every one takes
     (project, mandate) and returns a boolean; none looks at any other. Mandate field
     names are api.md §6.1. */

  var screens = {
    /**
     * 1. The project's country is on the mandate's eligible list.
     *
     * Both ends go through `normaliseCountry`, so the screen is right however it is
     * called — the wire adapter has already normalised a project it built, and
     * normalising twice is the same as normalising once.
     *
     * `some` rather than `map(...).indexOf(...)`: this runs once per project per
     * recompute, and building a throwaway array of normalised codes each time put
     * hundreds of allocations inside the path §12 budgets at under 100 ms.
     */
    country: function (p, m) {
      var wanted = normaliseCountry(p.countryCode);
      return (m.countries || []).some(function (code) {
        return normaliseCountry(code) === wanted;
      });
    },

    /** 2. The project's development stage is in scope. */
    stage: function (p, m) {
      return (m.stages || []).indexOf(p.stage) !== -1;
    },

    /** 3. Commercial operation falls inside the mandate's COD window, inclusive. */
    codWindow: function (p, m) {
      return p.codYear >= m.codFrom && p.codYear <= m.codTo;
    },

    /**
     * 4. The project's own minimum DSCR clears the mandate floor.
     *
     * `minDscr` is null where the project carries no debt (api.md §2). An unlevered
     * project has no debt service to fail to cover, so it passes: screening it out
     * would reject the safest assets in the pipeline for having no risk to measure.
     * See docs/decisions.md A-21.
     */
    minDscr: function (p, m) {
      return p.minDscr === null || p.minDscr === undefined || p.minDscr >= m.minDscr;
    },

    /** 5. Development risk is at or under the appetite's ceiling. */
    riskCap: function (p, m, cap) {
      return p.developmentRiskScore <= cap;
    },

    /** 6. Grid connection is secured, when the mandate demands it. */
    gridSecured: function (p, m) {
      return !m.gridSecuredOnly || p.gridSecured === true;
    },

    /** 7. An O&M partner is contracted, when the mandate demands it. */
    omContracted: function (p, m) {
      return !m.omContractedOnly || p.omContracted === true;
    },

    /**
     * 8. Revenue is euro-denominated, when the mandate demands it.
     * Statements are always euros; `currency` is the revenue currency, and a non-EUR
     * project is only ever screened out, never converted here (decisions A-12).
     */
    currency: function (p, m) {
      return !m.eurRevenueOnly || p.currency === 'EUR';
    },

    /** 9. The user has not excluded the project by hand. */
    notExcluded: function (p, m, cap, excludedIds) {
      return (excludedIds || []).indexOf(p.id) === -1;
    },
  };

  var SCREEN_ORDER = [
    'country', 'stage', 'codWindow', 'minDscr', 'riskCap',
    'gridSecured', 'omContracted', 'currency', 'notExcluded',
  ];

  /** True when a project passes all nine. */
  function passes(p, m, cap, excludedIds) {
    for (var i = 0; i < SCREEN_ORDER.length; i++) {
      if (!screens[SCREEN_ORDER[i]](p, m, cap, excludedIds)) return false;
    }
    return true;
  }

  /** Which screens a project fails, in order. Drives "why was this dropped". */
  function failedScreens(p, m, cap, excludedIds) {
    return SCREEN_ORDER.filter(function (name) {
      return !screens[name](p, m, cap, excludedIds);
    });
  }

  /* ── The eligible pool, in aggregate ────────────────────────────────────────
     Field names are the POST /mandate/preview response (api.md §5). Solar share is
     capacity-weighted and gearing is cost-weighted, matching the portfolio aggregates
     the optimiser reports, so the mandate and the result cannot disagree about what
     "62% solar" means. Both are undefined rather than zero when the pool is empty:
     there is no mix without a pool. */
  function aggregate(pool) {
    var mw = 0, solarMw = 0, capex = 0, debt = 0, equity = 0;
    pool.forEach(function (p) {
      mw += p.capacityMw;
      if (p.technology === 'solar') solarMw += p.capacityMw;
      capex += p.totalCapex_m;
      debt += p.seniorDebt_m;
      equity += p.equity_m;
    });
    return {
      eligibleCount: pool.length,
      eligibleCapacityMw: mw,
      eligibleEquity_m: equity,
      eligibleCapex_m: capex,
      eligibleSeniorDebt_m: debt,
      eligibleSolarShare: mw > 0 ? solarMw / mw : NaN,
      eligibleGearing: capex > 0 ? debt / capex : NaN,
    };
  }

  /* ── The warnings ───────────────────────────────────────────────────────────
     Codes and severities from api.md §5; strings and marks from ui-contract.md §3.5
     and §3.6. The order is §5.4's order of severity, which is NOT the order the design
     mockup emits them in (decisions A-5).

     `severity` is `alert` or `info` and nothing else. It used to carry a third value,
     `blocking`, which api.md §5 had specified — but 1A's `WarningSeverity` admits two,
     and `FeasibilityWarning` validates that a warning's severity is the one its *code*
     carries, so the server could not emit `blocking` and 2B could not store it. #9
     corrected the document; this follows it (decisions 3A-5).

     Whether a warning stops the run is a property of the code, not of its severity —
     `BLOCKING` below, mirroring `WarningCode.disables_run` — and it reaches a caller as
     `runnable`. Severity says how loudly to render a warning; `runnable` says whether the
     button works. `tone` is the visual treatment. Every warning carries a mark as well, so
     none is conveyed by colour alone (epic §5, decisions A-10). */

  /** The two codes that disable the run — exactly the two POST /optimisations 422s. */
  var BLOCKING = ['NO_CANDIDATES', 'LOCKS_EXCEED_CAPITAL'];

  /*
   * `heldIds` is the locks net of exclusions — the ids `lockedEquity_m` is the equity
   * of. It replaced a `total` parameter that was accepted and never read.
   */

  function warnings(pool, agg, mandate, heldIds, lockedIds, excludedIds, lockedEquity_m) {
    var out = [];
    var empty = pool.length === 0;

    if (empty) {
      out.push({
        code: 'NO_CANDIDATES', severity: 'alert', tone: 'alert', mark: '×',
        message: 'No candidates pass the current screens. '
               + 'Widen countries, stages or the COD window.',
      });
    }

    if (lockedEquity_m > mandate.availableCapital_m) {
      out.push({
        code: 'LOCKS_EXCEED_CAPITAL', severity: 'alert', tone: 'alert', mark: '×',
        message: 'Locked projects need ' + fmt.eurM(lockedEquity_m) + ' of equity against '
               + fmt.eurM(mandate.availableCapital_m) + ' available. Release a lock to run.',
        detail: {
          lockedEquity_m: lockedEquity_m,
          availableCapital_m: mandate.availableCapital_m,
          excess_m: lockedEquity_m - mandate.availableCapital_m,
          /* The locks this figure is the equity of — so an excluded id is absent,
             because it commits nothing. §3.6 tells the user which lock to release,
             and naming one that contributes nothing to the excess is worse than
             naming none. */
          lockedIds: heldIds.slice(),
        },
      });
    }

    if (!empty && agg.eligibleCapacityMw < mandate.capacityTargetMw) {
      out.push({
        code: 'CAPACITY_BELOW_TARGET', severity: 'alert', tone: 'alert', mark: '!',
        message: 'Eligible pipeline is ' + fmt.mw(agg.eligibleCapacityMw)
               + ' — below the ' + fmt.mw(mandate.capacityTargetMw) + ' target.',
      });
    }

    if (!empty && fmt.defined(agg.eligibleGearing) && agg.eligibleGearing < mandate.minLeverage) {
      out.push({
        code: 'LEVERAGE_UNREACHABLE', severity: 'alert', tone: 'alert', mark: '!',
        message: 'Minimum leverage of ' + fmt.percent(mandate.minLeverage)
               + ' exceeds what the eligible pool supports ('
               + fmt.percent(agg.eligibleGearing) + ').',
      });
    }

    /* One-sided on purpose. A pool with far more solar than the target can still reach
       it by selecting fewer solar projects; a pool with far less cannot reach it at all,
       which is what "may be unreachable" says. See docs/decisions.md A-22. */
    if (!empty && fmt.defined(agg.eligibleSolarShare)
        && agg.eligibleSolarShare < mandate.solarShare - 0.2) {
      out.push({
        code: 'SOLAR_MIX_UNREACHABLE', severity: 'info', tone: 'neutral', mark: '!',
        message: 'Solar target of ' + fmt.percent(mandate.solarShare)
               + ' may be unreachable: eligible pool is '
               + fmt.percent(agg.eligibleSolarShare) + ' solar.',
      });
    }

    /* A ratio rather than `equity < capital * 0.9`, so that a pool absorbing exactly
       the floor lands on the same side here as it does in `feasibility.py`, which
       divides. The two forms differ only in the last bits, and only exactly on the
       boundary — which is the case #12 ships a parity pair for. */
    if (!empty && agg.eligibleEquity_m / mandate.availableCapital_m < 0.9) {
      out.push({
        code: 'CAPITAL_UNDERUSED', severity: 'info', tone: 'neutral', mark: '!',
        message: 'Full pipeline absorbs only ' + fmt.eurM(agg.eligibleEquity_m)
               + ' of the ' + fmt.eurM(mandate.availableCapital_m) + ' available.',
      });
    }

    var locked = (lockedIds || []).length;
    var excluded = (excludedIds || []).length;
    if (locked > 0 || excluded > 0) {
      out.push({
        code: 'LOCKS_PRESENT', severity: 'info', tone: 'neutral', mark: '•',
        message: locked + ' project(s) locked in; ' + excluded + ' excluded.',
      });
    }

    return out;
  }

  /**
   * The whole §5.4 computation, in the shape POST /mandate/preview answers with.
   *
   * `locks` is the preview request's `{ lockedIds, excludedIds }` (api.md §5). They are
   * not part of the mandate object, because they are a view on a result rather than a
   * statement of intent.
   *
   * `runnable` is false if and only if one of the two blocking *codes* is present —
   * exactly the two conditions POST /optimisations answers with 422, so the button and
   * the API agree by construction. Keyed on the code rather than on a severity value,
   * because severity is `alert` or `info` on both sides (decisions 3A-5).
   */
  function feasibility(payload, mandate, locks) {
    var all = projects(payload);
    var lockedIds = (locks && locks.lockedIds) || [];
    var excludedIds = (locks && locks.excludedIds) || [];
    var cap = riskCap(payload, mandate.riskAppetite);

    /* A lock re-admits. `screens.py` computes `eligible = survives_every_screen |
       locked`, and this side used to drop a locked project that failed a screen — so
       the footer promised a candidate count the run would not deliver, which is the
       one bug this whole file exists to avoid. An exclusion still wins over a lock,
       on both sides (decisions 4B-6). */
    var held = lockedIds.filter(function (id) { return excludedIds.indexOf(id) === -1; });
    var pool = all.filter(function (p) {
      return passes(p, mandate, cap, excludedIds) || held.indexOf(p.id) !== -1;
    });
    var agg = aggregate(pool);

    /* Locked equity is what the locks actually commit, so an excluded id contributes
       nothing: `feasibility.py` takes `set(locked) - set(excluded)` before summing. */
    var lockedEquity_m = all.reduce(function (sum, p) {
      return held.indexOf(p.id) === -1 ? sum : sum + p.equity_m;
    }, 0);

    var found = warnings(pool, agg, mandate, held, lockedIds, excludedIds, lockedEquity_m);
    var blocking = found.some(function (w) { return BLOCKING.indexOf(w.code) !== -1; });

    return {
      eligibleCount: agg.eligibleCount,
      totalCount: all.length,
      eligibleCapacityMw: agg.eligibleCapacityMw,
      eligibleEquity_m: agg.eligibleEquity_m,
      eligibleSolarShare: agg.eligibleSolarShare,
      eligibleGearing: agg.eligibleGearing,
      lockedEquity_m: lockedEquity_m,
      warnings: found,
      runnable: !blocking,
      /* Not on the wire: the pool itself, and the three footer figures already
         formatted, so the page never formats a number of its own (spec §14). */
      pool: pool,
      display: {
        eligibleCount: fmt.count(agg.eligibleCount),
        totalCount: fmt.count(all.length),
        eligibleCapacityMw: fmt.mw(agg.eligibleCapacityMw),
        eligibleEquity_m: fmt.eurM(agg.eligibleEquity_m),
        eligibleSolarShare: fmt.percent(agg.eligibleSolarShare),
        eligibleGearing: fmt.percent(agg.eligibleGearing),
      },
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
    projects: projects,
    riskCap: riskCap,
  };

  root.TerraFolio = root.TerraFolio || {};
  root.TerraFolio.feasibility = api;

  if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
