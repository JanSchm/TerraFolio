/**
 * map.js — the §7.3 site map: real geography, real coordinates, and a notice when
 * the atlas is not there.
 *
 * Only two things are borrowed: `d3.geoMercator` and `d3.geoPath` for the
 * projection, and `topojson.feature` for the geometry. Everything else — the tints,
 * the markers, the legend — is a handful of SVG elements, because that is all it is.
 *
 * **Sites are plotted from the run's own `holdings`**, not from the live pipeline, so
 * a run reopened after a project has been deleted still shows where its portfolio
 * was (spec §13, api.md §8.2). **Countries are joined on the ISO code**, never on
 * `properties.name`: a string join drops a mismatch silently, and a map quietly
 * missing Greece looks exactly like a map of a portfolio that holds nothing there.
 *
 * Classic script in the browser, CommonJS under node. See docs/decisions.md A-15.
 */
(function (root) {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';

  /** ui-contract.md §5.3, pinned so the committee pack's own projection can match. */
  var CENTRE = [12, 55];
  var SCALE_FACTOR = 1.15;
  var HEIGHT = 300;
  var MIN_RADIUS = 3;
  var RADIUS_PER_ROOT_MW = 0.42;

  /**
   * ISO 3166-1 alpha-3 to numeric, for the fourteen markets.
   *
   * Natural Earth's 110m corpus identifies a country by its **numeric** ISO code and
   * carries no alpha-3, while a project file carries alpha-3 (`pipeline-schema.md`
   * §4.1). So the join needs one of these two tables to exist, and this is the one
   * that is a quotation of a standard rather than of a label: `properties.name` is
   * free text that a corpus update can restyle — "United Kingdom" to "U.K." — and a
   * join on it fails by drawing an untinted country rather than by raising anything.
   *
   * A code with no entry simply is not tinted. Its markers still plot, because they
   * come from the run's own coordinates and not from the atlas at all.
   */
  var ISO_NUMERIC = {
    ESP: '724', PRT: '620', ITA: '380', GRC: '300', FRA: '250', DEU: '276', POL: '616',
    ROU: '642', NLD: '528', DNK: '208', IRL: '372', SWE: '752', FIN: '246', GBR: '826',
  };

  /* ── The atlas ──────────────────────────────────────────────────────────────
     Preferred from the global, because `public/countries-110m.js` is how the atlas
     reaches a page opened from disk, where fetch cannot read a file:// URL (A-15).
     Fetched otherwise, and when neither answers the panel says so. */

  function vendoredAtlas() {
    return (root.TerraFolio && root.TerraFolio.worldAtlas) || null;
  }

  function atlas() {
    var vendored = vendoredAtlas();
    if (vendored) return Promise.resolve(vendored);
    if (typeof fetch !== 'function' || !root.location || root.location.protocol === 'file:') {
      return Promise.resolve(null);
    }
    return fetch('public/countries-110m.json').then(function (response) {
      return response.ok ? response.json() : null;
    }).catch(function () {
      return null;
    });
  }

  /** The libraries the projection needs, present only where they were vendored. */
  function libraries() {
    var geo = root.d3 || {};
    var topo = root.topojson || {};
    if (typeof geo.geoMercator !== 'function' || typeof topo.feature !== 'function') return null;
    return { geo: geo, topo: topo };
  }

  /* ── Drawing ────────────────────────────────────────────────────────────── */

  function element(name, attributes) {
    var node = document.createElementNS(SVG_NS, name);
    Object.keys(attributes || {}).forEach(function (key) {
      node.setAttribute(key, attributes[key]);
    });
    return node;
  }

  function finite(value) {
    return typeof value === 'number' && isFinite(value);
  }

  /** ui-contract.md §5.3: area scales with capacity, with a floor so a small site shows. */
  function radius(capacityMw) {
    var mw = typeof capacityMw === 'number' && capacityMw > 0 ? capacityMw : 0;
    return Math.max(MIN_RADIUS, Math.sqrt(mw) * RADIUS_PER_ROOT_MW);
  }

  function isSolar(row) {
    return row.technology === 'solar';
  }

  /** The set of numeric ids the portfolio holds something in. */
  function heldCountries(sites) {
    var held = {};
    sites.forEach(function (row) {
      var numeric = ISO_NUMERIC[row.iso3];
      if (numeric) held[numeric] = true;
    });
    return held;
  }

  /**
   * Render the map into `panel`, from `sites` — the selected holdings.
   *
   * Returns `true` when it drew, `false` when it degraded to the notice, so a caller
   * can report the panel's state without inspecting the DOM.
   */
  function draw(panel, sites, topology) {
    var libs = libraries();
    if (!panel) return false;
    if (!topology || !libs) return degrade(panel);

    var collection = libs.topo.feature(topology, topology.objects.countries);
    if (!collection || !collection.features) return degrade(panel);

    var width = panel.clientWidth || 420;
    var projection = libs.geo.geoMercator()
      .center(CENTRE)
      .scale(width * SCALE_FACTOR)
      .translate([width / 2, HEIGHT / 2]);
    var path = libs.geo.geoPath(projection);

    var svg = element('svg', {
      viewBox: '0 0 ' + width + ' ' + HEIGHT,
      preserveAspectRatio: 'xMidYMid meet',
      class: 'block h-full w-full',
      'aria-hidden': 'true',
    });

    var held = heldCountries(sites);
    collection.features.forEach(function (feature) {
      var shape = path(feature);
      if (!shape) return;
      svg.appendChild(element('path', {
        d: shape,
        class: held[String(feature.id)] ? 'fill-accent-200' : 'fill-neutral-200',
        stroke: 'currentColor',
        'stroke-width': '0.6',
        'stroke-opacity': '0.16',
      }));
    });

    sites.forEach(function (row) {
      // Checked before projecting, not after. `geoMercator` reads a null coordinate
      // as zero and answers a perfectly finite point in the Gulf of Guinea, so a
      // project with no location would plot — convincingly — off the coast of Ghana.
      if (!finite(row.lat) || !finite(row.lon)) return;
      var at = projection([row.lon, row.lat]);
      if (!at || !finite(at[0]) || !finite(at[1])) return;
      var marker = element('circle', {
        cx: at[0],
        cy: at[1],
        r: radius(row.capacityMw),
        class: isSolar(row) ? 'fill-solar' : 'fill-wind',
        'fill-opacity': '0.82',
        stroke: '#f2f2f3',
        'stroke-width': '1',
      });
      // §7.1: the marker's colour distinguishes the technology, so its name must
      // carry the technology too. The svg itself is aria-hidden; the panel's own
      // label and the holdings table are what a reader uses.
      var title = element('title', {});
      title.textContent = [row.name, isSolar(row) ? 'Solar' : 'Wind',
        Math.round(row.capacityMw) + ' MW', row.country].join(' · ');
      marker.appendChild(title);
      svg.appendChild(marker);
    });

    panel.textContent = '';
    panel.appendChild(svg);
    return true;
  }

  /**
   * spec §13: "Map panel degrades to a notice; the rest of the page is unaffected."
   * The notice replaces the panel's contents only — nothing above or below it knows.
   */
  function degrade(panel) {
    panel.textContent = '';
    var notice = document.createElement('p');
    notice.className = 'flex h-full items-center justify-center text-note text-muted';
    notice.textContent = 'Map data unavailable.';
    panel.appendChild(notice);
    return false;
  }

  /** Fetch or read the atlas, then draw. Resolves to what `draw` returned. */
  function render(panel, sites) {
    if (!panel) return Promise.resolve(false);
    return atlas().then(function (topology) {
      return draw(panel, sites || [], topology);
    }).catch(function () {
      return degrade(panel);
    });
  }

  var map = {
    CENTRE: CENTRE,
    SCALE_FACTOR: SCALE_FACTOR,
    HEIGHT: HEIGHT,
    ISO_NUMERIC: ISO_NUMERIC,
    radius: radius,
    heldCountries: heldCountries,
    atlas: atlas,
    draw: draw,
    degrade: degrade,
    render: render,
  };

  root.TerraFolio = root.TerraFolio || {};
  root.TerraFolio.map = map;

  if (typeof module === 'object' && module.exports) module.exports = map;
})(typeof globalThis !== 'undefined' ? globalThis : this);
