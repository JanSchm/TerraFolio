/**
 * Writes the shared sticky-nav block into every page, so the four copies cannot
 * drift apart by hand. Authoring-time only — it is not part of `npm run build`,
 * and the pages it writes are ordinary static HTML that #10 and #11 edit directly.
 *
 * Each page declares its place in the stepper with a marker line
 *   <!--NAV:mandate-->   (or search, portfolio, or none for the style guide)
 * and the generated block follows it. The marker is kept in the output, so this is
 * idempotent: run it again after editing nav.part.html and every page picks the
 * change up. Without that, the first run consumed its own marker and every later
 * run silently did nothing.
 *
 * aria-current drives both the announcement and, through an aria-[current=step]
 * variant in the markup, the colour — so the two cannot disagree.
 *
 * Run: node tools/splice-nav.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const NAV = fs.readFileSync(path.join(WEB, 'tools', 'nav.part.html'), 'utf8').replace(/\n$/, '');

const START = '<!-- nav:start';
const END = '<!-- nav:end -->';

/** The marker, plus any block a previous run already wrote after it. */
const REGION = /^([ \t]*)<!--NAV:([a-z]+)-->[ \t]*(?:\n[\s\S]*?<!-- nav:end -->)?/m;

let changed = 0;
for (const file of fs.readdirSync(WEB).filter((f) => f.endsWith('.html'))) {
  const full = path.join(WEB, file);
  const source = fs.readFileSync(full, 'utf8');

  const region = source.match(REGION);
  if (!region) {
    console.warn(`${file.padEnd(16)} no <!--NAV:step--> marker; skipped`);
    continue;
  }

  const [matched, indent, step] = region;
  const block = step === 'none'
    ? NAV
    : NAV.replace(`data-step="${step}" class=`, `data-step="${step}" aria-current="step" class=`);

  const next = source.replace(matched, `${indent}<!--NAV:${step}-->\n${block}`);
  if (next === source) {
    console.log(`${file.padEnd(16)} already current (step: ${step})`);
    continue;
  }
  fs.writeFileSync(full, next);
  changed += 1;
  console.log(`${file.padEnd(16)} nav written (step: ${step})`);
}

/** A sanity check, so a botched run cannot leave the pages half-written. */
for (const file of fs.readdirSync(WEB).filter((f) => f.endsWith('.html'))) {
  const source = fs.readFileSync(path.join(WEB, file), 'utf8');
  if (!source.includes(START) || !source.includes(END)) {
    throw new Error(`${file} lost its nav block`);
  }
}
console.log(`${changed} page(s) updated`);
