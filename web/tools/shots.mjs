/**
 * Renders every page from file:// in the installed Chrome and writes a PNG.
 *
 * This is how the "opens directly from disk with no server" and "styleguide renders
 * every control" criteria were checked, so it lives in the repo rather than in a
 * shell history: #10 auditing copy and #11 wiring data both need to see what a page
 * actually looks like, and neither should have to reconstruct the command.
 *
 * Chrome's own headless mode does the work, so this adds no dependency — no
 * puppeteer, no browser download. If Chrome is not installed the script says so and
 * exits 0, because a screenshot is a convenience and must not fail anyone's build.
 *
 * Run: npm run shots [-- --out DIR] [--width N] [--height N]
 */
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const CHROMES = [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
];

/** Each page at the width its own content column is designed for. */
const PAGES = [
  { file: 'mandate.html', width: 1440, height: 1000 },
  { file: 'search.html', width: 1100, height: 700 },
  { file: 'portfolio.html', width: 1560, height: 1100 },
  { file: 'styleguide.html', width: 1440, height: 2400 },
];

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const chrome = CHROMES.find((c) => fs.existsSync(c));
if (!chrome) {
  console.log('No Chrome, Chromium or Edge found; skipping screenshots.');
  console.log('Tried:\n  ' + CHROMES.join('\n  '));
  process.exit(0);
}

const outDir = path.resolve(WEB, arg('out', 'dist/shots'));
fs.mkdirSync(outDir, { recursive: true });

if (!fs.existsSync(path.join(WEB, 'dist', 'app.css'))) {
  console.error('dist/app.css is missing — run `npm run build` first, or the pages render unstyled.');
  process.exit(1);
}

for (const page of PAGES) {
  const source = path.join(WEB, page.file);
  if (!fs.existsSync(source)) continue;

  const out = path.join(outDir, page.file.replace(/\.html$/, '.png'));
  fs.rmSync(out, { force: true });

  /* Chrome writes the PNG and then sometimes lingers instead of exiting. The file is
     what we came for, so a timeout is only a failure if nothing was written. Passing
     --user-data-dir makes the lingering reliable rather than occasional, which is why
     this runs on the default profile. */
  try {
    execFileSync(chrome, [
    '--headless',
    '--disable-gpu',
    '--no-sandbox',
    '--hide-scrollbars',
    `--window-size=${arg('width', page.width)},${arg('height', page.height)}`,
    '--virtual-time-budget=5000',
    `--screenshot=${out}`,
    // file:// on purpose: this is the criterion being demonstrated.
    'file://' + source,
    ], { stdio: ['ignore', 'ignore', 'ignore'], timeout: 45_000 });
  } catch (error) {
    if (!fs.existsSync(out)) throw error;
  }

  if (!fs.existsSync(out)) {
    console.error(`${page.file}: Chrome produced no screenshot`);
    process.exitCode = 1;
    continue;
  }
  const bytes = fs.statSync(out).size;
  console.log(`${page.file.padEnd(16)} ${String(bytes).padStart(7)} bytes  ${path.relative(WEB, out)}`);
}

console.log(`\n${PAGES.length} page(s) rendered from file:// with no server.`);
