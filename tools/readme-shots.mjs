/**
 * Regenerates the four screenshots the README embeds, from the running application.
 *
 * `web/tools/shots.mjs` photographs the page shells from `file://` with no server, which
 * is the criterion #10 and #11 were checked against. This is the other half: the live
 * application, with a real pipeline behind it and a real run in front of it — the search
 * screen caught mid-flight, the portfolio screen with data in it, a project sheet open.
 *
 * The run is pinned to a **seed**, so the portfolio in the README is the same portfolio
 * `terrafolio run --seed 3` prints. Re-running this script against the same pipeline
 * reproduces the images rather than producing new ones, which is the point.
 *
 * Chrome's own remote-debugging protocol does the work over node's built-in WebSocket, so
 * this adds no dependency — no puppeteer, no browser download. If Chrome is not installed
 * the script says so and exits 0: screenshots are a convenience and must not fail a build.
 *
 * Run:  uv run terrafolio serve --port 8123      (in another shell)
 *       node tools/readme-shots.mjs [--base URL] [--seed N] [--out DIR]
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const CHROMES = [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
];

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const BASE = arg('base', 'http://127.0.0.1:8123').replace(/\/$/, '');
const SEED = Number(arg('seed', 3));
const OUT = path.resolve(ROOT, arg('out', 'docs/screenshots'));
const PORT = 9333;

/** The default mandate, as `web/js/mandate.js` seeds it. Screenshot 01 shows these values. */
const MANDATE = {
  availableCapital_m: 1200, capacityTargetMw: 1500, solarShare: 0.45, targetIrr: 0.11,
  holdYears: 10,
  countries: ['DE', 'DK', 'ES', 'FI', 'FR', 'GR', 'IE', 'IT', 'NL', 'PL', 'PT', 'RO', 'SE', 'UK'],
  stages: ['construction', 'greenfield', 'ready_to_build'],
  minLeverage: 0.6, minDscr: 1.25,
  maxMerchantShare: 0.35, maxCountryShare: 0.35, maxProjectShare: 0.15,
  codFrom: 2027, codTo: 2032, riskAppetite: 'balanced',
  gridSecuredOnly: false, eurRevenueOnly: false, omContractedOnly: false,
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------
// The smallest CDP client that can drive a page and photograph it
// ---------------------------------------------------------------------------

class Chrome {
  constructor(proc, ws) {
    this.proc = proc;
    this.ws = ws;
    this.nextId = 0;
    this.pending = new Map();
    this.session = null;
    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      const waiter = this.pending.get(message.id);
      if (!waiter) return;
      this.pending.delete(message.id);
      message.error ? waiter.reject(new Error(JSON.stringify(message.error))) : waiter.resolve(message.result);
    };
  }

  static async launch(binary) {
    const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'terrafolio-shots-'));
    const proc = spawn(binary, [
      '--headless=new',
      `--remote-debugging-port=${PORT}`,
      `--user-data-dir=${profile}`,
      '--no-first-run', '--no-default-browser-check',
      '--disable-gpu', '--hide-scrollbars', '--force-color-profile=srgb',
      'about:blank',
    ], { stdio: ['ignore', 'ignore', 'ignore'] });

    let version = null;
    for (let i = 0; i < 100 && !version; i++) {
      try {
        version = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
      } catch { await sleep(100); }
    }
    if (!version) throw new Error('Chrome did not expose a debugging endpoint');

    const socket = new WebSocket(version.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });

    const chrome = new Chrome(proc, socket);
    const { targetId } = await chrome.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await chrome.send('Target.attachToTarget', { targetId, flatten: true });
    chrome.session = sessionId;
    await chrome.send('Page.enable');
    await chrome.send('Runtime.enable');
    return chrome;
  }

  send(method, params = {}) {
    const id = ++this.nextId;
    const frame = { id, method, params };
    if (this.session && method !== 'Target.createTarget' && method !== 'Target.attachToTarget') {
      frame.sessionId = this.session;
    }
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify(frame));
    });
  }

  viewport(width, height) {
    // deviceScaleFactor 2: the README is read on retina displays as often as not.
    return this.send('Emulation.setDeviceMetricsOverride', {
      width, height, deviceScaleFactor: 2, mobile: false,
    });
  }

  async goto(url, settle = 1500) {
    await this.send('Page.navigate', { url });
    await sleep(settle);
  }

  async evaluate(expression) {
    const result = await this.send('Runtime.evaluate', { expression, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? 'evaluate failed');
    return result.result.value;
  }

  /** A dispatched mouse event, not `element.click()`: the tables listen through a delegate. */
  async click(x, y) {
    for (const type of ['mousePressed', 'mouseReleased']) {
      await this.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 });
    }
  }

  async shot(name) {
    const { data } = await this.send('Page.captureScreenshot', { format: 'png' });
    const file = path.join(OUT, name);
    fs.mkdirSync(OUT, { recursive: true });
    fs.writeFileSync(file, Buffer.from(data, 'base64'));
    console.log(`${name.padEnd(18)} ${String(fs.statSync(file).size).padStart(7)} bytes`);
  }
}

// ---------------------------------------------------------------------------

const chromeBinary = CHROMES.find((candidate) => fs.existsSync(candidate));
if (!chromeBinary) {
  console.log('No Chrome, Chromium or Edge found; skipping screenshots.');
  console.log('Tried:\n  ' + CHROMES.join('\n  '));
  process.exit(0);
}

let pipeline;
try {
  pipeline = await (await fetch(`${BASE}/pipeline/status`)).json();
} catch {
  console.error(`Nothing is serving ${BASE}. Start it with: uv run terrafolio serve --port 8123`);
  process.exit(1);
}
console.log(`${pipeline.loadedCount} files loaded · ${pipeline.pipelineHash.slice(0, 20)}…`);

const created = await (await fetch(`${BASE}/optimisations`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ mandate: MANDATE, effort: 'standard', seed: SEED }),
})).json();
const runId = created.runId;
for (let i = 0; i < 100; i++) {
  const run = await (await fetch(`${BASE}/optimisations/${runId}`)).json();
  if (run.status !== 'running') break;
  await sleep(100);
}
console.log(`run ${runId} · seed ${SEED}`);

const chrome = await Chrome.launch(chromeBinary);

// The search screen redirects to the portfolio the moment it settles. Neutering
// `assign` lets it finish drawing so it can be photographed part-way through.
await chrome.send('Page.addScriptToEvaluateOnNewDocument', {
  source: `if (location.pathname.indexOf('search') !== -1) {
    Object.defineProperty(window.location, 'assign', { value: () => { window.__settled = true; }, writable: true });
  }`,
});

// ---- 01 · the mandate, with its feasibility footer -------------------------
await chrome.viewport(1440, 1060);
await chrome.goto(`${BASE}/mandate.html`, 1800);
await chrome.shot('01-mandate.png');

// ---- 02 · the search, caught about two thirds of the way through ----------
await chrome.viewport(1180, 660);
await chrome.goto(`${BASE}/search.html?run=${runId}`, 200);
for (let i = 0; i < 400; i++) {
  const state = await chrome.evaluate(`(() => {
    const el = document.querySelector('[data-region="round-counter"]');
    const m = el && el.textContent.match(/(\\d+)\\s*\\/\\s*(\\d+)/);
    return { round: m ? +m[1] : 0, total: m ? +m[2] : 0, settled: !!window.__settled };
  })()`);
  if (state.settled || (state.total && state.round / state.total >= 0.62)) break;
  await sleep(30);
}
await chrome.shot('02-search.png');

// ---- 03 · the portfolio: tiles, cash flow and map -------------------------
await chrome.viewport(1440, 880);
await chrome.goto(`${BASE}/portfolio.html?run=${runId}`, 2500);
await chrome.shot('03-portfolio.png');

// ---- 04 · the holdings table with a project sheet open --------------------
await chrome.viewport(1440, 1010);
// The chart's screen-reader table comes first in the document; this is the other one.
const HOLDINGS = `[...document.querySelectorAll('table')].find(t => !t.classList.contains('sr-only'))`;
for (let i = 0; i < 60; i++) {
  if (await chrome.evaluate(`${HOLDINGS}.querySelectorAll('tbody tr').length`) > 3) break;
  await sleep(200);
}
await chrome.evaluate(`(() => {
  // Room to scroll past the end, so the holdings card can sit at the top of the frame.
  document.body.style.paddingBottom = '700px';
  const head = ${HOLDINGS}.querySelector('thead');
  window.scrollTo(0, head.getBoundingClientRect().top + window.scrollY - 182);
  return true;
})()`);
await sleep(600);
const row = await chrome.evaluate(`(() => {
  const cell = ${HOLDINGS}.querySelectorAll('tbody tr')[1].querySelectorAll('td')[1];
  const box = cell.getBoundingClientRect();
  return { x: box.left + 60, y: box.top + box.height / 2, name: cell.innerText.split('\\n')[0] };
})()`);
await chrome.click(row.x, row.y);
await sleep(1400);
await chrome.shot('04-holdings.png');
console.log(`project sheet: ${row.name}`);

chrome.proc.kill();
process.exit(0);
