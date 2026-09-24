/**
 * search.js — screen 02, driven by the run's own event stream.
 *
 * spec §6 is explicit that the curves are "real per-generation data streamed from
 * the engine, not a simulated animation". They are: every point drawn here came off
 * `GET /optimisations/{id}/stream`. What this file adds is **pacing**, which is a
 * different thing from invention.
 *
 * It needs pacing because the event log is the stream's source of truth (api.md §7),
 * so a browser that POSTs and then subscribes is routinely handed the entire run at
 * once — a Standard search over the shipped pipeline finishes in under a tenth of a
 * second. Rendering that burst in one frame shows the user a finished chart and a
 * screen that vanishes before it can be read, which is the outcome §6's 1.5 s
 * minimum exists to prevent. So frames are buffered and replayed on a fixed tick.
 * No datum is invented, dropped or reordered; only the moment of drawing moves.
 *
 * Classic script in the browser, CommonJS under node. See docs/decisions.md A-15.
 */
(function (root) {
  'use strict';

  var load = (typeof require === 'function')
    ? function (name) { return require('./' + name + '.js'); }
    : function (name) { return root.TerraFolio && root.TerraFolio[name]; };

  var fmt = load('format');
  var api = load('api');
  var charts = load('charts');
  var store = load('mandate');

  /** spec §6: "If the run completes faster than 1.5 seconds, hold the screen." */
  var MINIMUM_MS = 1500;

  /** No faster than about one frame at 60 Hz; below that the tick is wasted work. */
  var FASTEST_TICK_MS = 16;

  /**
   * How long a dropped stream may keep trying before the screen says so.
   *
   * `EventSource` retries by itself and the log replays what was missed, so a brief
   * outage should pass unremarked; an id that resolves to nothing never will.
   */
  var RECONNECT_GRACE_MS = 5000;

  /**
   * ui-contract.md §4's status line, in thirds of the run. The strings are pinned
   * there verbatim — they are the replacements §14 requires for the mockup's copy,
   * which named the operators rather than what the user's portfolios are doing.
   */
  var NOTES = [
    'building the first candidates · checking them against the constraints',
    'recombining the best candidates · discarding those that breach a constraint',
    'settling on the best portfolio · computing levered returns',
  ];

  function note(round, total) {
    if (!total) return NOTES[0];
    var third = round / total;
    if (third < 1 / 3) return NOTES[0];
    if (third < 2 / 3) return NOTES[1];
    return NOTES[2];
  }

  /* ── The DOM ────────────────────────────────────────────────────────────── */

  function field(name) {
    return document.querySelector('[data-field="' + name + '"]');
  }

  function setField(name, text) {
    var node = field(name);
    if (node) node.textContent = text;
  }

  /** `ROUND 07 / 60` — §4 wants the counter zero-padded to two digits. */
  function counter(value) {
    var text = fmt.count(value);
    return text.length < 2 ? '0' + text : text;
  }

  /* ── The run ────────────────────────────────────────────────────────────── */

  /** Idempotent: `boot` calls it, and a second call would open a second stream. */
  var current = null;

  function start() {
    if (current) return current;
    if (!document.querySelector('[data-region="round-counter"]')) return null;

    var page = {
      runId: api ? api.runIdFromUrl() : null,
      total: 0,
      waiting: [],
      shown: [],
      startedAt: now(),
      stream: null,
      ticker: null,
      finished: null,
      stopped: false,
    };

    current = page;
    var cancel = document.querySelector('[data-action="cancel"]');
    if (cancel) {
      // Not preventDefault: the link goes back to the mandate, and this only makes
      // sure the page stops listening to a run it is walking away from.
      cancel.addEventListener('click', function () { stop(page); });
    }

    var held = store ? store.steering() : null;
    if (!page.runId && held) page.runId = held.runId;
    if (held && held.totalRounds) showTotal(page, held.totalRounds);
    if (!page.runId || !api || api.offline()) return page;

    // The total has to be known before the first frame: the announcer stays silent
    // until every figure in its region has a value, and the counter reads as one
    // string, so an unknown total keeps the whole region quiet (decisions 3B-3).
    if (!page.total) {
      api.getResult(page.runId).then(function (result) {
        showTotal(page, totalOf(result));
      }).catch(function (error) {
        /* A run id that resolves to nothing would otherwise spin for ever: the
           stream against a 404 reconnects on a timer and never says anything. */
        fail(page, { error: { message: error.message } });
      });
    }

    page.stream = api.openStream(page.runId, {
      round: function (frame) { arrive(page, frame); },
      done: function (frame) { page.finished = frame; settle(page); },
      failed: function (frame) { fail(page, frame); },
      dropped: function () { lost(page); },
    });
    return page;
  }

  /**
   * How many rounds this run has.
   *
   * A run still in flight says so directly; one that has already finished — which is
   * the common case, since a Standard search takes less time than opening this page —
   * comes back as a full record with no `totalGenerations` on it at all, and its
   * length is the trace it stored.
   */
  function totalOf(result) {
    if (!result) return 0;
    if (result.totalGenerations) return result.totalGenerations;
    return (result.convergence && result.convergence.length) || 0;
  }

  function now() {
    return (root.performance && root.performance.now) ? root.performance.now() : Date.now();
  }

  function showTotal(page, total) {
    if (!total || page.total === total) return;
    page.total = total;
    setField('roundTotal', counter(total));
    if (!page.ticker) begin(page);
  }

  /**
   * A frame off the wire. It is queued rather than drawn: the whole run may already
   * be in the log, and drawing it as it arrives would finish the chart instantly.
   */
  function arrive(page, frame) {
    if (!frame) return;
    showTotal(page, frame.totalGenerations);
    page.waiting.push(frame);
    begin(page);
  }

  function begin(page) {
    if (page.ticker || page.stopped || typeof root.setInterval !== 'function') return;
    var every = page.total > 0
      ? Math.max(FASTEST_TICK_MS, MINIMUM_MS / page.total)
      : FASTEST_TICK_MS;
    page.ticker = root.setInterval(function () { tick(page); }, every);
  }

  /**
   * One frame drawn per tick.
   *
   * When the engine is slower than the tick the queue is empty and this costs
   * nothing; when it is faster the queue drains at a readable rate. Either way the
   * frames are the engine's own, in its own order.
   */
  function tick(page) {
    if (page.waiting.length) {
      page.shown.push(page.waiting.shift());
      draw(page);
    }
    settle(page);
  }

  function draw(page) {
    var latest = page.shown[page.shown.length - 1];
    if (!latest) return;
    var best = latest.best || {};

    setField('round', counter(latest.generation));
    setField('mandateScore', fmt.mandateScore(latest.bestFitness));
    setField('bestCapacityMw', fmt.mw(best.capacityMw));
    setField('bestProjectCount', fmt.count(best.projectCount));
    setField('bestEquity_m', fmt.eurM(best.equity_m));
    setField('bestBlendedIrr', fmt.irr(best.blendedIrr));
    setField('progress-note', note(latest.generation, page.total));

    progress(latest.generation, page.total);
    plot(page);
  }

  function progress(round, total) {
    var bar = field('progress');
    var fill = field('progress-fill');
    var share = total > 0 ? Math.min(1, round / total) : 0;
    if (fill) fill.style.width = (share * 100) + '%';
    if (!bar) return;
    bar.setAttribute('aria-valuenow', String(Math.round(share * 100)));
    bar.setAttribute('aria-valuetext', 'Round ' + fmt.count(round) + ' of ' + fmt.count(total));
  }

  function plot(page) {
    var shape = charts.curves(page.shown, page.total || page.shown.length);
    var best = document.querySelector('[data-series="best"]');
    var mean = document.querySelector('[data-series="mean"]');
    if (best) best.setAttribute('points', shape.best);
    if (mean) mean.setAttribute('points', shape.mean);
  }

  /**
   * Move on, but not before the screen has been readable for §6's minimum and not
   * before every frame that arrived has been drawn.
   */
  function settle(page) {
    if (!page.finished || page.waiting.length || page.stopped) return;
    var remaining = MINIMUM_MS - (now() - page.startedAt);
    if (remaining > 0) {
      if (!page.holding) {
        page.holding = root.setTimeout(function () {
          page.holding = null;
          settle(page);
        }, remaining);
      }
      return;
    }
    stop(page);
    if (page.finished.status === 'succeeded') {
      root.location.assign('portfolio.html?run=' + encodeURIComponent(page.runId));
    }
  }

  function fail(page, frame) {
    stop(page);
    var error = (frame && frame.error) || {};
    setField('progress-note', error.message || 'The search did not complete.');
  }

  /**
   * The stream dropped and did not come back.
   *
   * `EventSource` reconnects on its own and the event log replays from
   * `Last-Event-ID`, so a blip needs no help. This only speaks once the connection
   * has failed outright, because the alternative is a screen that spins for ever
   * saying nothing.
   */
  function lost(page) {
    if (page.stopped || page.finished) return;
    if (page.lostAt === undefined) page.lostAt = now();
    if (now() - page.lostAt < RECONNECT_GRACE_MS) return;
    stop(page);
    setField('progress-note',
      'Lost contact with the optimiser. The run is still recorded; reopen it from the mandate.');
  }

  function stop(page) {
    page.stopped = true;
    if (page.ticker) { root.clearInterval(page.ticker); page.ticker = null; }
    if (page.holding) { root.clearTimeout(page.holding); page.holding = null; }
    if (page.stream) { page.stream.close(); page.stream = null; }
  }

  function boot() {
    if (typeof document === 'undefined') return;
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () { start(); });
    } else {
      start();
    }
  }

  var search = {
    MINIMUM_MS: MINIMUM_MS,
    NOTES: NOTES,
    note: note,
    counter: counter,
    start: start,
    reset: function () { current = null; },
    arrive: arrive,
    tick: tick,
    draw: draw,
    stop: stop,
    lost: lost,
    totalOf: totalOf,
  };

  root.TerraFolio = root.TerraFolio || {};
  root.TerraFolio.search = search;

  if (typeof module === 'object' && module.exports) module.exports = search;
  boot();
})(typeof globalThis !== 'undefined' ? globalThis : this);
