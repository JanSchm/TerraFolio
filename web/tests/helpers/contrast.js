/**
 * Resolves the colour pairings a page actually renders, and their WCAG ratios.
 *
 * tests/contrast.test.js checks a hand-written list of twenty-eight role pairs. This
 * answers a different question — not "is every role the system defines legible" but
 * "is every pair the markup actually produces legible" — and the two catch different
 * mistakes. A role nobody uses cannot fail in front of a user; a pair nobody wrote
 * down can.
 *
 * Foreground comes from the nearest `text-{token}` on the element or an ancestor,
 * background from the nearest `bg-{token}` ancestor that is not transparent. `<body>`
 * carries both, so every walk terminates.
 */
const config = require('../../tailwind.config.js');

const C = config.theme.colors;

/* ── Colour ───────────────────────────────────────────────────────────────── */

function channels(hex) {
  const h = hex.replace('#', '');
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}

function hex(rgb) {
  return `#${rgb.map((c) => Math.round(c).toString(16).padStart(2, '0')).join('')}`;
}

/** `rgb(29 31 32 / 0.16)` — the one token that is not a hex triple. */
function parse(value) {
  if (value.startsWith('#')) return { rgb: channels(value), alpha: 1 };
  const m = value.match(/rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)(?:\s*[/,]\s*([\d.]+))?\s*\)/);
  if (!m) return null;
  return { rgb: [+m[1], +m[2], +m[3]], alpha: m[4] === undefined ? 1 : parseFloat(m[4]) };
}

/** Anything translucent has to be resolved against what is behind it. */
function over(fg, bg) {
  if (fg.alpha >= 1) return hex(fg.rgb);
  const ground = parse(bg);
  return hex(fg.rgb.map((c, i) => c * fg.alpha + ground.rgb[i] * (1 - fg.alpha)));
}

function relativeLuminance(value) {
  const [r, g, b] = channels(value).map((c) => {
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

/* ── Tailwind class → token ───────────────────────────────────────────────── */

/** `accent-700` → the hex on the ladder; `muted` → the alias' hex. */
function token(name) {
  const [head, step] = name.split('-');
  if (step !== undefined && C[head] && typeof C[head] === 'object') return C[head][step] || null;
  return typeof C[name] === 'string' ? C[name] : null;
}

/**
 * `text-accent-700`, `bg-text/35`, `border-divider`. Returns the resolved value and
 * the token's name, or null when the class is not a colour (`text-h2`, `bg-cover`).
 * An arbitrary value is reported rather than skipped: a hard-coded colour is exactly
 * what this audit exists to catch.
 */
function colourClass(cls, prefix) {
  if (!cls.startsWith(`${prefix}-`)) return null;
  const rest = cls.slice(prefix.length + 1);
  /* An arbitrary value is only this audit's business when it is a colour:
     `text-[19px]` is a font size and `w-[440px]` is a width. A hard-coded colour
     is reported rather than skipped, because it is exactly what this catches. */
  if (rest.startsWith('[')) {
    return /^\[(#|rgba?\(|hsla?\(|color\()/.test(rest) ? { arbitrary: rest } : null;
  }
  const [name, opacity] = rest.split('/');
  const value = token(name);
  if (!value) return null;
  const parsed = parse(value);
  if (!parsed) return null;
  if (opacity) parsed.alpha = parseInt(opacity, 10) / 100;
  return { name, parsed };
}

/** The nearest ancestor (self first) carrying a colour in this prefix. */
function inherited(el, prefix, doc) {
  for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
    for (const cls of node.classList) {
      const found = colourClass(cls, prefix);
      if (!found) continue;
      if (found.arbitrary) return { ...found, on: node };
      if (prefix === 'bg' && found.name === 'transparent') continue;
      return { ...found, on: node };
    }
  }
  return null;
}

/* ── Walking a page ───────────────────────────────────────────────────────── */

/**
 * Not rendered text. Compared case-insensitively because an SVG element reports a
 * lowercase `tagName` while an HTML one reports uppercase — so an SVG `<title>`,
 * which is a tooltip and not visible text, was being audited as if it were.
 */
const SKIP = new Set(['SCRIPT', 'STYLE', 'TEMPLATE', 'TITLE', 'DESC']);
const skipped = (el) => SKIP.has(el.tagName.toUpperCase());

function isHidden(el) {
  return !!el.closest('[hidden], .sr-only');
}

/**
 * WCAG 1.4.3 exempts pure decoration. An `aria-hidden` element is hidden from
 * assistive technology, which on these pages means one of two things: a separator
 * or a rule that carries nothing (the stepper's em dashes, a legend swatch), or a
 * status mark that is essential to a sighted reader. The first is decoration; the
 * second is not, and is held at 4.5:1 by its own assertion rather than being
 * excluded here with everything else.
 */
function isDecoration(el) {
  return !!el.closest('[aria-hidden="true"]');
}

/** The element's own border colour — an ancestor's frame is not this control's. */
function ownBorder(el) {
  for (const cls of el.classList) {
    const found = colourClass(cls, 'border');
    if (found) return { ...found, on: el };
  }
  return null;
}

/**
 * Every (foreground, background) pair with visible text on it, plus the border of
 * every enabled control, which WCAG 1.4.11 holds at 3:1.
 */
function pairings(document, page) {
  const found = new Map();
  const add = (fg, bg, need, what, el) => {
    if (!fg || !bg) return;
    if (fg.arbitrary || bg.arbitrary) {
      found.set(`arbitrary:${fg.arbitrary || bg.arbitrary}`, {
        arbitrary: fg.arbitrary || bg.arbitrary, what, page,
        where: el.outerHTML.replace(/\s+/g, ' ').slice(0, 80),
      });
      return;
    }
    const ground = over(bg.parsed, C.bg);
    const ink = over(fg.parsed, ground);
    const key = `${ink}|${ground}|${need}`;
    if (!found.has(key)) {
      found.set(key, { fg: fg.name, bg: bg.name, ink, ground, need, what, page,
        ratio: ratio(ink, ground) });
    }
  };

  for (const el of document.querySelectorAll('*')) {
    if (skipped(el) || isHidden(el)) continue;

    const ownText = [...el.childNodes]
      .some((n) => n.nodeType === 3 && n.nodeValue.trim().length > 0);
    if (ownText && !isDecoration(el)) {
      add(inherited(el, 'text', document) || { name: 'text', parsed: parse(C.text) },
        inherited(el, 'bg', document) || { name: 'bg', parsed: parse(C.bg) },
        4.5, 'text', el);
    }

    /* A control's own boundary, against what is behind the control — not against
       its own fill, which would compare a button's border to the button. Disabled
       controls are exempt from 1.4.11. */
    const control = el.matches('input, select, textarea, button, [role="switch"]');
    if (control && !el.disabled) {
      const border = ownBorder(el);
      if (border) {
        const behind = el.parentElement
          ? inherited(el.parentElement, 'bg', document)
          : null;
        add(border, behind || { name: 'bg', parsed: parse(C.bg) }, 3, 'control boundary', el);
      }
    }
  }
  return [...found.values()];
}

module.exports = { ratio, over, parse, token, colourClass, inherited, ownBorder, isDecoration, isHidden, pairings, C };
