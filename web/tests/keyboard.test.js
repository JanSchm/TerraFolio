/**
 * "Every control is fully keyboard-operable."
 *
 * jsdom cannot press Tab, so this asserts the properties that make tab order work:
 * every control is a native focusable element, has an accessible name, and does not
 * jump the order with a positive tabindex. That covers the failure mode this issue
 * exists to avoid — the reference mockup built its controls out of divs, which take
 * no focus at all — and it is checked on the rendered DOM, after Alpine has run.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { PAGES, loadPage } = require('./helpers/page.js');

const NATIVE = ['INPUT', 'BUTTON', 'SELECT', 'TEXTAREA', 'A'];

/** The accessible name, by the parts of the accname algorithm that matter here. */
function accessibleName(el, doc) {
  const label = el.getAttribute('aria-label');
  if (label && label.trim()) return label.trim();

  const labelledBy = el.getAttribute('aria-labelledby');
  if (labelledBy) {
    const text = labelledBy.split(/\s+/)
      .map((id) => doc.getElementById(id)?.textContent ?? '').join(' ').trim();
    if (text) return text;
  }

  if (el.id) {
    const forLabel = doc.querySelector(`label[for="${el.id}"]`);
    if (forLabel?.textContent.trim()) return forLabel.textContent.trim();
  }

  const wrapping = el.closest('label');
  if (wrapping?.textContent.trim()) return wrapping.textContent.trim();

  if (el.tagName === 'BUTTON' || el.tagName === 'A') {
    if (el.textContent.trim()) return el.textContent.trim();
    const title = el.getAttribute('title');
    if (title?.trim()) return title.trim();
  }

  if (el.tagName === 'INPUT' && el.type === 'search' && el.getAttribute('placeholder')) {
    return el.getAttribute('placeholder');
  }

  const fieldset = el.closest('fieldset');
  const legend = fieldset?.querySelector('legend');
  if (legend?.textContent.trim()) return legend.textContent.trim();

  return '';
}

for (const page of PAGES) {
  test(`${page}: every control is a native focusable element`, async () => {
    const dom = await loadPage(page);
    const d = dom.window.document;

    const interactive = [...d.querySelectorAll(
      '[role="switch"], [role="button"], [role="checkbox"], [role="radio"], [role="slider"], .chip')];
    for (const el of interactive) {
      assert.ok(NATIVE.includes(el.tagName),
        `${el.getAttribute('role') || el.className} is a <${el.tagName.toLowerCase()}>; ` +
        'controls must be built on a native element so they take focus');
    }
    dom.window.close();
  });

  test(`${page}: every focusable control has an accessible name`, async () => {
    const dom = await loadPage(page);
    const d = dom.window.document;
    const controls = [...d.querySelectorAll(
      'input:not([type=hidden]), button, select, textarea, a[href]')]
      .filter((el) => !el.disabled && el.getAttribute('aria-hidden') !== 'true');

    // A page may legitimately have few controls; what matters is that each is named.
    const nameless = controls
      .filter((el) => !accessibleName(el, d))
      .map((el) => el.outerHTML.replace(/\s+/g, ' ').slice(0, 100));
    assert.deepEqual(nameless, [], 'a control with no name is unusable by a screen reader');
    dom.window.close();
  });

  test(`${page}: no control jumps the tab order with a positive tabindex`, async () => {
    const dom = await loadPage(page);
    const offenders = [...dom.window.document.querySelectorAll('[tabindex]')]
      .filter((el) => Number(el.getAttribute('tabindex')) > 0)
      .map((el) => el.outerHTML.slice(0, 80));
    assert.deepEqual(offenders, [],
      'a positive tabindex detaches an element from document order and breaks Tab');
    dom.window.close();
  });

  test(`${page}: sliders announce their value as text, not as a raw number`, async () => {
    const dom = await loadPage(page);
    const ranges = [...dom.window.document.querySelectorAll('input[type=range]')];
    for (const range of ranges) {
      const text = range.getAttribute('aria-valuetext');
      assert.ok(text && text.trim(),
        'a range must carry aria-valuetext so a reader hears "€1,200m", not "1200"');
    }
    dom.window.close();
  });

  test(`${page}: every radio group is grouped and named`, async () => {
    const dom = await loadPage(page);
    const d = dom.window.document;
    for (const radio of d.querySelectorAll('input[type=radio]')) {
      assert.ok(radio.getAttribute('name'),
        'radios need a shared name, or arrow keys will not move between them');
      assert.ok(radio.closest('fieldset'),
        'a radio group needs a fieldset and legend so the group itself is announced');
    }
    dom.window.close();
  });
}
