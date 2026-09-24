/**
 * Collects the contrast audit over every state the four pages can render.
 *
 * The pages are walked with the holdings rows populated and both overlays open,
 * because a pairing that only appears in a drawer is still a pairing a user reads.
 */
const { loadPage, PAGES } = require('./page.js');
const { pairings } = require('./contrast.js');
const { ROWS } = require('./holdings.js');

const settled = (dom) => new Promise((r) => dom.window.setTimeout(r, 40));

async function auditRows() {
  const all = new Map();
  for (const page of PAGES) {
    const dom = await loadPage(page);
    const d = dom.window.document;

    // Everything the page can show, not only what it shows on arrival.
    const table = d.querySelector('[data-region="holdings"]');
    if (table) {
      const data = dom.window.Alpine.$data(table.closest('table'));
      data.dscrFloor = 1.25;
      data.rows = ROWS;
      await settled(dom);
    }
    for (const trigger of ['[data-action="export"]', '[data-region="holdings"] [data-action="open-drawer"]']) {
      const el = d.querySelector(trigger);
      if (el) { el.click(); await settled(dom); }
    }

    for (const row of pairings(d, page)) {
      const key = row.arbitrary ? `arbitrary:${row.arbitrary}` : `${row.ink}|${row.ground}|${row.need}`;
      if (!all.has(key)) all.set(key, row);
    }
    dom.window.close();
  }
  return [...all.values()].sort((a, b) =>
    (a.need - b.need) || a.fg.localeCompare(b.fg) || a.bg.localeCompare(b.bg));
}

function renderTable(rows) {
  const lines = [
    '| Foreground | Background | Resolved | Ratio | Needs | Where |',
    '|---|---|---|---|---|---|',
  ];
  for (const r of rows) {
    lines.push(`| \`${r.fg}\` | \`${r.bg}\` | ${r.ink} on ${r.ground} | **${r.ratio.toFixed(2)}:1** | `
      + `${r.need.toFixed(1)}:1 | ${r.what} |`);
  }
  return lines.join('\n');
}

module.exports = { auditRows, renderTable };
