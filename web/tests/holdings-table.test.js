/**
 * The holdings table's sort state and row signals — ui-contract.md §5.4 and §7.1.
 *
 * Two things are checked here that nothing checked before.
 *
 * `aria-sort` existed on every sortable header, hard-coded to "none", and nothing
 * moved it. An attribute that never changes is worse than an absent one: it tells a
 * screen-reader user the table is unsorted while the arrow beside it says otherwise.
 *
 * And the sort keys were the mockup's internal names — `lev`, `cf`, `gwh`,
 * `offtake`. §5.4 is explicit that they are the run's `holdings` field names,
 * because #12 asserts client and server produce the same order and that needs one
 * agreed name per column. This reads the fifteen keys out of the contract rather
 * than transcribing them, so the markup cannot drift from it.
 *
 * The row assertions run over rows cloned from the template with fixture data, since
 * `rows` ships empty and #11 assigns it. That is the only way these signals can be
 * checked before group 4 — and it is why they are worth landing now rather than
 * retrofitting later.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { loadPage } = require('./helpers/page.js');
const { ROWS, LOCKED, NOT_SELECTED, SELECTED } = require('./helpers/holdings.js');
const { status } = require('../js/controls.js');

const DOCS = path.resolve(__dirname, '..', '..', 'docs');

/** §5.4's sixteen-row column table; the last cell of each row is the sort key. */
function sortKeysFromContract() {
  const ui = fs.readFileSync(path.join(DOCS, 'ui-contract.md'), 'utf8');
  const section = ui.slice(ui.indexOf('### 5.4'), ui.indexOf('### 5.5'));
  const keys = [];
  for (const line of section.split('\n')) {
    const cells = line.split('|').map((c) => c.trim());
    if (cells.length < 7 || !/^\d+$/.test(cells[1])) continue;
    const key = cells[5].replace(/`/g, '');
    if (key && key !== 'not sortable') keys.push(key);
  }
  assert.equal(keys.length, 15, 'ui-contract.md §5.4 should name fifteen sort keys');
  return keys;
}

async function holdings(page = 'portfolio.html') {
  const dom = await loadPage(page);
  const table = dom.window.document.querySelector('[data-region="holdings"]').closest('table');
  return { dom, table, data: dom.window.Alpine.$data(table) };
}

/** Alpine renders on a microtask; one macrotask is enough to see the result. */
const settled = (dom) => new Promise((r) => dom.window.setTimeout(r, 30));

test('the sort keys are the field names §5.4 pins, not the mockup\'s', async () => {
  const { dom, table } = await holdings();
  const inMarkup = [...table.querySelectorAll('thead [data-sort]')].map((b) => b.dataset.sort);
  const columns = [...table.querySelectorAll('thead th[data-column]')].map((th) => th.dataset.column);
  dom.window.close();

  assert.deepEqual(inMarkup, sortKeysFromContract(),
    'a column\'s sort key is the name the run\'s holdings array uses (api.md §8.2)');
  assert.deepEqual(columns, inMarkup, 'th and its button must name the same column');
});

test('aria-sort follows the sort state, and only one column claims it', async () => {
  const { dom, table } = await holdings();
  const th = (key) => table.querySelector(`thead th[data-column="${key}"]`);
  const button = (key) => table.querySelector(`thead [data-sort="${key}"]`);
  const sorted = () => [...table.querySelectorAll('thead th[data-column]')]
    .filter((e) => e.getAttribute('aria-sort') !== 'none')
    .map((e) => `${e.dataset.column}:${e.getAttribute('aria-sort')}`);

  // §5.4's default, before anyone has clicked anything.
  await settled(dom);
  assert.deepEqual(sorted(), ['equityIrr:descending'], 'default sort is equityIrr descending');

  button('capacityMw').click();
  await settled(dom);
  assert.deepEqual(sorted(), ['capacityMw:descending'],
    'a new column sorts descending, and the old one gives up its aria-sort');

  button('capacityMw').click();
  await settled(dom);
  assert.equal(th('capacityMw').getAttribute('aria-sort'), 'ascending',
    'clicking the sorted column reverses it');

  button('capacityMw').click();
  await settled(dom);
  assert.equal(th('capacityMw').getAttribute('aria-sort'), 'descending', 'and reverses back');
  dom.window.close();
});

test('the arrow is decorative and mirrors aria-sort, so they cannot disagree', async () => {
  const { dom, table } = await holdings();
  const arrowOf = (key) => table.querySelector(`thead [data-sort="${key}"] [data-field="sort-arrow"]`);

  for (const key of ['name', 'minDscr', 'gearing']) {
    table.querySelector(`thead [data-sort="${key}"]`).click();
    await settled(dom);
    const th = table.querySelector(`thead th[data-column="${key}"]`);
    const arrow = arrowOf(key);
    assert.equal(arrow.getAttribute('aria-hidden'), 'true', 'the arrow is decoration');
    assert.equal(arrow.textContent, status.MARK[th.getAttribute('aria-sort')],
      `${key}'s arrow must show the direction aria-sort reports`);

    // And every other column shows nothing at all.
    for (const other of ['name', 'minDscr', 'gearing'].filter((k) => k !== key)) {
      assert.equal(arrowOf(other).textContent, '', `${other} is not the sorted column`);
    }
  }
  dom.window.close();
});

test('sorting announces itself to whoever holds the rows', async () => {
  const { dom, table } = await holdings();
  const heard = [];
  // Flattened out of the page's realm: a detail object built there has a different
  // Object.prototype, which deepEqual compares and reports as an identical mismatch.
  dom.window.document.addEventListener('tf:change', (e) => heard.push(
    { name: e.detail.name, field: e.detail.value.field, direction: e.detail.value.direction }));
  table.querySelector('thead [data-sort="minDscr"]').click();
  await settled(dom);
  dom.window.close();

  assert.deepEqual(heard, [{ name: 'holdingsSort', field: 'minDscr', direction: 'descending' }],
    'the table owns aria-sort; #11 owns the 50 ms reorder (§12), and this is the seam');
});

/* ── The row, rendered ──────────────────────────────────────────────────────── */

test('a row carries every second signal §7.1 owes this table', async () => {
  const { dom, table, data } = await holdings();
  data.dscrFloor = 1.25;                     // the mandate's floor; #11 supplies it
  data.rows = ROWS;
  await settled(dom);

  const rows = [...table.querySelectorAll('[data-region="holdings"] tr')];
  assert.equal(rows.length, 3, 'the template renders one row per holding');
  const byId = Object.fromEntries(rows.map((r) => [r.dataset.projectId, r]));

  // Locked: an accent-100 ground, so it owes a filled square and the word.
  assert.ok(byId[LOCKED.id].className.includes('bg-highlight'));
  assert.equal(byId[LOCKED.id].children[0].textContent.replace(/\s+/g, ' ').trim(),
    `${status.MARK.locked} ${status.WORD.locked}`);

  // Not selected: a neutral-200 ground, so it owes `Not selected` in the row's name.
  assert.ok(byId[NOT_SELECTED.id].className.includes('bg-deemph'));
  assert.match(byId[NOT_SELECTED.id].children[0].textContent, /Not selected/);

  // Selected: no tone, and therefore nothing to compensate for.
  assert.equal(byId[SELECTED.id].className.includes('bg-highlight'), false);
  assert.equal(byId[SELECTED.id].className.includes('bg-deemph'), false);
  assert.match(byId[SELECTED.id].children[0].textContent, /Not locked/);
  assert.equal(/Not selected/.test(byId[SELECTED.id].children[0].textContent), false);

  dom.window.close();
});

test('a Min DSCR under the floor carries a mark and names the floor it broke', async () => {
  const { dom, table, data } = await holdings();
  data.dscrFloor = 1.25;
  data.rows = ROWS;
  await settled(dom);

  const cell = (id) => table.querySelector(`[data-project-id="${id}"]`).children[14];
  const breached = cell(NOT_SELECTED.id);

  assert.match(breached.className, /text-breach/, 'the alert tone, which is not enough on its own');
  assert.equal(breached.querySelector('[aria-hidden="true"]').textContent, status.MARK.breach);
  assert.equal(breached.querySelector('.sr-only').textContent, ', below the 1.25× floor');
  assert.equal(breached.textContent.replace(/\s+/g, ''), '1.18×!,belowthe1.25×floor');

  // A compliant cell claims nothing.
  const fine = cell(SELECTED.id);
  assert.equal(/text-breach/.test(fine.className), false);
  assert.equal(fine.querySelector('[aria-hidden="true"]').textContent, '');
  assert.equal(fine.querySelector('.sr-only').textContent, '');
  dom.window.close();
});

test('a breach cannot be claimed before the mandate supplies a floor', async () => {
  const { dom, table, data } = await holdings();
  data.rows = ROWS;                          // dscrFloor left null
  await settled(dom);
  const cell = table.querySelector(`[data-project-id="${NOT_SELECTED.id}"]`).children[14];
  assert.equal(/text-breach/.test(cell.className), false,
    'with no floor known there is nothing to be under; the cell must not guess one');
  assert.equal(cell.textContent.trim(), '1.18×');
  dom.window.close();
});

test('every figure in a row goes through format.js, em dash included', async () => {
  const { dom, table, data } = await holdings();
  data.dscrFloor = 1.25;
  data.rows = ROWS;
  await settled(dom);

  const cells = [...table.querySelector(`[data-project-id="${SELECTED.id}"]`).children]
    .map((td) => td.textContent.replace(/\s+/g, ' ').trim());
  assert.equal(cells.length, 16, '§5.4 pins sixteen columns');
  assert.deepEqual(cells.slice(2), [
    'Solar', 'Greenfield', '180', '2028', '141', '56', '60%', '24.6%', '388', '41', '72%',
    '12.4%', '1.38×', '2.7',
  ]);

  // §13 and api.md §1.4: an undefined IRR is an em dash, never a zero.
  const undefinedIrr = table.querySelector(`[data-project-id="${NOT_SELECTED.id}"]`).children[13];
  assert.equal(undefinedIrr.textContent.trim(), '—');
  dom.window.close();
});

test('a row is opened from a real button, so the drawer is reachable by keyboard', async () => {
  const { dom, table, data } = await holdings();
  data.rows = ROWS;
  await settled(dom);
  const openers = table.querySelectorAll('[data-region="holdings"] [data-action="open-drawer"]');
  assert.equal(openers.length, 3);
  for (const opener of openers) {
    assert.equal(opener.tagName, 'BUTTON',
      'a clickable <tr> is not keyboard-operable; §7.2 wants every control reachable');
    assert.ok(opener.textContent.trim().length > 0, 'and it must have an accessible name');
  }
  dom.window.close();
});
