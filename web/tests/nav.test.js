/**
 * The sticky nav is duplicated into four files by tools/splice-nav.mjs rather than
 * templated, because the pages have to work from disk with no build step beyond CSS.
 * Duplication drifts, so this asserts the four copies are identical apart from which
 * step carries aria-current — the one thing that should differ.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { WEB, PAGES } = require('./helpers/page.js');

function navBlock(page) {
  const html = fs.readFileSync(path.join(WEB, page), 'utf8');
  const start = html.indexOf('<!-- nav:start');
  const end = html.indexOf('<!-- nav:end -->');
  assert.ok(start !== -1 && end > start, `${page} has no nav block`);
  return html.slice(start, end);
}

/** Removes the single per-page attribute, leaving what must be identical. */
const normalise = (block) => block.replace(/\s*aria-current="step"/g, '');

test('every page carries the shared nav block', () => {
  for (const page of PAGES) assert.ok(navBlock(page).length > 200, `${page} nav looks truncated`);
});

test('the four nav blocks are identical apart from the current step', () => {
  const [first, ...rest] = PAGES;
  const reference = normalise(navBlock(first));
  for (const page of rest) {
    assert.equal(normalise(navBlock(page)), reference,
      `${page}'s nav has drifted from ${first}'s. Edit tools/nav.part.html and re-run ` +
      'node tools/splice-nav.mjs rather than editing a page by hand.');
  }
});

test('each product page marks exactly one current step; the style guide marks none', () => {
  const expected = {
    'mandate.html': 'mandate',
    'search.html': 'search',
    'portfolio.html': 'portfolio',
    'styleguide.html': null,
  };
  for (const page of PAGES) {
    const block = navBlock(page);
    const marked = [...block.matchAll(/data-step="([a-z]+)" aria-current="step"/g)].map((m) => m[1]);
    if (expected[page] === null) {
      assert.deepEqual(marked, [], 'the style guide is not a step in the flow');
    } else {
      assert.deepEqual(marked, [expected[page]],
        `${page} must mark exactly one step, and it must be its own`);
    }
  }
});

test('the stepper is an ordered list, so its order is conveyed and not just drawn', () => {
  const block = navBlock(PAGES[0]);
  assert.match(block, /<ol[^>]*aria-label="Progress"/);
  assert.equal((block.match(/<li/g) || []).length, 5, 'three steps and two separators');
  assert.match(block, /aria-hidden="true"[^>]*>&#8212;/,
    'the em-dash separators are decorative and must be hidden from readers');
});

test('the source of truth is the partial, not any one page', () => {
  const partial = fs.readFileSync(path.join(WEB, 'tools', 'nav.part.html'), 'utf8');
  assert.equal(normalise(navBlock('mandate.html')).trim(),
    partial.slice(partial.indexOf('<!-- nav:start'), partial.indexOf('<!-- nav:end -->')).trim());
});

test('re-running the splice tool is a no-op, so nav edits can be propagated', () => {
  const { execFileSync } = require('node:child_process');
  const before = PAGES.map((p) => fs.readFileSync(path.join(WEB, p), 'utf8'));

  execFileSync(process.execPath, ['tools/splice-nav.mjs'], { cwd: WEB, encoding: 'utf8' });

  PAGES.forEach((page, i) => {
    assert.equal(fs.readFileSync(path.join(WEB, page), 'utf8'), before[i],
      `${page} changed on a second splice; the tool must be idempotent`);
  });
});

test('every page keeps its NAV marker, which is what makes the tool re-runnable', () => {
  for (const page of PAGES) {
    const html = fs.readFileSync(path.join(WEB, page), 'utf8');
    const marker = html.match(/<!--NAV:([a-z]+)-->/);
    assert.ok(marker,
      `${page} has no <!--NAV:step--> marker, so tools/splice-nav.mjs can never update it again`);

    // The marker and the block must agree about which step this page is.
    const block = navBlock(page);
    const current = block.match(/data-step="([a-z]+)" aria-current="step"/);
    if (marker[1] === 'none') {
      assert.equal(current, null, 'the style guide marks no step');
    } else {
      assert.equal(current?.[1], marker[1],
        `${page} declares step "${marker[1]}" but marks "${current?.[1]}" as current`);
    }
  }
});

