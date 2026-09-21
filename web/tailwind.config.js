/**
 * The "Industry" blueprint design system, ported from the mockup's :root token block
 * (Portfolio Optimiser (standalone).html, template lines 315-375).
 *
 * Two deliberate departures from that block, both recorded in docs/decisions.md:
 *
 *  - The bare `--color-accent: #5980a6` and the `--color-accent-2` ladder are NOT carried
 *    over. Bare accent scores 3.71:1 against the page background, so every role that used
 *    it for text or ink failed WCAG AA. Issue #5's semantic mapping already names accent-700
 *    for active data ink, which is 5.78:1, so the roles move and the ladder stays exact.
 *  - Spacing REPLACES Tailwind's default scale rather than extending it, so nothing off the
 *    3.4px blueprint grid is reachable from a utility class.
 */

/** 3.4px base: the system's named steps are 1, 2, 3, 4, 6 and 8. */
const step = (n) => `${+(n * 3.4).toFixed(2)}px`;
const spacing = { 0: '0px', px: '1px' };
for (let n = 1; n <= 24; n++) spacing[n] = step(n);
for (const half of [0.5, 1.5, 2.5, 3.5]) spacing[half] = step(half);

module.exports = {
  content: ['./*.html', './js/**/*.js'],
  theme: {
    spacing,

    /* Square corners everywhere. The 2/4/7px tokens exist because the system defines them,
       but DEFAULT is 0 so `rounded` cannot reintroduce a curve by accident. */
    borderRadius: {
      none: '0px',
      DEFAULT: '0px',
      sm: '2px',
      md: '4px',
      lg: '7px',
      full: '9999px',
    },

    /* Hairlines. 2px exists only for the focus ring. */
    borderWidth: { 0: '0px', DEFAULT: '1px', 2: '2px' },
    outlineWidth: { 0: '0px', 2: '2px' },
    outlineOffset: { 0: '0px', 2: '2px' },

    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      inherit: 'inherit',

      bg: '#f2f2f3',
      surface: '#e9e9ea',
      text: '#1d1f20',
      divider: 'rgb(29 31 32 / 0.16)',

      accent: {
        100: '#eef6ff',
        200: '#d6ebff',
        300: '#b5d9fd',
        400: '#94bce3',
        500: '#749dc4',
        600: '#597ea3',
        700: '#416180',
        800: '#2c455d',
        900: '#1d2d3d',
      },
      neutral: {
        100: '#f5f5f8',
        200: '#e7e7ea',
        300: '#d4d4d7',
        400: '#b7b7ba',
        500: '#98989b',
        600: '#7a7a7d',
        700: '#5d5d60',
        800: '#424244',
        900: '#2b2b2d',
      },

      /* Semantic aliases. Every one of these is a token above, not a new value, so the
         ladder stays the single source of truth and usage reads as intent.
         See docs/decisions.md for the contrast ratio behind each. */
      ink: '#416180',      // accent-700  active data ink, and solar
      solar: '#416180',    // accent-700
      wind: '#94bce3',     // accent-400
      breach: '#2c455d',   // accent-800  there is no red in this system
      highlight: '#eef6ff',// accent-100  highlight tile, locked row
      deemph: '#e7e7ea',   // neutral-200 de-emphasised row
      muted: '#5d5d60',    // neutral-700 muted body, micro-labels, table headers
      edge: '#7a7a7d',     // neutral-600 control boundaries, which need 3:1
    },

    fontFamily: {
      sans: ['Barlow', 'system-ui', 'sans-serif'],
      condensed: ['"Barlow Condensed"', 'system-ui', 'sans-serif'],
    },

    /* The mockup sizes everything inline and lands on half-pixel values. Naming them here
       keeps arbitrary values out of the markup. Sizes are the mockup's; only the roles moved. */
    fontSize: {
      'micro': ['11px', { lineHeight: '1.2', letterSpacing: '0.12em' }],
      'micro-lg': ['11.5px', { lineHeight: '1.2', letterSpacing: '0.12em' }],
      'tick': ['10.5px', { lineHeight: '1.2' }],
      'legend': ['12px', { lineHeight: '1.4' }],
      'note': ['13px', { lineHeight: '1.45' }],
      'meta': ['12.5px', { lineHeight: '1.45' }],
      'warn': ['13.5px', { lineHeight: '1.4' }],
      'label': ['14px', { lineHeight: '1.4' }],
      'body': ['15px', { lineHeight: '1.55' }],
      'body-lg': ['16px', { lineHeight: '1.55' }],
      'cell': ['14.5px', { lineHeight: '1.45' }],
      'seg': ['14.5px', { lineHeight: '1.2' }],
      'num-sm': ['15px', { lineHeight: '1.15' }],
      'num-cell': ['16.5px', { lineHeight: '1.2' }],
      'num': ['22px', { lineHeight: '1.15' }],
      'num-md': ['23px', { lineHeight: '1.15' }],
      'num-lg': ['24px', { lineHeight: '1.15' }],
      'num-xl': ['27px', { lineHeight: '1.1' }],
      'num-2xl': ['33px', { lineHeight: '1.1' }],
      'h6': ['13px', { lineHeight: '1.2', letterSpacing: '0.08em' }],
      'h5': ['15px', { lineHeight: '1.2', letterSpacing: '0.08em' }],
      'h4': ['20px', { lineHeight: '1.12', letterSpacing: '-0.015em' }],
      'h3': ['27px', { lineHeight: '1.12', letterSpacing: '-0.015em' }],
      'h2': ['36px', { lineHeight: '1.12', letterSpacing: '-0.015em' }],
      'h1': ['40px', { lineHeight: '1.12', letterSpacing: '-0.015em' }],
    },

    extend: {
      /* Per-page content widths. */
      maxWidth: {
        mandate: '1440px',
        search: '1000px',
        portfolio: '1560px',
        prose: '60ch',
      },
      /* The z-index ladder, named so nobody has to remember the numbers. */
      zIndex: {
        actionbar: '30',
        nav: '40',
        scrim: '60',
        drawer: '61',
        dialog: '70',
      },
      letterSpacing: {
        micro: '0.12em',
        brand: '0.14em',
        caps: '0.08em',
        wide: '0.1em',
        cta: '0.06em',
        tight: '0.01em',
      },
      keyframes: {
        spin360: { to: { transform: 'rotate(360deg)' } },
        blink: { '0%,100%': { opacity: '1' }, '50%': { opacity: '0.2' } },
      },
      animation: {
        spin360: 'spin360 0.9s linear infinite',
        blink: 'blink 1.6s ease-in-out infinite',
      },
    },
  },
  corePlugins: {
    /* Nothing in the system uses a shadow: panels are hairline frames, not cards. */
    boxShadow: false,
    boxShadowColor: false,
  },
  plugins: [],
};
