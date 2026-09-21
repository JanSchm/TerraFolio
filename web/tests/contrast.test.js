/**
 * WCAG contrast over the token/role table, computed from the tailwind config itself.
 *
 * This exists because jsdom has no layout engine, so axe cannot run color-contrast —
 * and because the mockup this design system is ported from fails AA in six places.
 * Asserting the ratios directly is stricter than an axe page scan would be: it covers
 * every pair the system defines, not only the pairs that reach a rendered page, so a
 * role that is introduced later cannot quietly pick a failing token.
 *
 * See docs/decisions.md D8 for the six failures and where each role moved.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const config = require('../tailwind.config.js');

const C = config.theme.colors;

function channels(hex) {
  const h = hex.replace('#', '');
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}

function relativeLuminance(hex) {
  const [r, g, b] = channels(hex).map((c) => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(fg, bg) {
  const a = relativeLuminance(fg);
  const b = relativeLuminance(bg);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

/** WCAG 2.1: 4.5:1 for body text, 3:1 for large text and for UI/graphic boundaries. */
const TEXT = 4.5;
const LARGE = 3.0;
const UI = 3.0;

/** [description, foreground, background, threshold] — every role the system defines. */
const PAIRS = [
  ['body text on the page', C.text, C.bg, TEXT],
  ['body text on a surface', C.text, C.surface, TEXT],
  ['body text on a de-emphasised row', C.text, C.neutral[200], TEXT],
  ['muted body and micro-labels on the page', C.muted, C.bg, TEXT],
  ['muted body on a de-emphasised row', C.muted, C.neutral[200], TEXT],
  ['muted sub-label on the highlight tile', C.muted, C.highlight, TEXT],
  ['active data ink on the page', C.ink, C.bg, TEXT],
  ['active data ink on a surface', C.ink, C.surface, TEXT],
  ['active data ink on the highlight tile', C.ink, C.highlight, TEXT],
  ['breach text on the page', C.breach, C.bg, TEXT],
  ['breach text on the locked row', C.breach, C.highlight, TEXT],
  ['breach text on a de-emphasised row', C.breach, C.neutral[200], TEXT],
  ['primary button label on its fill', C.bg, C.accent[700], TEXT],
  ['primary button label on its hover fill', C.bg, C.accent[800], TEXT],
  ['SOLAR inset label on the solar fill', C.bg, C.solar, TEXT],
  ['WIND inset label on the wind fill', C.accent[900], C.wind, TEXT],
  ['segmented selected label on its fill', C.bg, C.accent[700], TEXT],
  ['chip label, unpressed', C.ink, C.bg, TEXT],
  ['chip label, pressed', C.accent[800], C.accent[100], TEXT],
  ['field label on the page', C.neutral[800], C.bg, TEXT],
  ['input text on the field fill', C.text, C.neutral[100], TEXT],

  ['control boundary on the page', C.edge, C.bg, UI],
  ['control boundary on the field fill', C.edge, C.neutral[100], UI],
  ['focus ring on the page', C.ink, C.bg, UI],
  ['focus ring on a surface', C.ink, C.surface, UI],
  ['switch knob, off', C.neutral[600], C.bg, UI],
  ['positive cash-flow bar on the page', C.accent[700], C.bg, UI],
  ['hairline around the wind fill', C.accent[700], C.bg, UI],
  ['hairline around a negative bar', C.accent[700], C.bg, UI],
];

for (const [what, fg, bg, need] of PAIRS) {
  test(`contrast: ${what} — ${fg} on ${bg}`, () => {
    const r = ratio(fg, bg);
    assert.ok(r >= need,
      `${r.toFixed(2)}:1 is below the ${need}:1 floor. Move the role to a darker token ` +
      'rather than inventing a colour — the ladder is fixed by issue #5.');
  });
}

test('the bare accent the mockup used as text is not in the ported palette', () => {
  // #5980a6 scores 3.71:1 on the page background. Every role that used it moved to
  // accent-700 (5.78:1). If it comes back, these ratios stop meaning anything.
  const flat = JSON.stringify(config.theme.colors).toLowerCase();
  assert.ok(!flat.includes('#5980a6'),
    'bare --color-accent fails AA as text; use accent-700 (see docs/decisions.md D8)');
  assert.ok(!flat.includes('#728fab'), 'the accent-2 ladder is not part of this system');
});

test('every semantic alias resolves to a token on the ladder, not a new colour', () => {
  const ladder = new Set([
    ...Object.values(C.accent), ...Object.values(C.neutral), C.bg, C.surface, C.text,
  ]);
  for (const name of ['ink', 'solar', 'wind', 'breach', 'highlight', 'muted', 'edge']) {
    assert.ok(ladder.has(C[name]),
      `${name} (${C[name]}) must be a step on the ladder, so the ladder stays the source of truth`);
  }
});
