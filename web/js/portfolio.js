/**
 * portfolio.js — screen 03, over one stored run.
 *
 * Everything here reads `GET /optimisations/{id}` and nothing reads the live
 * pipeline. A run is immutable and addressable (spec §11), so a URL carrying its id
 * reopens the exact result — the same holdings, the same mandate, the same figures —
 * even after the directory beneath it has moved (§13). Merging against the current
 * pipeline would quietly undo that.
 *
 * The two cash-flow series are kept apart by name, everywhere: `cashflow30Y_m` has
 * **no** terminal value and is what the chart, the tile and the CSV show;
 * `cashflowHold_m` has one and drives nothing on this screen but the IRR that
 * arrived already solved. Conflating them is the likeliest silent bug in the whole
 * feature and it produces numbers that look right (api.md §1.5).
 *
 * Classic script in the browser, CommonJS under node. See docs/decisions.md A-15.
 */
(function (root) {
  'use strict';

  var load = (typeof require === 'function')
    ? function (name) { return require('./' + name + '.js'); }
    : function (name) { return root.TerraFolio && root.TerraFolio[name]; };

  var fmt = load('format');
  var api = load('api');
  var charts = load('charts');
  var mapper = load('map');
  var table = load('table');
  var store = load('mandate');
  var status = (typeof require === 'function') ? require('./controls.js').status
    : (root.TerraFolio && root.TerraFolio.status);

  var TECHNOLOGY = { solar: 'Solar', onshore_wind: 'Wind', offshore_wind: 'Offshore wind' };

  /**
   * The seven provenance groups, as a reader should see them.
   *
   * pipeline-schema.md §8 names them in the file's own vocabulary — `debtTerms`, `om`
   * — which is right for a schema and wrong on screen. Any group not named here
   * falls back to its key, so a group added to the schema shows up rather than
   * disappearing.
   */
  var PROVENANCE_GROUPS = {
    // "P50 generation" rather than the bare noun: it is what the group covers —
    // pipeline-schema.md §8 gives it as netCapacityFactor and physicals.generationGwh
    // — and it says which sense of the word is meant, to a reader and to §14's guard.
    generation: 'P50 generation', price: 'Price', capex: 'Capex', opex: 'Opex',
    debtTerms: 'Debt terms', grid: 'Grid connection', om: 'O&M',
  };
  var STAGE = {
    greenfield: 'Greenfield', ready_to_build: 'Ready-to-build', construction: 'Construction',
  };

  /** ui-contract.md §5.1's two tolerances, both on the tile rather than on the engine. */
  var CAPACITY_TOLERANCE = 0.08;
  var SPLIT_TOLERANCE = 0.08;

  /* ── DOM helpers ────────────────────────────────────────────────────────── */

  function field(name) {
    return document.querySelector('[data-field="' + name + '"]');
  }

  function setField(name, text) {
    var node = field(name);
    if (node) node.textContent = text;
  }

  /**
   * A tile's compliance state, set through its own Alpine component so the mark and
   * the word come from the one status vocabulary rather than from here (§7.1).
   */
  function setTile(name, value, sub, state) {
    setField(name, value);
    setField(name + '-sub', sub);
    var tile = document.querySelector('[data-tile="' + name + '"]');
    if (!tile || !root.Alpine || typeof root.Alpine.$data !== 'function') return;
    var data = root.Alpine.$data(tile);
    if (data) data.state = state || 'neutral';
  }

  /** Compliant when the rule holds, breaching when it does not, neutral when it cannot be judged. */
  function state(holds) {
    if (holds === null || holds === undefined) return 'neutral';
    return holds ? 'on-target' : 'breach';
  }

  /* ── The twelve tiles (ui-contract.md §5.1) ─────────────────────────────── */

  function tiles(run) {
    var a = run.aggregates || {};
    var m = run.mandate || {};
    var defined = fmt.defined;

    setTile('capacity', fmt.mw(a.capacityMw), capacitySub(a, m),
      state(defined(a.capacityMw)
        ? Math.abs(a.capacityMw - m.capacityTargetMw) / m.capacityTargetMw <= CAPACITY_TOLERANCE
        : null));

    setTile('projects', fmt.count(a.projectCount),
      fmt.join(fmt.count(a.solarCount) + ' solar', fmt.count(a.windCount) + ' wind'), 'neutral');

    setTile('tech-split', fmt.percent(a.solarShare) + ' solar',
      'target ' + fmt.percent(m.solarShare) + ' solar',
      state(defined(a.solarShare)
        ? Math.abs(a.solarShare - m.solarShare) <= SPLIT_TOLERANCE : null));

    setTile('equity', fmt.eurM(a.equity_m),
      fmt.join('of ' + fmt.eurM(m.availableCapital_m),
        fmt.percent(a.capitalDeployed) + ' deployed'), 'neutral');

    setTile('cost', fmt.eurM(a.totalCapex_m), fmt.eurM(a.seniorDebt_m) + ' senior debt', 'neutral');

    // §5.1: the MOIC clause is dropped rather than shown as 0.00x when there is none.
    var holdLabel = field('irr-hold');
    if (holdLabel) holdLabel.textContent = ' (' + fmt.count(m.holdYears) + 'y)';
    setTile('irr', fmt.irr(a.equityIrr),
      fmt.join('hurdle ' + fmt.irr(m.targetIrr),
        defined(a.moic) ? fmt.moic(a.moic) + ' MOIC' : null),
      state(defined(a.equityIrr) ? a.equityIrr >= m.targetIrr : null));

    setTile('leverage', fmt.percent(a.gearing),
      fmt.join('min ' + fmt.percent(m.minLeverage), 'DSCR floor ' + fmt.dscr(a.worstMinDscr)),
      state(defined(a.gearing) ? a.gearing >= m.minLeverage : null));

    setTile('lcoe', fmt.eur(a.weightedLcoe), 'per MWh, 6% real', 'neutral');

    setTile('generation', fmt.gwh(a.annualGenerationGwh),
      fmt.count(a.co2AvoidedKt) + ' kt CO₂ avoided p.a.', 'neutral');

    setTile('fcfe30', fmt.eurM(a.thirtyYearFcfe_m), 'undiscounted, post debt', 'neutral');

    setTile('merchant', fmt.percent(a.merchantShare), 'cap ' + fmt.percent(m.maxMerchantShare),
      state(defined(a.merchantShare) ? a.merchantShare <= m.maxMerchantShare : null));

    setTile('concentration', fmt.percent(a.largestCountryShare),
      fmt.join('cap ' + fmt.percent(m.maxCountryShare),
        'risk score ' + fmt.score(a.weightedRiskScore)),
      state(defined(a.largestCountryShare)
        ? a.largestCountryShare <= m.maxCountryShare : null));
  }

  /**
   * spec §13: where the capacity target is unreachable the run still returns the best
   * feasible portfolio, and the tile names the shortfall rather than only the target.
   */
  function capacitySub(a, m) {
    var target = 'target ' + fmt.mw(m.capacityTargetMw);
    if (!fmt.defined(a.capacityMw) || a.capacityMw >= m.capacityTargetMw) return target;
    return fmt.join(target, fmt.mw(m.capacityTargetMw - a.capacityMw) + ' short');
  }

  /* ── The run sub-line (ui-contract.md §5) ───────────────────────────────── */

  function subtitle(run) {
    var a = run.aggregates || {};
    var countries = Object.keys(a.countryShares || {}).length;
    return fmt.mw(a.capacityMw) + ' across ' + fmt.count(a.projectCount) + ' projects in '
      + fmt.count(countries) + ' countries ' + fmt.SEPARATOR + ' '
      + fmt.count((run.mandate || {}).holdYears) + '-year hold '
      + fmt.SEPARATOR + ' run ' + run.runRef;
  }

  /* ── The thirty-bar cash-flow chart (ui-contract.md §5.2) ────────────────── */

  function cashflow(run) {
    var series = run.cashflow30Y_m || [];
    var baseYear = baseYearOf(run);
    var shape = charts.bars(series, baseYear);
    var plot = document.querySelector('[data-region="cashflow-bars"]');
    var tickRow = document.querySelector('[data-region="cashflow-ticks"]');
    var body = document.querySelector('[data-region="cashflow-table"] tbody');
    var zero = field('zero-line');

    setField('axis-top', fmt.eurM(shape.max));
    setField('axis-bottom', fmt.eurM(shape.min));
    setField('cashflow-cumulative', fmt.eurM(shape.total));
    if (zero) zero.style.top = shape.zeroPercent + '%';
    if (!plot) return shape;

    plot.textContent = '';
    if (tickRow) tickRow.textContent = '';
    if (body) body.textContent = '';

    var marks = charts.ticks(shape.bars.length, baseYear);
    shape.bars.forEach(function (bar, index) {
      plot.appendChild(barColumn(bar, shape.zeroPercent));
      if (tickRow) tickRow.appendChild(tick(marks[index]));
      if (body) body.appendChild(cashflowRow(bar));
    });
    return shape;
  }

  /**
   * One bar: a real button, so the series is reachable by keyboard and by a reader
   * rather than only by pointer hover (§5.2). Its readout carries the sign in words,
   * because sitting below the line is a position and a lighter tone is a colour —
   * neither survives on its own (§7.1).
   */
  function barColumn(bar, zeroPercent) {
    var column = document.createElement('li');
    column.className = 'relative flex-1';

    var button = document.createElement('button');
    button.type = 'button';
    button.className = bar.negative
      ? 'absolute inset-x-0 block w-full cursor-pointer border border-accent-700 bg-accent-300 p-0'
      : 'absolute inset-x-0 block w-full cursor-pointer border-0 bg-accent-700 p-0 hover:bg-accent-800';
    button.style.height = bar.heightPercent + '%';
    if (bar.negative) button.style.top = zeroPercent + '%';
    else button.style.bottom = (100 - zeroPercent) + '%';

    var readout = document.createElement('span');
    readout.className = 'sr-only';
    readout.textContent = readoutFor(bar);
    button.appendChild(readout);

    var show = function () { setField('cashflow-hover', readoutFor(bar)); };
    button.addEventListener('mouseenter', show);
    button.addEventListener('focus', show);
    column.appendChild(button);
    return column;
  }

  /** `2027: minus €28.9m` — one decimal, and the sign as a word (§5.2, §7.1). */
  function readoutFor(bar) {
    var amount = fmt.eurM(Math.abs(bar.value));
    return fmt.year(bar.year) + ': ' + (bar.negative ? 'minus ' : '') + amount;
  }

  function tick(year) {
    var cell = document.createElement('li');
    cell.className = 'flex-1 text-center text-tick text-muted';
    cell.textContent = year === null ? '' : fmt.year(year);
    return cell;
  }

  function cashflowRow(bar) {
    var row = document.createElement('tr');
    var year = document.createElement('th');
    year.setAttribute('scope', 'row');
    year.textContent = fmt.year(bar.year);
    var amount = document.createElement('td');
    amount.textContent = (bar.negative ? 'minus ' : '') + fmt.eurM(Math.abs(bar.value));
    row.appendChild(year);
    row.appendChild(amount);
    return row;
  }

  /**
   * The base year of the run, taken from its exit year rather than assumed.
   *
   * `holdYears` and the mandate's COD window are both on the run, and the pipeline's
   * base year is the COD floor — 2027 for the shipped corpus. Reading it off the
   * mandate keeps the chart's x-axis tied to the run rather than to a literal.
   */
  function baseYearOf(run) {
    var m = run.mandate || {};
    return typeof m.codFrom === 'number' ? m.codFrom : 2027;
  }

  /* ── The drawer (ui-contract.md §5.5) ───────────────────────────────────── */

  function openDrawer(page, projectId) {
    var row = findHolding(page, projectId);
    if (!row) return null;
    page.openProject = projectId;

    setField('drawer-kicker', fmt.join(TECHNOLOGY[row.technology], STAGE[row.stage]));
    setField('drawer-name', row.name);
    setField('drawer-location', fmt.join(row.country,
      fmt.degrees(row.lat) + ', ' + fmt.degrees(row.lon), row.id));

    headline(page, row);
    groups(page, row);
    steeringButtons(page, row);

    if (api && !api.offline()) {
      api.getProjectStatements(projectId).then(function (statements) {
        if (statements && page.openProject === projectId) provenance(statements);
      }).catch(function () { /* the scalar groups above stand on their own */ });
    }
    return row;
  }

  function findHolding(page, projectId) {
    var holdings = (page.run && page.run.holdings) || [];
    for (var i = 0; i < holdings.length; i++) {
      if (holdings[i].id === projectId) return holdings[i];
    }
    return null;
  }

  function headline(page, row) {
    var hold = ((page.run || {}).mandate || {}).holdYears;
    render('drawer-headline', [
      ['Capacity', fmt.mw(row.capacityMw)],
      ['Equity IRR (' + fmt.count(hold) + 'y)', fmt.irr(row.equityIrr)],
      ['Equity', fmt.eurM(row.equity_m)],
      ['MOIC', fmt.moic(row.moic)],
    ], 'headline');
  }

  function groups(page, row) {
    var run = page.run || {};
    var lastYear = baseYearOf(run) + (run.mandate || {}).holdYears - 1;
    var basis = (row.provenance || {});

    var sections = [
      ['Technical', [
        ['Net capacity factor', fmt.percent1(row.netCapacityFactor), basis.generation],
        ['P50 generation', fmt.gwh(row.annualGenerationGwh) + '/yr', basis.generation],
        ['Commercial operation', fmt.year(row.codYear), null],
        ['Grid connection', row.gridSecured ? 'Secured' : 'Application pending', basis.grid],
        ['O&M', row.omContracted ? 'Long-term service agreement signed' : 'Not contracted', basis.om],
      ]],
      ['Capital structure', [
        ['Total project cost', fmt.eurM(row.totalCapex_m) + ' (' + fmt.eur(row.capexPerKw) + '/kW)', basis.capex],
        ['Senior debt', fmt.eurM(row.seniorDebt_m) + ' at ' + fmt.percent(row.gearing) + ', '
          + fmt.percent1(row.debtRate) + ', ' + fmt.count(row.debtTenorYears) + 'y', basis.debtTerms],
        ['Minimum DSCR', fmt.dscr(row.minDscr), basis.debtTerms],
        ['Equity payback', row.paybackYear === null || row.paybackYear === undefined
          ? 'beyond ' + fmt.year(baseYearOf(run) + 29) : fmt.year(row.paybackYear), null],
        ['Currency', row.currency + (row.currency === 'EUR' ? '' : ' — hedge required'), null],
      ]],
      ['Revenue', [
        ['Contracted share', fmt.percent(row.ppaShare), basis.price],
        ['PPA price', row.ppaTenorYears > 0
          ? fmt.eur(row.ppaPrice) + '/MWh for ' + fmt.count(row.ppaTenorYears) + ' yrs'
          : 'merchant only', basis.price],
        ['Capture price', fmt.eur(row.capturePrice) + '/MWh (baseload '
          + fmt.eur(row.countryBaseloadPrice) + ')', basis.price],
        ['LCOE', fmt.eur(row.lcoe) + '/MWh', null],
        ['Opex', fmt.eur(row.opexPerKwYear) + '/kW/yr', basis.opex],
      ]],
      ['Risk', [
        ['Development risk score', fmt.score(row.developmentRiskScore) + ' / 5', null],
        ['Status in portfolio', statusIn(page, row), null],
        ['Locked', page.locks.lockedIds.indexOf(row.id) === -1
          ? 'No' : 'Yes — forced into next run', null],
      ]],
    ];

    var host = document.querySelector('[data-region="drawer-groups"]');
    if (!host) return;
    host.textContent = '';
    sections.forEach(function (section) {
      host.appendChild(heading(section[0]));
      host.appendChild(definitions(section[1]));
    });

    // spec §13: a project completing after the hold ends contributes construction
    // outflows and an exit value only, and the sheet has to say so.
    if (row.codYear > lastYear) {
      var note = document.createElement('p');
      note.className = 'mt-3 text-meta text-muted';
      note.textContent = 'Commercial operation in ' + fmt.year(row.codYear)
        + ' falls after the ' + fmt.count((run.mandate || {}).holdYears)
        + '-year hold ends in ' + fmt.year(lastYear)
        + '. Over the hold this project contributes construction outflows and an exit value only.';
      host.appendChild(note);
    }
  }

  function statusIn(page, row) {
    if (page.locks.excludedIds.indexOf(row.id) !== -1) return 'Excluded';
    return row.selected ? 'Selected' : 'Not selected';
  }

  function heading(text) {
    var h = document.createElement('h3');
    h.className = 'mb-[6px] mt-[18px] text-h6 uppercase tracking-caps text-accent-700';
    h.textContent = text;
    return h;
  }

  /**
   * A group of rows. Where a figure's provenance is softer than a signed contract the
   * row says which — that is what tells a committee how much of an early-stage file
   * is prediction rather than contract (pipeline-schema.md §8).
   */
  function definitions(rows) {
    var list = document.createElement('dl');
    list.className = 'm-0 grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-[6px]';
    rows.forEach(function (entry) {
      var term = document.createElement('dt');
      term.className = 'text-cell text-muted';
      term.textContent = entry[0];
      var value = document.createElement('dd');
      value.className = 'm-0 text-right text-cell';
      value.textContent = entry[1];
      var note = basisNote(entry[2]);
      if (note) value.appendChild(note);
      list.appendChild(term);
      list.appendChild(value);
    });
    return list;
  }

  var CONTRACTED = 'contracted';

  function basisNote(entry) {
    if (!entry || !entry.estimateBasis || entry.estimateBasis === CONTRACTED) return null;
    var note = document.createElement('span');
    note.className = 'block text-meta text-muted';
    note.textContent = entry.estimateBasis.split('_').join(' ');
    return note;
  }

  /**
   * The full provenance block, which only arrives with the statements: `GET /pipeline`
   * and the run's holdings carry the abbreviated form without the analyst's notes,
   * and the notes are the larger half of it (api.md §2, §4).
   */
  function provenance(statements) {
    var block = statements.provenance || {};
    var host = document.querySelector('[data-region="drawer-groups"]');
    if (!host) return;
    var existing = host.querySelector('[data-region="drawer-provenance"]');
    if (existing) existing.parentNode.removeChild(existing);

    var section = document.createElement('div');
    section.setAttribute('data-region', 'drawer-provenance');
    section.appendChild(heading('Provenance'));
    section.appendChild(definitions([
      ['Prepared by', block.preparedBy || fmt.DASH, null],
      ['Last updated', block.preparedOn || fmt.DASH, null],
      ['Model version', block.modelVersion || fmt.DASH, null],
    ]));

    var fields = block.fields || {};
    var rows = Object.keys(fields).map(function (group) {
      var entry = fields[group] || {};
      return [PROVENANCE_GROUPS[group] || group,
        fmt.join((entry.estimateBasis || '').split('_').join(' '), entry.confidence), null];
    });
    if (rows.length) section.appendChild(definitions(rows));
    host.appendChild(section);
  }

  function render(region, rows, kind) {
    var host = document.querySelector('[data-region="' + region + '"]');
    if (!host) return;
    host.textContent = '';
    rows.forEach(function (entry) {
      var cell = document.createElement('div');
      cell.className = kind === 'headline'
        ? 'border-b border-r border-divider px-[14px] py-[10px]' : '';
      var term = document.createElement('dt');
      term.className = 'text-micro uppercase tracking-micro text-muted';
      term.textContent = entry[0];
      var value = document.createElement('dd');
      value.className = 'num m-0 text-num-md';
      value.textContent = entry[1];
      cell.appendChild(term);
      cell.appendChild(value);
      host.appendChild(cell);
    });
  }

  /* ── Steering (§7.6) ────────────────────────────────────────────────────── */

  function steeringButtons(page, row) {
    var lock = document.querySelector('[data-action="drawer-lock"]');
    var exclude = document.querySelector('[data-action="drawer-exclude"]');
    var locked = page.locks.lockedIds.indexOf(row.id) !== -1;
    var excluded = page.locks.excludedIds.indexOf(row.id) !== -1;
    if (lock) {
      lock.setAttribute('aria-pressed', locked ? 'true' : 'false');
      lock.textContent = locked ? 'Unlock' : 'Lock into portfolio';
    }
    if (exclude) {
      exclude.setAttribute('aria-pressed', excluded ? 'true' : 'false');
      exclude.textContent = excluded ? 'Re-admit candidate' : 'Exclude from search';
    }
  }

  /**
   * Whether the mandate or the steering has moved since this run was submitted, which
   * is what §7.6 relabels the primary action on. A signature rather than a flag, so
   * nothing has to remember to set it.
   */
  function changed(page) {
    if (!store) return false;
    var mandate = store.savedMandate() || (page.run || {}).mandate;
    var recorded = page.locks.signature;
    if (!recorded || !mandate) return false;
    return store.signature(mandate, page.locks) !== recorded;
  }

  function rerunLabel(page) {
    var button = document.querySelector('[data-action="rerun"]');
    if (button) button.textContent = changed(page) ? 'Re-run with changes' : 'Re-run';
  }

  function steer(page, projectId, change) {
    if (!store) return null;
    page.locks = store.steer(projectId, change);
    applyLocks(page);
    var row = findHolding(page, projectId);
    if (row) steeringButtons(page, row);
    rerunLabel(page);
    return page.locks;
  }

  /**
   * The lock column reads the row, so the row's own flag has to move with the store.
   *
   * A **new object** per changed row rather than a mutation. Alpine's keyed `x-for`
   * reuses the element it already has for a key and only re-evaluates that row's
   * bindings when the item itself changes; mutating the object in place left the
   * store, the backing array and the rendered row disagreeing, with the row the only
   * one a user can see.
   */
  function applyLocks(page) {
    var holdings = (page.run && page.run.holdings) || [];
    page.run.holdings = holdings.map(function (row) {
      var locked = page.locks.lockedIds.indexOf(row.id) !== -1;
      if (row.locked === locked) return row;
      var copy = {};
      Object.keys(row).forEach(function (key) { copy[key] = row[key]; });
      copy.locked = locked;
      return copy;
    });
    table.holdings(page.view, page.run.holdings);
    refresh(page);
  }

  /* ── The table ──────────────────────────────────────────────────────────── */

  function refresh(page) {
    if (!page.view) return;
    var rows = table.resolve(page.view);
    table.count(page.view);
    if (page.table) page.table.rows = rows;
  }

  /* ── Exports (api.md §9) ────────────────────────────────────────────────── */

  function exports(page) {
    var run = page.run || {};
    setField('export-subtitle', subtitle(run));
    setField('export-rows', fmt.count((run.selectedIds || []).length) + ' rows');
    if (!api) return;
    var urls = api.exportUrls(run.runId);
    var wanted = [
      ['export-holdings', urls.holdings],
      ['export-cashflow', urls.cashflow],
      ['export-pack', urls.pack],
    ];
    wanted.forEach(function (pair) {
      var button = document.querySelector('[data-action="' + pair[0] + '"]');
      if (!button) return;
      button.addEventListener('click', function () { download(pair[1]); });
    });
  }

  /**
   * Follow the URL rather than fetch it: all three answer with Content-Disposition,
   * and letting the browser handle that is what puts a named file on disk instead of
   * a blob this page would have to name itself.
   */
  function download(url) {
    if (!api || api.offline()) return;
    var link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', '');
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  /* ── Wiring ─────────────────────────────────────────────────────────────── */

  function runIdFromUrl() {
    var search = root.location && root.location.search;
    if (!search) return null;
    var match = search.replace('?', '').split('&').filter(function (pair) {
      return pair.indexOf('run=') === 0;
    })[0];
    return match ? decodeURIComponent(match.slice(4)) : null;
  }

  /**
   * Wire the screen, once.
   *
   * Idempotent because `boot` calls it and a caller — a test, a console — may call
   * it again: `bind` attaches a document-level listener per call, so a second page
   * would render over the first with its own view and the two would fight over
   * `rows`. Returning the page already in place is both the safe answer and the
   * useful one.
   */
  var current = null;

  function start() {
    if (current) return current;
    var host = document.querySelector('[data-region="holdings"]');
    if (!host) return null;

    var page = {
      run: null,
      view: table.view(),
      table: null,
      locks: store ? store.steering() : { lockedIds: [], excludedIds: [], signature: null },
      openProject: null,
    };

    var element = host.closest('table');
    if (element && root.Alpine && typeof root.Alpine.$data === 'function') {
      page.table = root.Alpine.$data(element);
    }

    current = page;
    table.bind(page.view, function () { refresh(page); });
    listen(page);

    var runId = runIdFromUrl() || page.locks.runId;
    if (runId && api && !api.offline()) {
      api.getResult(runId).then(function (result) {
        if (result && result.status === 'succeeded') show(page, result);
      }).catch(function () { /* the page keeps its em dashes */ });
    }
    return page;
  }

  function listen(page) {
    document.addEventListener('click', function (event) {
      var opener = event.target.closest && event.target.closest('[data-action="open-drawer"]');
      if (opener) {
        var row = opener.closest('[data-project-id]');
        if (row) openDrawer(page, row.getAttribute('data-project-id'));
        return;
      }
      var lock = event.target.closest && event.target.closest('[data-action="drawer-lock"]');
      if (lock && page.openProject) {
        steer(page, page.openProject,
          { locked: lock.getAttribute('aria-pressed') !== 'true' });
        return;
      }
      var exclude = event.target.closest && event.target.closest('[data-action="drawer-exclude"]');
      if (exclude && page.openProject) {
        steer(page, page.openProject,
          { excluded: exclude.getAttribute('aria-pressed') !== 'true' });
        return;
      }
      var rerun = event.target.closest && event.target.closest('[data-action="rerun"]');
      if (rerun) submit(page);
    });
  }

  /** Render one stored run. Everything on the screen comes from this object. */
  function show(page, run) {
    page.run = run;
    setField('result-subtitle', subtitle(run));
    tiles(run);
    cashflow(run);

    page.locks = store ? store.steering() : page.locks;
    adoptSteering(page, run);
    // The floor before the rows, so no row is ever rendered against a null one and
    // then corrected: with no floor known a cell cannot claim a breach (3B-7).
    if (page.table) page.table.dscrFloor = (run.mandate || {}).minDscr;
    applyLocks(page);
    table.countryOptions(page.view);

    var panel = document.querySelector('[data-region="map"]');
    if (panel) mapper.render(panel, (run.holdings || []).filter(function (row) { return row.selected; }));

    exports(page);
    rerunLabel(page);
    return page;
  }

  /**
   * Adopt the run's own steering where this session has none.
   *
   * A run records which projects were locked into it and which were excluded from it
   * (api.md §8.2, §8). Opening a shared result in a fresh session should therefore
   * carry on from where that run left off rather than showing a lock column that
   * contradicts the run it is describing — and §7.6's accumulation then works from
   * the right starting point. A session that already has its own steering keeps it.
   */
  function adoptSteering(page, run) {
    var locked = (run.holdings || []).filter(function (row) { return row.locked; })
      .map(function (row) { return row.id; });
    var excluded = run.excludedIds || [];
    if (!store) return;
    if (page.locks.lockedIds.length || page.locks.excludedIds.length) return;
    if (!locked.length && !excluded.length) return;
    page.locks = store.saveSteering({
      lockedIds: locked.slice().sort(),
      excludedIds: excluded.slice().sort(),
      runId: page.locks.runId || run.runId,
      runRef: page.locks.runRef || run.runRef,
      signature: page.locks.signature,
    });
  }

  /**
   * §7.6's loop: the same mandate, the accumulated locks and exclusions, a new run.
   * The mandate is the user's current one where they have edited it, so that the
   * relabelled action does what its label says.
   */
  function submit(page) {
    if (!api || api.offline() || !page.run) return null;
    var mandate = (store && store.savedMandate()) || page.run.mandate;
    return api.getPipelineStatus().then(function (health) {
      return api.postOptimisation({
        mandate: mandate,
        lockedIds: page.locks.lockedIds,
        excludedIds: page.locks.excludedIds,
        effort: page.run.effort || 'standard',
        seed: null,
        pipelineHash: health ? health.pipelineHash : null,
      });
    }).then(function (accepted) {
      if (!accepted || !store) return null;
      var next = store.steering();
      next.runId = accepted.runId;
      next.runRef = accepted.runRef;
      next.totalRounds = accepted.totalGenerations;
      next.signature = store.signature(mandate, next);
      store.saveSteering(next);
      root.location.assign('search.html?run=' + encodeURIComponent(accepted.runId));
      return accepted;
    }).catch(function () { return null; });
  }

  function boot() {
    if (typeof document === 'undefined') return;
    if (root.Alpine) start();
    else document.addEventListener('alpine:initialized', function () { start(); });
  }

  var portfolio = {
    TECHNOLOGY: TECHNOLOGY,
    PROVENANCE_GROUPS: PROVENANCE_GROUPS,
    STAGE: STAGE,
    CAPACITY_TOLERANCE: CAPACITY_TOLERANCE,
    SPLIT_TOLERANCE: SPLIT_TOLERANCE,
    start: start,
    reset: function () { current = null; },
    show: show,
    tiles: tiles,
    subtitle: subtitle,
    cashflow: cashflow,
    openDrawer: openDrawer,
    provenance: provenance,
    steer: steer,
    applyLocks: applyLocks,
    adoptSteering: adoptSteering,
    changed: changed,
    rerunLabel: rerunLabel,
    refresh: refresh,
    readoutFor: readoutFor,
    capacitySub: capacitySub,
    runIdFromUrl: runIdFromUrl,
  };

  root.TerraFolio = root.TerraFolio || {};
  root.TerraFolio.portfolio = portfolio;

  if (typeof module === 'object' && module.exports) module.exports = portfolio;
  boot();
})(typeof globalThis !== 'undefined' ? globalThis : this);
