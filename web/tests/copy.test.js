/**
 * Epic §5 and §12: no genetic-algorithm vocabulary in user-facing copy.
 *
 * The search screen is where this leaks, because the thing it is showing really is
 * a GA and the reference mockup names it as one — "Population of 90 candidate
 * portfolios, tournament selection, uniform crossover, 2.5% mutation", "GENERATION
 * 07 / 60", "Best fitness". An investment professional watching their mandate run
 * should be told what is happening to their portfolios, not to a chromosome.
 *
 * Checked against rendered text, so it covers copy that arrives through a binding.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { PAGES, loadPage } = require('./helpers/page.js');

/** Terms of art from the algorithm, not from the domain. */
const GA_VOCABULARY = [
  'genetic algorithm', 'chromosome', 'genome', 'gene ', 'allele',
  'crossover', 'mutation', 'mutate', 'tournament selection', 'elitism', 'elite',
  'fitness', 'generation', 'population', 'seeding population', 'converged',
  // Added by issue #10: ui-contract.md §8 bans twelve terms, and these four were
  // missing from the list above. 'solution space' was live in styleguide.html.
  'solution space', 'objective function', 'convergence', 'tournament',
];

/**
 * Domain words that merely look like GA vocabulary. "Generation" is what a wind
 * farm does; P50 generation in GWh is the single most common figure in the product.
 */
const DOMAIN_EXCEPTIONS = [
  'p50 generation', 'annual generation', 'generation in gwh', 'generation, gwh',
  'gwh/y', 'power generation',
];

for (const page of PAGES) {
  test(`${page}: user-facing copy carries no GA vocabulary`, async () => {
    const dom = await loadPage(page);
    let text = dom.window.document.body.textContent.toLowerCase().replace(/\s+/g, ' ');
    dom.window.close();

    for (const allowed of DOMAIN_EXCEPTIONS) text = text.split(allowed).join(' ');

    const found = GA_VOCABULARY.filter((term) => text.includes(term));
    assert.deepEqual(found, [],
      `${page} shows algorithm vocabulary to the user: ${found.join(', ')}. ` +
      'Describe what is happening to their portfolios instead.');
  });
}

test('the style guide is checked too, since it is where patterns get copied from', () => {
  assert.ok(PAGES.includes('styleguide.html'));
});
