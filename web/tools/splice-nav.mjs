/**
 * Writes the shared sticky-nav block into every page, so the four copies cannot
 * drift apart by hand. Authoring-time only — it is not part of `npm run build`,
 * and the pages it writes are ordinary static HTML that #10 and #11 edit directly.
 *
 * Each page marks its place in the stepper with a line
 *   <!--NAV:mandate-->  (or search, portfolio, or none for the styleguide)
 * and this replaces that line with the block, adding aria-current="step" to the
 * matching step. aria-current drives both the announcement and, through an
 * aria-[current=step] variant in the markup, the colour — so the two cannot disagree.
 *
 * Run: node tools/splice-nav.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const NAV = fs.readFileSync(path.join(WEB, 'tools', 'nav.part.html'), 'utf8').replace(/\n$/, '');

for (const file of fs.readdirSync(WEB).filter((f) => f.endsWith('.html'))) {
  const full = path.join(WEB, file);
  const source = fs.readFileSync(full, 'utf8');
  const marker = source.match(/^[ \t]*<!--NAV:([a-z]+)-->[ \t]*$/m);
  if (!marker) continue;

  const step = marker[1];
  const block = step === 'none'
    ? NAV
    : NAV.replace(`data-step="${step}" class=`, `data-step="${step}" aria-current="step" class=`);

  fs.writeFileSync(full, source.replace(marker[0], block));
  console.log(`${file.padEnd(16)} nav spliced (step: ${step})`);
}
