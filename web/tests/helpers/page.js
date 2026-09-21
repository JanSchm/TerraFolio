/**
 * Loads a real page into jsdom with its own scripts running, so tests see the DOM a
 * user gets rather than the authored markup. Alpine expands its templates and resolves
 * its bindings under jsdom, which matters: without it, axe would audit
 * `<template x-for>` and `:aria-checked` rather than the rendered controls.
 */
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const WEB = path.resolve(__dirname, '..', '..');

/** The four pages this issue owns, in stepper order. */
const PAGES = ['mandate.html', 'search.html', 'portfolio.html', 'styleguide.html']
  .filter((p) => fs.existsSync(path.join(WEB, p)));

/** Resolves once Alpine has finished its first pass, or after a bail-out timeout. */
function alpineSettled(window) {
  return new Promise((resolve) => {
    const done = () => window.requestAnimationFrame(() => setTimeout(resolve, 60));
    if (window.Alpine) return done();
    window.document.addEventListener('alpine:initialized', done);
    setTimeout(resolve, 3000);
  });
}

async function loadPage(file) {
  const full = path.join(WEB, file);
  const dom = new JSDOM(fs.readFileSync(full, 'utf8'), {
    url: 'file://' + full,
    runScripts: 'dangerously',
    resources: 'usable',
    pretendToBeVisual: true,
  });
  await new Promise((resolve) => {
    if (dom.window.document.readyState === 'complete') resolve();
    else dom.window.addEventListener('load', resolve);
  });
  await alpineSettled(dom.window);
  return dom;
}

module.exports = { WEB, PAGES, loadPage };
