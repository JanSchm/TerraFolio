/**
 * edge-states.js — the three states spec §13 requires a page to render, and that
 * nothing rendered.
 *
 * §13 has nine rows. Six of them are already visible somewhere a user can reach:
 * the feasibility footer carries the two blocking warnings, the holdings table
 * renders an em dash for an undefined IRR, and the committee pack shows a
 * concentration breach and a capacity shortfall on their tiles. Three were not
 * visible anywhere, because the code that would have shown them belongs to the
 * page scripts issue #11 is still writing:
 *
 *   - the site map degrading to a notice when its geometry will not load,
 *   - the mandate page saying that `pipeline/` holds no files, and that the
 *     pipeline moved under a stored run,
 *   - the detail sheet saying that a project's commercial operation date falls
 *     after the hold period.
 *
 * They live in one file rather than in `map.js`, `mandate.js` and `portfolio.js`
 * so that #11, which is in flight, folds three factories in rather than resolving
 * three conflicting files. See docs/decisions.md 4C-9.
 *
 * None of these fetches anything. Each is a pure function of state a page hands
 * it, which is what keeps the pages working over file:// (A-15) and keeps
 * tests/offline.test.js honest.
 *
 * Classic script, not an ES module, for the same reason as every other file here.
 */
(function (root) {
  'use strict';

  var required = (typeof require === 'function');

  /* The same dual resolution controls.js and feasibility.js use: the browser has
     loaded these as classic scripts by the time Alpine initialises, while node
     requires them. Without the node branch none of this is unit-testable. */
  var fmt = required ? require('./format.js') : root.TerraFolio && root.TerraFolio.format;
  if (!fmt) throw new Error('edge-states.js requires format.js to be loaded first');

  var STATUS = required
    ? require('./controls.js').status
    : root.TerraFolio && root.TerraFolio.status;
  if (!STATUS) throw new Error('edge-states.js requires controls.js to be loaded first');

  /* d3-geo and topojson-client are the map's only dependencies, and their absence
     is one of the ways the map becomes unavailable — so they are looked up, never
     asserted. */
  function geo() {
    if (required) { try { return require('../vendor/d3-geo.min.js'); } catch (e) { return null; } }
    return root.d3 || null;
  }
  function topo() {
    if (required) {
      try { return require('../vendor/topojson-client.min.js'); } catch (e) { return null; }
    }
    return root.topojson || null;
  }

  /**
   * The sentences this file owns.
   *
   * `mapUnavailable` is pinned verbatim by ui-contract.md §5.3 and is the same
   * string the committee pack renders server-side, so the two surfaces cannot
   * drift. `emptyPipeline` is new: §13 requires the mandate page to say that
   * `pipeline/` is empty, and no document pinned the words (4C-1). It is
   * deliberately different from NO_CANDIDATES — "there are no files" and "no file
   * passes your screens" send a user to different places, and telling somebody to
   * widen a screen when there is nothing to widen is worse than saying nothing.
   *
   * There is no sentence here for a moved pipeline. The server already has one —
   * `The pipeline changed since you loaded it; reload and try again.` — and a
   * second copy of it in JavaScript is a third place for it to drift (4C-6,
   * following 3A-11). `pipelineNotice` renders the one the 409 carried.
   */
  var MESSAGES = {
    mapUnavailable: 'Map data unavailable.',
    emptyPipeline: 'The pipeline holds no project files. Add files to pipeline/ and reload.',
  };

  /* ui-contract.md §5.3, in one place so the geometry is quotable rather than
     scattered through the rendering. The committee pack reads the same numbers
     from export/pack-layout.json. */
  var MAP = {
    width: 960, height: 300,
    centreLon: 12, centreLat: 55, scaleFactor: 1.15,
    countryStroke: 0.6,
    markerRadiusFloor: 3, markerRadiusFactor: 0.42,
    markerFillOpacity: 0.82, markerStroke: 1,
  };

  function isFiniteNumber(value) {
    return typeof value === 'number' && isFinite(value);
  }

  /**
   * §7.3 and §13: a real projection of the selected sites, or a notice.
   *
   * The degradation is the part §13 legislates, and it is deliberately total: a
   * missing atlas, a malformed one, or a missing projection library all produce
   * the same notice, and none of them throws. A map that half-drew — countries
   * but no markers, or markers at NaN — would be worse than one that says it is
   * not there, because a reader cannot tell a missing country from an empty one.
   */
  function siteMap(options) {
    var o = options || {};
    return {
      sites: o.sites || [],
      atlas: o.atlas !== undefined ? o.atlas : (root.TerraFolio && root.TerraFolio.worldAtlas),

      /** The countries the portfolio holds an asset in, for the tint. */
      get heldCountries() {
        var codes = {};
        this.sites.forEach(function (site) {
          if (site && site.iso3) codes[site.iso3] = true;
        });
        return codes;
      },

      get available() {
        var atlas = this.atlas;
        if (!atlas || !atlas.objects || !atlas.objects.countries) return false;
        return Boolean(geo() && topo());
      },

      get notice() { return this.available ? '' : MESSAGES.mapUnavailable; },

      /**
       * The panel's markup: an <svg> when the geometry is there, a <p> when not.
       *
       * A portfolio with no sites yet draws nothing rather than an empty atlas —
       * the panel is a summary of a selection, and there is no selection before a
       * run. A *missing* atlas still says so, because that is a fault the user
       * can act on whether or not a run has happened.
       */
      get markup() {
        if (!this.available) return '<p class="notice">' + MESSAGES.mapUnavailable + '</p>';
        if (!this.sites.length) return '';
        return this.svg();
      },

      svg: function () {
        var d3 = geo();
        var projection = d3.geoMercator()
          .center([MAP.centreLon, MAP.centreLat])
          .scale(MAP.width * MAP.scaleFactor)
          .translate([MAP.width / 2, MAP.height / 2]);
        var path = d3.geoPath(projection);
        var held = this.heldCountries;

        var countries = topo().feature(this.atlas, this.atlas.objects.countries).features;
        var shapes = countries.map(function (country) {
          var drawn = path(country);
          if (!drawn) return '';
          var fill = held[country.id] ? 'var(--color-accent-200)' : 'var(--color-neutral-200)';
          return '<path d="' + drawn + '" fill="' + fill
            + '" stroke="var(--color-divider)" stroke-width="' + MAP.countryStroke + '"/>';
        }).join('');

        var markers = this.sites.map(function (site) {
          if (!isFiniteNumber(site.lat) || !isFiniteNumber(site.lon)) return '';
          var point = projection([site.lon, site.lat]);
          if (!point || !isFiniteNumber(point[0]) || !isFiniteNumber(point[1])) return '';
          var mw = isFiniteNumber(site.capacityMw) ? site.capacityMw : 0;
          var radius = Math.max(
            MAP.markerRadiusFloor, Math.sqrt(mw) * MAP.markerRadiusFactor
          );
          var colour = site.technology === 'solar'
            ? 'var(--color-accent-700)'
            : 'var(--color-accent-400)';
          return '<circle cx="' + point[0].toFixed(1) + '" cy="' + point[1].toFixed(1)
            + '" r="' + radius.toFixed(1) + '" fill="' + colour
            + '" fill-opacity="' + MAP.markerFillOpacity
            + '" stroke="var(--color-bg)" stroke-width="' + MAP.markerStroke + '"/>';
        }).join('');

        return '<svg viewBox="0 0 ' + MAP.width + ' ' + MAP.height
          + '" preserveAspectRatio="xMidYMid meet" class="h-full w-full">'
          + shapes + markers + '</svg>';
      },
    };
  }

  /**
   * §13: the pipeline is empty, or it moved under a stored run.
   *
   * Both are blockers rather than advisories — one cannot be run against, the
   * other cannot be re-run against — so both take the blocking mark and word from
   * the one status vocabulary controls.js exports. Nothing here is carried by
   * colour alone (§7.1).
   */
  function pipelineNotice(options) {
    var o = options || {};
    return {
      /** 'none', 'empty' or 'moved'. */
      state: o.state || 'none',

      /** The sentence a 409 carried, for the 'moved' state. */
      detail: o.detail || '',

      get visible() { return this.state === 'empty' || this.state === 'moved'; },

      get message() {
        if (this.state === 'empty') return MESSAGES.emptyPipeline;
        if (this.state === 'moved') return this.detail;
        return '';
      },

      get mark() { return this.visible ? STATUS.MARK.blocking : STATUS.MARK.none; },
      get word() { return this.visible ? STATUS.WORD.blocking : ''; },
      get toneClass() { return this.visible ? STATUS.TONE.blocking : ''; },

      /**
       * `GET /pipeline/status` → a state. `fileCount` rather than `loadedCount`,
       * because a directory of files that all failed their tie-outs is a
       * different problem with a different answer — those files are named in the
       * rejection list, and telling their author the pipeline is empty would send
       * them looking for a directory that is not the one at fault.
       */
      fromStatus: function (status) {
        this.state = (status && status.fileCount === 0) ? 'empty' : 'none';
        return this.state;
      },

      /** A 409 body → the 'moved' state, carrying the server's own sentence. */
      fromConflict: function (body) {
        var error = body && body.error;
        if (!error || error.code !== 'PIPELINE_MOVED') return this.state;
        this.state = 'moved';
        this.detail = error.message || '';
        return this.state;
      },
    };
  }

  /**
   * §13 and ui-contract.md §5.5: a project whose COD falls after the hold period
   * contributes only construction outflows and an exit value, and the drawer says
   * so.
   *
   * Computed from three figures the page already has — the project's COD, the
   * pipeline's base year and the mandate's hold period — rather than from a flag
   * on the wire, because a flag would be a mandate-dependent value and nothing
   * mandate-dependent is stored (epic §5, 4C-5). Moving the hold slider changes
   * the answer, which is exactly the property a stored flag would lose.
   *
   * The tone is `info`, not a breach. Nothing is outside the mandate here — the
   * portfolio is doing what a short hold and a late COD imply — and `text-breach`
   * marks a mandate breach and nothing else.
   */
  function holdingNote(options) {
    var o = options || {};
    return {
      codYear: o.codYear,
      baseYear: o.baseYear,
      holdYears: o.holdYears,

      /** The last year the truncated cash-flow series covers. */
      get exitYear() {
        if (!isFiniteNumber(this.baseYear) || !isFiniteNumber(this.holdYears)) return null;
        return this.baseYear + this.holdYears - 1;
      },

      get afterHold() {
        var exit = this.exitYear;
        return exit !== null && isFiniteNumber(this.codYear) && this.codYear > exit;
      },

      get message() {
        if (!this.afterHold) return '';
        return 'Commercial operation falls after the ' + fmt.count(this.holdYears)
          + '-year hold. The project contributes construction outflows and an exit value only.';
      },

      /** The COD row's own suffix, so the figure and the caveat sit together. */
      get codLabel() {
        if (!isFiniteNumber(this.codYear)) return fmt.DASH;
        if (!this.afterHold) return fmt.year(this.codYear);
        return fmt.year(this.codYear) + ' ' + fmt.DASH + ' after the '
          + fmt.count(this.holdYears) + '-year hold';
      },

      get mark() { return this.afterHold ? STATUS.MARK.info : STATUS.MARK.none; },
      get word() { return this.afterHold ? STATUS.WORD.info : ''; },
    };
  }

  var factories = {
    siteMap: siteMap,
    pipelineNotice: pipelineNotice,
    holdingNote: holdingNote,
  };

  root.TerraFolio = root.TerraFolio || {};
  root.TerraFolio.edgeStates = factories;

  if (root.document) {
    root.document.addEventListener('alpine:init', function () {
      Object.keys(factories).forEach(function (name) {
        root.Alpine.data(name, factories[name]);
      });
    });
  }

  if (typeof module === 'object' && module.exports) {
    module.exports = Object.assign({}, factories, { messages: MESSAGES, map: MAP });
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
