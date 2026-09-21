/**
 * "All four pages open directly from disk with no server and no network."
 *
 * Asserted as a static property rather than by watching a browser: every reference a
 * page makes must be a relative path that resolves to a file in the repository. That
 * catches the regression this guards against — someone adding a Google Fonts link or
 * a CDN script tag — at the point it is written rather than on an aeroplane.
 *
 * §12 forbids the network dependency; docs/decisions.md D1 and D3 cover why the
 * scripts are classic and why the world atlas ships twice.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { WEB, PAGES } = require('./helpers/page.js');

const REMOTE = /^(https?:)?\/\/|^data:|^blob:/i;

/** Every src/href a page declares, with the attribute it came from. */
function references(html) {
  const out = [];
  const tag = /<(script|link|img|source|iframe|a)\b[^>]*>/gi;
  let m;
  while ((m = tag.exec(html))) {
    const el = m[0];
    const src = /\s(?:src|href)\s*=\s*"([^"]*)"/i.exec(el);
    if (src) out.push({ tag: m[1].toLowerCase(), url: src[1], html: el.slice(0, 90) });
  }
  return out;
}

for (const page of PAGES) {
  const html = fs.readFileSync(path.join(WEB, page), 'utf8');

  test(`${page}: every reference is local`, () => {
    const remote = references(html).filter((r) => REMOTE.test(r.url));
    assert.deepEqual(remote.map((r) => r.url), [],
      `${page} would need the network to render. Vendor the asset into web/ instead.`);
  });

  test(`${page}: every referenced file exists on disk`, () => {
    const missing = references(html)
      .filter((r) => r.tag !== 'a' && !REMOTE.test(r.url) && !r.url.startsWith('#'))
      .filter((r) => !fs.existsSync(path.join(WEB, r.url.split(/[?#]/)[0])));
    assert.deepEqual(missing.map((r) => r.url), [],
      `${page} references files that are not there; opening it from disk would 404`);
  });

  test(`${page}: links go to sibling pages, not to a server`, () => {
    const links = references(html).filter((r) => r.tag === 'a');
    for (const link of links) {
      assert.ok(!REMOTE.test(link.url), `${link.url} leaves the local page set`);
      if (!link.url.startsWith('#')) {
        assert.ok(fs.existsSync(path.join(WEB, link.url.split('#')[0])),
          `${link.url} does not resolve to a file`);
      }
    }
  });

  test(`${page}: no module scripts, which browsers refuse to load over file://`, () => {
    assert.ok(!/<script[^>]*type\s*=\s*"module"/i.test(html),
      'a module script is CORS-blocked from a file:// origin — see docs/decisions.md D1');
  });

  test(`${page}: no fetch or XHR at page level, which file:// also blocks`, () => {
    const inline = [...html.matchAll(/<script(?![^>]*\ssrc=)[^>]*>([\s\S]*?)<\/script>/gi)]
      .map((m) => m[1]).join('\n');
    assert.ok(!/\bfetch\s*\(|XMLHttpRequest/.test(inline),
      'file:// blocks both; the world atlas ships as a classic script for this reason');
  });
}

test('the stylesheet only ever asks for fonts that are in web/fonts/', () => {
  const css = fs.readFileSync(path.join(WEB, 'src', 'fonts.css'), 'utf8');
  const urls = [...css.matchAll(/url\('([^']+)'\)/g)].map((m) => m[1]);
  assert.ok(urls.length >= 10, `expected the vendored faces, saw ${urls.length}`);
  for (const url of urls) {
    assert.ok(!REMOTE.test(url), `${url} is remote; §12 forbids a network dependency for type`);
    assert.ok(fs.existsSync(path.resolve(WEB, 'src', url)), `${url} is missing from web/fonts/`);
  }
});

test('the built stylesheet inlines no remote reference', () => {
  const dist = path.join(WEB, 'dist', 'app.css');
  assert.ok(fs.existsSync(dist), 'run npm run build first');
  const css = fs.readFileSync(dist, 'utf8');
  const urls = [...css.matchAll(/url\(([^)]+)\)/g)].map((m) => m[1].replace(/['"]/g, ''));
  const remote = urls.filter((u) => REMOTE.test(u));
  assert.deepEqual(remote, [], 'dist/app.css must not reach the network either');
});
