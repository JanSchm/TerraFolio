/**
 * table.js — the §7.4 holdings table: sixteen columns, sortable on fifteen, with a
 * free-text search, three filters and the selected-versus-all-candidates toggle.
 *
 * Two things shape it.
 *
 * **It reads the run's own `holdings` array, never the live `GET /pipeline`.** That
 * array carries every eligible candidate the run saw with a `selected` flag, which
 * is what lets the toggle show what the optimiser rejected — and what lets a run
 * reopened after the pipeline has moved still show the portfolio it actually chose
 * (api.md §8.2, spec §13).
 *
 * **It sorts and filters a plain backing array and assigns `rows` once.** spec §12
 * budgets both under 50 ms at 500 rows. The rendering stays #10's `x-for` template,
 * which is where the four non-colour status signals live — the lock mark, `Locked`
 * and `Not selected` in the row's accessible name, and the Min DSCR breach. Rendering
 * rows some other way would leave every one of those assertions with nothing to
 * inspect (decisions 3B-7).
 *
 * Classic script in the browser, CommonJS under node. See docs/decisions.md A-15.
 */
(function (root) {
  'use strict';

  var load = (typeof require === 'function')
    ? function (name) { return require('./' + name + '.js'); }
    : function (name) { return root.TerraFolio && root.TerraFolio[name]; };

  var fmt = load('format');

  /** Long enough to swallow a burst of typing, short enough to feel immediate. */
  var SEARCH_DEBOUNCE_MS = 120;

  /* ── Ordering ───────────────────────────────────────────────────────────── */

  /** Canonical order, and the tie-break under every sort (epic §5). */
  function byId(a, b) {
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  }

  function missing(value) {
    return value === null || value === undefined || (typeof value === 'number' && isNaN(value));
  }

  /**
   * One comparison.
   *
   * A null `equityIrr` or `minDscr` sorts **last in both directions** (§5.4): an em
   * dash is absent, not small, and a project with no debt is not the worst-covered
   * one in the portfolio. Equal values fall back to the id, so the order is total
   * and a re-sort of the same data cannot shuffle.
   */
  function compare(a, b, field, direction) {
    var left = a[field];
    var right = b[field];
    if (missing(left) || missing(right)) {
      if (missing(left) && missing(right)) return byId(a, b);
      return missing(left) ? 1 : -1;
    }
    var order;
    if (typeof left === 'number' && typeof right === 'number') {
      order = left < right ? -1 : left > right ? 1 : 0;
    } else {
      var l = String(left);
      var r = String(right);
      order = l < r ? -1 : l > r ? 1 : 0;
    }
    if (order === 0) return byId(a, b);
    return direction === 'descending' ? -order : order;
  }

  /* ── The view ───────────────────────────────────────────────────────────── */

  /**
   * Everything the table shows, over one run's holdings.
   *
   * `entries` pairs each row with the lower-cased haystack the free-text search
   * reads, built once when the holdings arrive rather than per keystroke: three
   * `toLowerCase` calls per row per keystroke is the kind of cost that only shows up
   * at the 500 rows §12 names.
   */
  function view(options) {
    var o = options || {};
    return {
      entries: [],
      rows: [],
      selectedOnly: true,
      query: '',
      country: '',
      technology: '',
      stage: '',
      field: o.field || 'equityIrr',
      direction: o.direction || 'descending',
      shown: 0,
      total: 0,
    };
  }

  function holdings(state, list) {
    state.entries = (list || []).map(function (row) {
      return {
        row: row,
        haystack: [row.name, row.id, row.country].join(' ').toLowerCase(),
      };
    });
    return state;
  }

  function keeps(entry, state) {
    var row = entry.row;
    if (state.selectedOnly && row.selected === false) return false;
    if (state.country && row.countryCode !== state.country) return false;
    if (state.technology && row.technology !== state.technology) return false;
    if (state.stage && row.stage !== state.stage) return false;
    if (state.query && entry.haystack.indexOf(state.query) === -1) return false;
    return true;
  }

  /**
   * Filter, then sort, then count. Returns the rows to hand the table in one array,
   * so the render happens once rather than once per predicate.
   */
  function resolve(state) {
    var kept = [];
    var base = 0;
    for (var i = 0; i < state.entries.length; i++) {
      var entry = state.entries[i];
      if (state.selectedOnly && entry.row.selected === false) continue;
      base += 1;
      if (keeps(entry, state)) kept.push(entry.row);
    }
    var field = state.field;
    var direction = state.direction;
    kept.sort(function (a, b) { return compare(a, b, field, direction); });
    state.rows = kept;
    state.shown = kept.length;
    state.total = base;
    return kept;
  }

  /* ── The DOM ────────────────────────────────────────────────────────────── */

  /**
   * Bind the view to the page: the three selects, the search box, the toggle chip
   * and the table's own sort events.
   *
   * `apply` is passed in rather than called here, because assigning `rows` is the
   * table component's business and portfolio.js owns the component handle.
   */
  function bind(state, apply) {
    var search = document.querySelector('[data-filter="query"]');
    var timer = null;
    if (search) {
      search.addEventListener('input', function () {
        var value = search.value.trim().toLowerCase();
        if (timer) root.clearTimeout(timer);
        timer = root.setTimeout(function () {
          timer = null;
          state.query = value;
          apply();
        }, SEARCH_DEBOUNCE_MS);
      });
    }

    ['country', 'technology', 'stage'].forEach(function (name) {
      var select = document.querySelector('[data-filter="' + name + '"]');
      if (!select) return;
      select.addEventListener('change', function () {
        state[name] = select.value;
        apply();
      });
    });

    var toggle = document.querySelector('[data-action="show-all"]');
    if (toggle) {
      toggle.addEventListener('click', function () {
        state.selectedOnly = !state.selectedOnly;
        showAllChip(toggle, !state.selectedOnly);
        apply();
      });
    }

    document.addEventListener('tf:change', function (event) {
      var detail = event.detail;
      if (!detail || detail.name !== 'holdingsSort' || !detail.value) return;
      state.field = detail.value.field;
      state.direction = detail.value.direction;
      apply();
    });
    return state;
  }

  /**
   * The toggle's own two signals: `aria-pressed` for a reader, and the filled or
   * hollow square for a sighted one, because the fill alone is a colour (§7.1).
   */
  function showAllChip(chip, showingAll) {
    chip.setAttribute('aria-pressed', showingAll ? 'true' : 'false');
    var mark = chip.querySelector('[aria-hidden="true"]');
    var label = chip.querySelector('span:not([aria-hidden])');
    if (mark) mark.textContent = showingAll ? '■' : '□';
    if (label) label.textContent = showingAll ? 'Showing all candidates' : 'Show all candidates';
  }

  /** `12 of 177` beside the heading — how much of this view the filters let through. */
  function count(state) {
    var node = document.querySelector('[data-field="table-count"]');
    if (node) node.textContent = fmt.count(state.shown) + ' of ' + fmt.count(state.total);
  }

  /**
   * The country filter's options, from the run's own candidates rather than from a
   * fixed list of markets: a run that saw no Finnish project should not offer to
   * filter for one.
   */
  function countryOptions(state) {
    var select = document.querySelector('[data-filter="country"]');
    if (!select) return;
    var seen = {};
    state.entries.forEach(function (entry) {
      if (entry.row.countryCode) seen[entry.row.countryCode] = entry.row.country || entry.row.countryCode;
    });
    var codes = Object.keys(seen).sort(function (a, b) {
      return seen[a] < seen[b] ? -1 : seen[a] > seen[b] ? 1 : 0;
    });
    while (select.options.length > 1) select.remove(1);
    codes.forEach(function (code) {
      var option = document.createElement('option');
      option.value = code;
      option.textContent = seen[code];
      select.appendChild(option);
    });
  }

  var table = {
    SEARCH_DEBOUNCE_MS: SEARCH_DEBOUNCE_MS,
    view: view,
    holdings: holdings,
    resolve: resolve,
    compare: compare,
    byId: byId,
    bind: bind,
    count: count,
    countryOptions: countryOptions,
    showAllChip: showAllChip,
  };

  root.TerraFolio = root.TerraFolio || {};
  root.TerraFolio.table = table;

  if (typeof module === 'object' && module.exports) module.exports = table;
})(typeof globalThis !== 'undefined' ? globalThis : this);
