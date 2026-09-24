/**
 * charts.js — the two hand-built charts, as geometry rather than as drawing.
 *
 * ui-contract.md §6 says four charts, none from a library. Two of them are here
 * because they are the two that need arithmetic: the search screen's score curves
 * and the portfolio's thirty-bar cash-flow column chart. The technology split bar is
 * a control and lives in controls.js; the map is a projection and lives in map.js.
 *
 * Every function here is pure — it takes numbers and returns numbers or strings, and
 * touches no DOM. That is what lets the shapes be checked at their edges (one round,
 * a flat series, an all-negative series) without a page, and it keeps the callers
 * free to render into whatever markup #5 and #10 left them.
 *
 * Classic script in the browser, CommonJS under node. See docs/decisions.md A-15.
 */
(function (root) {
  'use strict';

  /* ── The search screen's two curves (ui-contract.md §4) ──────────────────────
     A 600 x 150 user space with preserveAspectRatio="none": the box stretches to
     the panel and the strokes do not, so there is no aspect arithmetic to do. */

  var VIEW_WIDTH = 600;
  var VIEW_HEIGHT = 150;

  /**
   * Inset, in user units, so a stroke sitting on the extreme of its range is drawn
   * rather than half-clipped by the edge of the box. The best series spends most of
   * a run at its own maximum, which is exactly the line this protects.
   */
  var INSET = 4;

  /**
   * Where round `n` sits horizontally.
   *
   * Scaled to the **total** rather than to how many rounds have arrived, so the
   * curve grows left to right across a fixed axis instead of rescaling under itself
   * on every frame — which reads as the whole history moving rather than as progress.
   */
  function x(round, total) {
    if (total <= 1) return 0;
    return ((round - 1) / (total - 1)) * VIEW_WIDTH;
  }

  /**
   * Where a score sits vertically, within a domain covering **both** series.
   *
   * One domain for the pair, because the point of the chart is the gap between them
   * closing; scaling each to its own range would hold that gap constant and show
   * nothing. A degenerate domain — one round, or a series that has not moved — draws
   * down the middle rather than dividing by zero.
   */
  function y(value, low, high) {
    var span = high - low;
    if (!isFinite(span) || span <= 0) return VIEW_HEIGHT / 2;
    var usable = VIEW_HEIGHT - INSET * 2;
    return VIEW_HEIGHT - INSET - ((value - low) / span) * usable;
  }

  function finite(value) {
    return typeof value === 'number' && isFinite(value);
  }

  /**
   * The `points` attribute for each series, from the rounds that have arrived.
   *
   * `rounds` are the stream's own frames in arrival order; `total` is the run's fixed
   * length, known from the 202 before the first frame. A frame whose score is not a
   * number is skipped rather than drawn at zero — an undefined score is not a score
   * of nothing (epic §5).
   */
  function curves(rounds, total) {
    var points = rounds || [];
    var low = Infinity;
    var high = -Infinity;
    var i;
    for (i = 0; i < points.length; i++) {
      var pair = [points[i].bestFitness, points[i].meanFitness];
      for (var j = 0; j < 2; j++) {
        if (!finite(pair[j])) continue;
        if (pair[j] < low) low = pair[j];
        if (pair[j] > high) high = pair[j];
      }
    }
    return {
      best: series(points, total, low, high, 'bestFitness'),
      mean: series(points, total, low, high, 'meanFitness'),
      low: low === Infinity ? null : low,
      high: high === -Infinity ? null : high,
    };
  }

  function series(points, total, low, high, key) {
    var out = [];
    for (var i = 0; i < points.length; i++) {
      var value = points[i][key];
      if (!finite(value)) continue;
      out.push(round2(x(points[i].generation, total)) + ',' + round2(y(value, low, high)));
    }
    return out.join(' ');
  }

  /** Two decimals of a user unit is a fiftieth of a pixel; more is only bytes. */
  function round2(value) {
    return Math.round(value * 100) / 100;
  }

  /* ── The portfolio's thirty-bar cash-flow chart (ui-contract.md §5.2) ─────────
     Flex columns sized in per cent, with the zero line positioned so the same
     percentage means the same amount above it and below. */

  /**
   * The geometry of one cash-flow series: where the zero line sits, and how tall
   * each bar is as a share of the plot.
   *
   * The zero line is at `max / (max - min)` of the height from the top, which is
   * what makes a bar of -50 exactly as long as a bar of +50. A series that never
   * goes negative puts the line on the floor and a series that never goes positive
   * puts it on the ceiling, both without a special case.
   */
  function bars(values, baseYear) {
    var series = values || [];
    /* A year with no number is **absent**, not a year in which the portfolio
       returned nothing. Coercing it to zero would draw a bar on the line and read
       "2031: €0m" aloud, which is a claim; epic §5 gives undefined one
       representation and it is not zero. `curves` skips such a point for the same
       reason, and `total` excludes it rather than dragging the caption's cumulative
       figure toward zero. */
    var known = series.filter(finite);
    var max = Math.max.apply(null, known.concat([0]));
    var min = Math.min.apply(null, known.concat([0]));
    var span = max - min;
    var zero = span > 0 ? (max / span) * 100 : 100;
    return {
      max: max,
      min: min,
      zeroPercent: zero,
      total: known.reduce(function (sum, v) { return sum + v; }, 0),
      /** False when any year is missing, so a caller can say so rather than imply it. */
      complete: known.length === series.length,
      bars: series.map(function (value, index) {
        var present = finite(value);
        var share = present && span > 0 ? (Math.abs(value) / span) * 100 : 0;
        return {
          /* Null when the base year is not known yet. `baseYear + index` would make
             a confident 0, 1, 2 … out of it, and a year label is a claim. */
          year: typeof baseYear === 'number' ? baseYear + index : null,
          value: present ? value : null,
          missing: !present,
          negative: present && value < 0,
          /** Per cent of the plot height, so the column CSS needs no pixel maths. */
          heightPercent: share,
        };
      }),
    };
  }

  /**
   * Which bars carry a year label. ui-contract.md §5.2 wants every fifth year,
   * centred under its bar, which is the first of each five rather than every index
   * divisible by five — the series starts at the base year, not at zero.
   */
  function ticks(count, baseYear, every) {
    var step = every || 5;
    var known = typeof baseYear === 'number';
    var out = [];
    for (var i = 0; i < count; i++) out.push(known && i % step === 0 ? baseYear + i : null);
    return out;
  }

  var charts = {
    VIEW_WIDTH: VIEW_WIDTH,
    VIEW_HEIGHT: VIEW_HEIGHT,
    INSET: INSET,
    x: x,
    y: y,
    curves: curves,
    bars: bars,
    ticks: ticks,
  };

  root.TerraFolio = root.TerraFolio || {};
  root.TerraFolio.charts = charts;

  if (typeof module === 'object' && module.exports) module.exports = charts;
})(typeof globalThis !== 'undefined' ? globalThis : this);
