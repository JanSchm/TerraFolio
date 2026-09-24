/**
 * map.js — the §7.3 site map, and the notice that replaces it.
 *
 * The degradation is the half worth guarding: spec §13 requires the panel to fail
 * on its own and leave the rest of the page working, and that is a path nothing
 * exercises in normal use.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { JSDOM } = require('jsdom');
const { servePage } = require('./helpers/served.js');

const WEB = path.resolve(__dirname, '..');
const map = require('../js/map.js');

function site(overrides) {
  return Object.assign({
    id: 'P01', name: 'Almonte Solar', country: 'Spain', iso3: 'ESP',
    technology: 'solar', capacityMw: 180, lat: 39.33, lon: -1.12,
  }, overrides || {});
}

/* ── Geometry ────────────────────────────────────────────────────────────────── */

test('the projection is ui-contract.md §5.3\'s, so the committee pack can match it', () => {
  assert.deepEqual(map.CENTRE, [12, 55]);
  assert.equal(map.SCALE_FACTOR, 1.15);
  assert.equal(map.HEIGHT, 300);
});

test('marker area scales with capacity, with a floor so a small site still shows', () => {
  assert.equal(map.radius(180), Math.sqrt(180) * 0.42);
  assert.equal(map.radius(1), 3, 'below the floor the radius is the floor');
  assert.equal(map.radius(0), 3);
  assert.equal(map.radius(null), 3, 'and a missing capacity is not a zero-radius marker');
  assert.ok(map.radius(400) > map.radius(100));
});

/* ── The country join ────────────────────────────────────────────────────────── */

test('countries are joined on the ISO code, never on the corpus\'s own label', () => {
  assert.deepEqual(map.heldCountries([site({ iso3: 'ESP' }), site({ iso3: 'GBR' })]),
    { 724: true, 826: true });
});

test('all fourteen markets resolve, and the atlas carries every one of those ids', () => {
  const topology = JSON.parse(fs.readFileSync(path.join(WEB, 'public', 'countries-110m.json'), 'utf8'));
  const ids = new Set(topology.objects.countries.geometries.map((g) => String(g.id)));
  const missing = Object.entries(map.ISO_NUMERIC).filter(([, numeric]) => !ids.has(numeric));
  assert.deepEqual(missing, [], 'a market with no shape would be silently untinted');
  assert.equal(Object.keys(map.ISO_NUMERIC).length, 14);
});

test('a country the table does not know is simply not tinted', () => {
  assert.deepEqual(map.heldCountries([site({ iso3: 'USA' })]), {},
    'and its markers still plot, because they come from the run and not from the atlas');
});

/* ── Rendering and degrading ─────────────────────────────────────────────────── */

function panelIn(dom) {
  const host = dom.window.document.createElement('div');
  host.setAttribute('data-region', 'map');
  dom.window.document.body.appendChild(host);
  return host;
}

test('with no atlas the panel degrades to the notice §13 names', async () => {
  const dom = new JSDOM('<!doctype html><p>', { url: 'http://localhost/' });
  const panel = panelIn(dom);
  global.document = dom.window.document;
  try {
    assert.equal(map.draw(panel, [site()], null), false);
    assert.equal(panel.textContent.trim(), 'Map data unavailable.');
    assert.equal(panel.querySelector('svg'), null);
  } finally {
    delete global.document;
    dom.window.close();
  }
});

test('degrading replaces the panel\'s contents and nothing else', async () => {
  const dom = await servePage('portfolio.html');
  const w = dom.window;
  const panel = w.document.querySelector('[data-region="map"]');
  const before = w.document.querySelectorAll('[data-tile]').length;
  w.TerraFolio.map.degrade(panel);
  assert.equal(panel.textContent.trim(), 'Map data unavailable.');
  assert.equal(panel.getAttribute('aria-label'), 'Map of selected sites across Europe',
    'the panel keeps its own label; only its contents changed');
  assert.equal(w.document.querySelectorAll('[data-tile]').length, before,
    'spec §13: the rest of the page is unaffected');
  dom.window.close();
});

test('with the atlas the map draws a shape per country and a marker per site', async () => {
  const dom = await servePage('portfolio.html');
  const w = dom.window;
  const panel = w.document.querySelector('[data-region="map"]');
  const sites = [site({ id: 'P01', technology: 'solar', iso3: 'ESP' }),
    site({ id: 'P02', technology: 'onshore_wind', iso3: 'GBR', name: 'Suffolk Wind', country: 'United Kingdom' })];
  const drew = w.TerraFolio.map.draw(panel, sites, w.TerraFolio.worldAtlas);
  assert.equal(drew, true);
  const svg = panel.querySelector('svg');
  assert.ok(svg.querySelectorAll('path').length > 100, 'the whole corpus is drawn, not only Europe');
  assert.equal(svg.querySelectorAll('circle').length, 2);

  const tinted = [...svg.querySelectorAll('path')].filter((p) => p.getAttribute('class') === 'fill-accent-200');
  assert.equal(tinted.length, 2, 'exactly the two countries the portfolio holds');

  const [solar, wind] = svg.querySelectorAll('circle');
  assert.equal(solar.getAttribute('class'), 'fill-solar');
  assert.equal(wind.getAttribute('class'), 'fill-wind');
  assert.match(solar.querySelector('title').textContent, /Solar/,
    '§7.1: the marker colour is a colour, so its name has to carry the technology');
  assert.match(wind.querySelector('title').textContent, /Suffolk Wind .* Wind .* 180 MW .* United Kingdom/);
  dom.window.close();
});

test('a site with no usable coordinates is skipped rather than drawn at the origin', async () => {
  const dom = await servePage('portfolio.html');
  const w = dom.window;
  const panel = w.document.querySelector('[data-region="map"]');
  w.TerraFolio.map.draw(panel, [site(), site({ id: 'P02', lat: null, lon: null })], w.TerraFolio.worldAtlas);
  assert.equal(panel.querySelectorAll('circle').length, 1);
  dom.window.close();
});

/* ── The marker's own figures go through format.js ───────────────────────────── */

test('a marker names its capacity the way the rest of the product does', async () => {
  const dom = await servePage('portfolio.html');
  const w = dom.window;
  const panel = w.document.querySelector('[data-region="map"]');
  w.TerraFolio.map.draw(panel, [site({ capacityMw: 1234, name: 'Grande Solar' })],
    w.TerraFolio.worldAtlas);
  const title = panel.querySelector('circle title').textContent;
  assert.match(title, /1,234 MW/,
    'spec §14: grouped, en-GB, and through format.js like every other figure');
  assert.equal(/1234 MW/.test(title), false);
  assert.match(title, /^Grande Solar · Solar · 1,234 MW · Spain$/,
    'and joined with the one inline separator');
  dom.window.close();
});

test('a marker with no capacity says so rather than printing nothing', async () => {
  const dom = await servePage('portfolio.html');
  const w = dom.window;
  const panel = w.document.querySelector('[data-region="map"]');
  w.TerraFolio.map.draw(panel, [site({ capacityMw: null })], w.TerraFolio.worldAtlas);
  assert.match(panel.querySelector('circle title').textContent, /—/);
  dom.window.close();
});

/* ── §13's degradation is total (4C-9) ───────────────────────────────────────── */

test('an atlas that is present but unusable degrades rather than throwing', async () => {
  const dom = await servePage('portfolio.html');
  const w = dom.window;
  const panel = w.document.querySelector('[data-region="map"]');
  const broken = [
    ['no objects at all', {}],
    ['an objects block with no countries', { objects: {} }],
    ['countries with no geometries', { objects: { countries: {} } }],
    ['geometries pointing at arcs that are not there', {
      type: 'Topology', arcs: [],
      objects: { countries: { type: 'GeometryCollection', geometries: [{ type: 'Polygon', arcs: [[99]] }] } },
    }],
  ];
  for (const [what, atlas] of broken) {
    let drew = null;
    assert.doesNotThrow(() => { drew = w.TerraFolio.map.draw(panel, [site()], atlas); }, what);
    assert.equal(drew, false, what);
    assert.equal(panel.textContent.trim(), 'Map data unavailable.', what);
    assert.equal(panel.querySelector('svg'), null, what);
  }
  dom.window.close();
});

test('a good atlas still draws after a broken one has been through the panel', async () => {
  const dom = await servePage('portfolio.html');
  const w = dom.window;
  const panel = w.document.querySelector('[data-region="map"]');
  w.TerraFolio.map.draw(panel, [site()], { objects: { countries: {} } });
  assert.equal(w.TerraFolio.map.draw(panel, [site()], w.TerraFolio.worldAtlas), true);
  assert.ok(panel.querySelector('svg circle'));
  dom.window.close();
});
