/**
 * mandate.js — screen 01, and the state the other two screens read.
 *
 * Two jobs that belong together because they are the same object:
 *
 *   1. **The store.** The mandate, the locks and the exclusions, persisted. spec §5
 *      says "every control persists per user between sessions", so the mandate is
 *      localStorage; §7.6 says locks and exclusions accumulate across runs, which is
 *      a property of the session's work rather than of the user, so those are
 *      sessionStorage alongside the run they were last submitted with. Both are
 *      exported, because search.js and portfolio.js read them and neither should be
 *      reaching into storage keys of its own.
 *   2. **The page.** The footer recomputes on every change, client-side, because
 *      ui-contract.md §3.4 budgets it under 100 ms and a round trip cannot promise
 *      that (spec §12). js/feasibility.js does the computing; this file moves values
 *      between the controls, that function and the DOM, and never formats a number.
 *
 * The page half runs only where `[data-form="mandate"]` exists, so the file is safe
 * to load on all three screens — which is what makes one store rather than three.
 *
 * Classic script in the browser, CommonJS under node. See docs/decisions.md A-15.
 */
(function (root) {
  'use strict';

  var load = (typeof require === 'function')
    ? function (name) { return require('./' + name + '.js'); }
    : function (name) { return root.TerraFolio && root.TerraFolio[name]; };

  var fmt = load('format');
  var feasibility = load('feasibility');
  var api = load('api');
  var status = (typeof require === 'function') ? require('./controls.js').status
    : (root.TerraFolio && root.TerraFolio.status);
  /* #13's, for this issue to fold in (4C-9). `pipelineNotice` tells an empty
     directory apart from a mandate nothing passes, which §13 requires and which
     `NO_CANDIDATES` cannot say: "there are no files" and "no file passes your
     screens" send a user to different places. */
  var edgeStates = (typeof require === 'function') ? require('./edge-states.js')
    : (root.TerraFolio && root.TerraFolio.edgeStates);

  var MANDATE_KEY = 'terrafolio.mandate.v1';
  var STEERING_KEY = 'terrafolio.steering.v1';

  /* ── Storage ────────────────────────────────────────────────────────────────
     Every access is wrapped: storage throws rather than returning null in a private
     window and where site data is blocked, and a page that cannot remember a mandate
     must still let the user state one. */

  function safely(read) {
    try {
      return read();
    } catch (ignored) {
      return null;
    }
  }

  function readJson(storage, key) {
    return safely(function () {
      var raw = storage && storage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    });
  }

  function writeJson(storage, key, value) {
    safely(function () {
      if (storage) storage.setItem(key, JSON.stringify(value));
      return null;
    });
  }

  function local() { return safely(function () { return root.localStorage; }); }
  function session() { return safely(function () { return root.sessionStorage; }); }

  /**
   * How long to wait before writing the mandate back.
   *
   * `localStorage.setItem` is synchronous and disk-backed, and A-18 makes ranges
   * dispatch on `input` so the footer can stay live — one drag of the capital slider
   * is 76 events. spec §5 asks for the mandate to survive *between sessions*, not for
   * every intermediate tick of a slider to reach disk, so the write trails the drag.
   */
  var PERSIST_DEBOUNCE_MS = 250;

  /* ── The store ──────────────────────────────────────────────────────────── */

  var EMPTY_STEERING = {
    lockedIds: [], excludedIds: [], runId: null, runRef: null, signature: null, totalRounds: null,
  };

  function savedMandate() {
    var saved = readJson(local(), MANDATE_KEY);
    return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : null;
  }

  function saveMandate(mandate) {
    writeJson(local(), MANDATE_KEY, mandate);
  }

  var persistTimer = null;
  var pending = null;

  /**
   * Persist the mandate, once the user stops moving.
   *
   * Also flushed on `pagehide`, so a mandate stated and immediately navigated away
   * from is still there on return — a trailing debounce that can lose the last edit
   * is not persistence.
   */
  function persistMandate(mandate) {
    pending = mandate;
    if (typeof root.setTimeout !== 'function') return flushMandate();
    if (persistTimer) root.clearTimeout(persistTimer);
    persistTimer = root.setTimeout(flushMandate, PERSIST_DEBOUNCE_MS);
    return null;
  }

  function flushMandate() {
    if (persistTimer && typeof root.clearTimeout === 'function') root.clearTimeout(persistTimer);
    persistTimer = null;
    if (!pending) return null;
    saveMandate(pending);
    pending = null;
    return null;
  }

  function steering() {
    var saved = readJson(session(), STEERING_KEY);
    if (!saved || typeof saved !== 'object') return clone(EMPTY_STEERING);
    return {
      lockedIds: ids(saved.lockedIds),
      excludedIds: ids(saved.excludedIds),
      runId: saved.runId || null,
      runRef: saved.runRef || null,
      signature: saved.signature || null,
      /* The run's length, from the 202. The search screen needs it before the first
         round streams or its live region stays silent at the one moment a user is
         waiting to hear a number (decisions 3B-3). Rebuilding this object field by
         field is what dropped it, and any later `saveSteering(steering())` then
         erased the stored value too. */
      totalRounds: saved.totalRounds || null,
    };
  }

  function saveSteering(next) {
    writeJson(session(), STEERING_KEY, next);
    return next;
  }

  function ids(value) {
    if (!Array.isArray(value)) return [];
    return value.filter(function (id) { return typeof id === 'string'; }).slice().sort();
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  /**
   * Put a project into, or take it out of, one of the two steering sets.
   *
   * Excluding clears a lock, because ui-contract.md §5.5 says so and because the two
   * instructions contradict each other: a lock forces a project into every candidate
   * portfolio and an exclusion removes it from the candidate set before the search.
   */
  function steer(projectId, change) {
    var next = steering();
    if (change.locked !== undefined) {
      next.lockedIds = withId(next.lockedIds, projectId, change.locked);
      /* Symmetric with the branch below. Locking a project the user had excluded
         used to leave it in both sets, which is a contradiction the screens then
         resolve one way and the equity sum another. Whichever instruction is given
         second is the one meant. */
      if (change.locked) next.excludedIds = withId(next.excludedIds, projectId, false);
    }
    if (change.excluded !== undefined) {
      next.excludedIds = withId(next.excludedIds, projectId, change.excluded);
      if (change.excluded) next.lockedIds = withId(next.lockedIds, projectId, false);
    }
    return saveSteering(next);
  }

  function withId(list, projectId, present) {
    var without = list.filter(function (id) { return id !== projectId; });
    return present ? without.concat([projectId]).sort() : without;
  }

  /**
   * What the primary action compares itself against, so §7.6's relabelling to
   * "Re-run with changes" is a fact rather than a flag someone has to remember to set.
   *
   * Both the keys and any list of values are sorted, because the question is whether
   * this is the same mandate and neither order carries meaning. The list half is not
   * theoretical: `domain/mandate.py` sorts and dedupes `countries` and `stages`, so
   * the mandate that comes back on a stored run is alphabetical while the chips emit
   * them in the order ui-contract.md §3.2 lays them out. Comparing those two
   * literally made every freshly finished run claim it had changed since it ran.
   */
  function signature(mandate, locks) {
    var keys = Object.keys(mandate || {}).sort();
    var ordered = keys.map(function (key) {
      var value = mandate[key];
      return [key, Array.isArray(value) ? value.slice().sort() : value];
    });
    return JSON.stringify([ordered, ids(locks && locks.lockedIds), ids(locks && locks.excludedIds)]);
  }

  /* ── Reading and writing the controls ───────────────────────────────────────
     Each control owns its own scale and offers `wire()` and `restore()`; this file
     knows only that a mandate field is whatever the control with that name carries.
     So the eighteen api.md §6.1 fields are never listed here, and cannot drift. */

  function components(form) {
    if (!root.Alpine || typeof root.Alpine.$data !== 'function') return [];
    var out = [];
    var nodes = form.querySelectorAll('[x-data]');
    for (var i = 0; i < nodes.length; i++) {
      var data = safely(function (node) { return root.Alpine.$data(node); }.bind(null, nodes[i]));
      if (data && data.name && typeof data.wire === 'function') out.push(data);
    }
    return out;
  }

  function readMandate(form) {
    var mandate = {};
    components(form).forEach(function (control) {
      mandate[control.name] = control.wire();
    });
    return mandate;
  }

  function applyMandate(form, saved) {
    components(form).forEach(function (control) {
      if (Object.prototype.hasOwnProperty.call(saved, control.name)) {
        safely(function () {
          control.restore(saved[control.name]);
          return null;
        });
      }
    });
  }

  /* ── The DOM ────────────────────────────────────────────────────────────── */

  function fields(name) {
    return document.querySelectorAll('[data-field="' + name + '"]');
  }

  function setField(name, text) {
    var nodes = fields(name);
    for (var i = 0; i < nodes.length; i++) nodes[i].textContent = text;
  }

  /**
   * The nine screens, named as the panel that carries them names them.
   *
   * `screensToWiden` speaks `optimiser/screens.py`'s vocabulary, which is right for
   * the wire and useless on screen — a user cannot widen a thing called `eurRevenue`.
   * These are ui-contract.md §3.2 and §3.3's own control labels, so the sentence
   * points at something the user can see.
   */
  var SCREEN_LABELS = {
    countries: 'Eligible countries',
    stages: 'Stages in scope',
    codWindow: 'the COD window',
    minDscr: 'Min DSCR',
    riskScore: 'Development risk appetite',
    gridSecured: 'Grid connection secured only',
    eurRevenue: 'EUR-denominated revenue only',
    omContracted: 'O&M partner contracted',
    exclusions: 'the projects you have excluded',
  };

  /** How many to name before the sentence stops being a list and starts being noise. */
  var SCREENS_NAMED = 3;

  /**
   * §13 asks for "an explicit warning naming the screens to widen". §3.5's pinned
   * string names countries, stages and the COD window, which is the common case and
   * simply wrong when the pool was emptied by the DSCR floor or the risk appetite.
   *
   * The pinned sentence is left exactly as it is — it is the server's `message` and
   * three guards hold it verbatim — and this is a second line beneath it, built from
   * the screens that actually rejected something, worst offender first.
   */
  function widenSentence(screens) {
    var named = (screens || []).slice(0, SCREENS_NAMED)
      .map(function (name) { return SCREEN_LABELS[name] || name; });
    if (!named.length) return '';
    if (named.length === 1) return 'Every candidate is dropped by ' + named[0] + '.';
    return 'Most are dropped by ' + named.slice(0, -1).join(', ')
      + ' and ' + named[named.length - 1] + '.';
  }

  /**
   * One warning, in the shape styleguide.html demonstrates: a mark that is hidden
   * from a reader because the word beside it says the same thing, and a tone that is
   * never the only signal (ui-contract.md §7.1).
   */
  function warningItem(warning, screens) {
    var blocks = warning.code === 'NO_CANDIDATES' || warning.code === 'LOCKS_EXCEED_CAPITAL';
    var key = blocks ? 'blocking' : warning.severity;
    var item = document.createElement('li');
    item.className = warning.tone === 'alert'
      ? 'flex items-start gap-[7px] text-warn text-breach'
      : 'flex items-start gap-[7px] text-warn text-muted';
    item.setAttribute('data-code', warning.code);

    var mark = document.createElement('span');
    mark.className = 'num flex-none';
    mark.setAttribute('aria-hidden', 'true');
    mark.textContent = warning.mark;

    var body = document.createElement('span');
    var word = document.createElement('span');
    word.className = 'sr-only';
    word.textContent = status.WORD[key];
    body.appendChild(word);
    body.appendChild(document.createTextNode(warning.message));

    if (warning.code === 'NO_CANDIDATES') {
      var widen = widenSentence(screens);
      if (widen) {
        var detail = document.createElement('span');
        detail.className = 'mt-[2px] block';
        detail.textContent = widen;
        body.appendChild(detail);
      }
    }

    item.appendChild(mark);
    item.appendChild(body);
    return item;
  }

  function renderWarnings(list, warnings, screens) {
    if (!list) return;
    list.textContent = '';
    warnings.forEach(function (warning) {
      list.appendChild(warningItem(warning, screens));
    });
  }

  /* ── The page ───────────────────────────────────────────────────────────── */

  /** Idempotent: `boot` calls it, and a second page would bind the form twice. */
  var current = null;

  function startPage() {
    if (current) return current;
    var form = document.querySelector('[data-form="mandate"]');
    if (!form) return null;

    var runButton = document.querySelector('[data-action="run"]');
    var warningList = document.querySelector('[data-region="feasibility-warnings"]');
    var page = {
      form: form,
      payload: null,
      mandate: {},
      submitWarning: null,
      submitting: false,
    };

    current = page;
    var saved = savedMandate();
    if (saved) applyMandate(form, saved);
    page.mandate = readMandate(form);

    form.addEventListener('tf:change', function (event) {
      var name = event.detail && event.detail.name;
      if (!name) return;
      /* Only a field the mandate already has. Every control on this form emits an
         api.md §6.1 name and `wire-contract.test.js` guards that, but `Mandate`
         forbids extra keys, so anything that slipped through would be a 400 on the
         wire rather than an ignored value. */
      if (!Object.prototype.hasOwnProperty.call(page.mandate, name)) return;
      page.mandate[name] = event.detail.value;
      persistMandate(page.mandate);
      page.submitWarning = null;
      render(page, runButton, warningList);
    });

    form.addEventListener('submit', function (event) {
      event.preventDefault();
      submit(page, runButton, warningList);
    });

    if (typeof root.addEventListener === 'function') {
      root.addEventListener('pagehide', flushMandate);
    }

    var disclosure = document.querySelector('[data-action="show-rejected"]');
    if (disclosure) disclosure.addEventListener('click', function () { toggleRejected(disclosure); });

    render(page, runButton, warningList);
    loadPipeline(page, runButton, warningList);
    return page;
  }

  function loadPipeline(page, runButton, warningList) {
    if (!api || api.offline()) return;
    api.getPipeline(page.mandate.holdYears).then(function (payload) {
      if (!payload) return;
      usePipeline(page, payload);
      render(page, runButton, warningList);
    }).catch(function (error) {
      page.submitWarning = asWarning(error);
      render(page, runButton, warningList);
    });
    api.getPipelineStatus().then(function (health) {
      renderHealth(health);
    }).catch(function () { /* the banner is additional; its absence is not an error */ });
  }

  /**
   * Take a pipeline payload, and settle everything about it that the mandate cannot
   * move.
   *
   * The eyebrow's whole-pipeline capacity is one of those: it is a property of the
   * directory, not of the mandate, and deriving it inside `render` re-mapped and
   * re-sorted every project on every keystroke against §12's 100 ms budget.
   */
  function usePipeline(page, payload) {
    page.payload = payload;
    page.totalCapacity = fmt.mw(feasibility.aggregate(feasibility.projects(payload)).eligibleCapacityMw);
    return page;
  }

  /**
   * The footer, the pipeline eyebrow and the warnings, from one computation.
   *
   * Every figure goes through `feasibility.js`'s `display`, which has already been
   * through `format.js` — the page never formats a number of its own (spec §14).
   */
  function render(page, runButton, warningList) {
    if (!page.payload) {
      renderWarnings(warningList, page.submitWarning ? [page.submitWarning] : []);
      return null;
    }
    var locks = steering();
    var result = feasibility.feasibility(page.payload, page.mandate, locks);

    setField('totalCount', result.display.totalCount);
    setField('totalCapacityMw', page.totalCapacity);
    setField('eligibleCount', result.display.eligibleCount);
    setField('eligibleCapacityMw', result.display.eligibleCapacityMw);
    setField('eligibleEquity_m', result.display.eligibleEquity_m);

    var shown = page.submitWarning ? [page.submitWarning].concat(result.warnings) : result.warnings;
    renderWarnings(warningList, shown, result.screensToWiden);
    if (runButton) runButton.disabled = !result.runnable || page.submitting;
    return result;
  }

  /**
   * The count of files that did not load, and a route to why.
   *
   * This is the user's only route to knowing a colleague's file is broken: a tie-out
   * failure keeps a project out of the pipeline entirely, so it is absent from every
   * figure on the page with nothing to mark its absence. api.md §3 carries the
   * reason per file, and the disclosure shows it.
   */
  function renderHealth(health) {
    var banner = document.querySelector('[data-region="pipeline-health"]');
    if (!banner || !health) return;
    renderEmptyPipeline(health);
    var rejected = health.rejected || [];
    banner.hidden = rejected.length === 0;
    if (!rejected.length) return;

    setField('rejectedCount', rejected.length === 1
      ? '1 file excluded by validation'
      : fmt.count(rejected.length) + ' files excluded by validation');

    var list = document.querySelector('[data-region="rejected-files"]');
    if (!list) return;
    list.textContent = '';
    rejected.forEach(function (entry) {
      var item = document.createElement('li');
      item.className = 'text-meta text-muted';
      var file = document.createElement('span');
      file.className = 'font-condensed font-semibold text-text';
      file.textContent = entry.file;
      item.appendChild(file);
      item.appendChild(document.createTextNode(' ' + fmt.SEPARATOR + ' ' + entry.message));
      list.appendChild(item);
    });
  }

  /**
   * §13's other pipeline state: the directory holds no files at all.
   *
   * `NO_CANDIDATES` still fires — an empty pipeline has nothing that passes — but it
   * tells the user to widen screens that are not the problem. `fileCount`, not
   * `loadedCount`: a directory whose files all failed their tie-outs is a different
   * fault with a different answer, and those files are named in the rejection list
   * above (4C-1).
   */
  function renderEmptyPipeline(health) {
    var region = document.querySelector('[data-region="pipeline-notice"]');
    if (!region || !root.Alpine || typeof root.Alpine.$data !== 'function') return null;
    var notice = safely(function () { return root.Alpine.$data(region); });
    if (!notice || typeof notice.fromStatus !== 'function') return null;
    /* #13 owns the component, its sentence and its marks; this file owns only the
       fact it reports. `fromStatus` reads `fileCount`, not `loadedCount` — a
       directory whose files all failed their tie-outs is a different fault with a
       different answer, and those files are named in the banner above (4C-1). */
    return notice.fromStatus(health);
  }

  /** Open or close the list of files that did not load. */
  function toggleRejected(button) {
    var list = document.querySelector('[data-region="rejected-files"]');
    if (!list) return;
    var open = button.getAttribute('aria-expanded') === 'true';
    button.setAttribute('aria-expanded', open ? 'false' : 'true');
    list.hidden = open;
  }

  /** A failed request, shaped like a feasibility warning so it renders the same way. */
  function asWarning(error) {
    return {
      code: error.code || 'REQUEST_FAILED',
      severity: 'alert',
      tone: 'alert',
      mark: status.MARK.blocking,
      message: error.message,
    };
  }

  function submit(page, runButton, warningList) {
    if (!api || api.offline() || !page.payload || page.submitting) return null;
    page.submitting = true;
    page.submitWarning = null;
    if (runButton) runButton.disabled = true;

    flushMandate();
    var locks = steering();
    return api.postOptimisation({
      mandate: page.mandate,
      lockedIds: locks.lockedIds,
      excludedIds: locks.excludedIds,
      effort: 'standard',
      seed: null,
      pipelineHash: page.payload.pipelineHash,
      assumptionSetId: page.payload.assumptionSetId,
    }).then(function (accepted) {
      if (!accepted) throw new Error('The optimiser did not accept the mandate.');
      var next = steering();
      next.runId = accepted.runId;
      next.runRef = accepted.runRef;
      next.signature = signature(page.mandate, next);
      next.totalRounds = accepted.totalGenerations;
      saveSteering(next);
      root.location.assign('search.html?run=' + encodeURIComponent(accepted.runId));
      return accepted;
    }).catch(function (error) {
      page.submitting = false;
      page.submitWarning = asWarning(error);
      render(page, runButton, warningList);
      return null;
    });
  }

  /* ── Wiring ─────────────────────────────────────────────────────────────── */

  function boot() {
    if (typeof document === 'undefined') return;
    if (root.Alpine) startPage();
    else document.addEventListener('alpine:initialized', function () { startPage(); });
  }

  var mandate = {
    MANDATE_KEY: MANDATE_KEY,
    STEERING_KEY: STEERING_KEY,
    savedMandate: savedMandate,
    saveMandate: saveMandate,
    persistMandate: persistMandate,
    flushMandate: flushMandate,
    steering: steering,
    saveSteering: saveSteering,
    steer: steer,
    signature: signature,
    readMandate: readMandate,
    applyMandate: applyMandate,
    warningItem: warningItem,
    widenSentence: widenSentence,
    SCREEN_LABELS: SCREEN_LABELS,
    startPage: startPage,
    reset: function () { current = null; },
    usePipeline: usePipeline,
    toggleRejected: toggleRejected,
    renderHealth: renderHealth,
    renderEmptyPipeline: renderEmptyPipeline,
  };

  root.TerraFolio = root.TerraFolio || {};
  root.TerraFolio.mandate = mandate;

  if (typeof module === 'object' && module.exports) module.exports = mandate;
  boot();
})(typeof globalThis !== 'undefined' ? globalThis : this);
