/**
 * Prints the contrast audit table for docs/decisions.md.
 *
 * A tool writes it, a test asserts re-running is a no-op — the same arrangement
 * tools/splice-nav.mjs and tests/nav.test.js use for the nav block, so the table in
 * the document cannot drift from the markup it describes.
 *
 * Run: node tests/tools/contrast-audit.js
 */
const { auditRows, renderTable } = require('../helpers/audit.js');

auditRows().then((rows) => process.stdout.write(renderTable(rows) + '\n'));
