/**
 * The same pages, loaded as if a server had sent them.
 *
 * helpers/page.js loads each page from a `file://` URL, which is the right default:
 * it is how a committee pack opens and how decisions.md A-15 requires the pages to
 * work. But a `file://` document has an **opaque origin**, and both storage areas
 * throw `SecurityError` there — so nothing about spec §5's "every control persists
 * per user between sessions" can be observed on a page loaded that way.
 *
 * So this loads the real markup at an `http://` origin and runs the same scripts in
 * the same order, by reading each `<script src>` off the page rather than listing
 * them here — a page that gains a script gets it here too, and one that loses it
 * cannot leave a stale name behind.
 */
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const WEB = path.resolve(__dirname, '..', '..');

/** Resolves once Alpine has finished its first pass, matching helpers/page.js. */
function alpineSettled(window) {
  return new Promise((resolve) => {
    const done = () => window.requestAnimationFrame(() => setTimeout(resolve, 60));
    if (window.Alpine) return done();
    window.document.addEventListener('alpine:initialized', done);
    setTimeout(resolve, 3000);
  });
}

async function servePage(file, origin = 'http://127.0.0.1:8000/') {
  const html = fs.readFileSync(path.join(WEB, file), 'utf8');
  const dom = new JSDOM(html, {
    url: origin + file,
    runScripts: 'dangerously',
    pretendToBeVisual: true,
  });

  // `resources` is deliberately left off, so jsdom fetches nothing over http and the
  // scripts are supplied from disk in document order instead.
  for (const el of dom.window.document.querySelectorAll('script[src]')) {
    dom.window.eval(fs.readFileSync(path.join(WEB, el.getAttribute('src')), 'utf8'));
  }
  await alpineSettled(dom.window);
  return dom;
}

module.exports = { WEB, servePage };
