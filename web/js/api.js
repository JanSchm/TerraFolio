/**
 * api.js — the whole client side of docs/api.md, in one place.
 *
 * Hand-written over `fetch` and `EventSource`. There is no generated client and no
 * HTTP library: the wire contract is twelve endpoints and one error envelope, and a
 * dependency would have to be vendored into web/vendor/ to survive tests/offline.js.
 *
 * Three things run through all of it:
 *
 *   - **Every call is feature-detected and returns `null` when there is no server.**
 *     spec §12 requires the pages to open from disk, where `fetch` is CORS-blocked
 *     against an opaque origin (decisions.md A-15), and the node suite loads every
 *     page from a `file://` URL under jsdom, which provides neither `fetch` nor
 *     `EventSource`. A bare call there throws inside a deferred script and takes
 *     Alpine's whole init pass with it. So `offline()` is checked first and every
 *     caller treats `null` as "no data yet", which is the state the pages ship in.
 *   - **One error shape.** api.md §1.7 gives every failure as
 *     `{ error: { code, message, detail } }` — including the 404 from a static mount,
 *     because api/errors.py wraps Starlette's own handlers. So one reader serves
 *     every endpoint, and `message` is already one sentence fit to show a user.
 *   - **Paths are root-relative.** api/app.py mounts the pages beside the API on one
 *     origin and there is no CORS anywhere, so an absolute path is both correct and
 *     the only thing that can work.
 *
 * Classic script in the browser, CommonJS under node. See docs/decisions.md A-15.
 */
(function (root) {
  'use strict';

  /* ── Availability ───────────────────────────────────────────────────────────
     Not a capability probe for its own sake: this is the one guard that keeps the
     pages working from disk and keeps 3B's suite green. */

  function offline() {
    if (typeof fetch !== 'function') return true;
    var where = root.location;
    return !where || where.protocol === 'file:';
  }

  function streamable() {
    return !offline() && typeof root.EventSource === 'function';
  }

  /* ── The error envelope (api.md §1.7) ───────────────────────────────────── */

  /**
   * An `Error` carrying the envelope's three parts, so a caller can branch on
   * `code` — `PIPELINE_MOVED`, `LOCKS_EXCEED_CAPITAL` — and still have something
   * printable in `message`.
   */
  function apiError(status, body) {
    var envelope = (body && body.error) || {};
    var error = new Error(envelope.message || 'The server could not complete that request.');
    error.name = 'ApiError';
    error.status = status;
    error.code = envelope.code || null;
    error.detail = envelope.detail || {};
    return error;
  }

  function parse(response) {
    return response.text().then(function (text) {
      if (!text) return null;
      try {
        return JSON.parse(text);
      } catch (ignored) {
        return null;
      }
    });
  }

  /**
   * One request. Resolves to the parsed body, to `null` when there is no server,
   * and rejects with an `apiError` on any status the caller did not name as ok.
   */
  function request(path, options) {
    if (offline()) return Promise.resolve(null);
    var settings = options || {};
    var init = { method: settings.method || 'GET', headers: settings.headers || {} };
    if (settings.body !== undefined) {
      init.body = JSON.stringify(settings.body);
      init.headers = assign({ 'Content-Type': 'application/json' }, init.headers);
    }
    return fetch(path, init).then(function (response) {
      if (settings.onStatus && settings.onStatus[response.status]) {
        return settings.onStatus[response.status](response);
      }
      if (!response.ok) {
        return parse(response).then(function (body) {
          throw apiError(response.status, body);
        });
      }
      return parse(response);
    });
  }

  /** `Object.assign` without relying on it; the vendored bundles target the same floor. */
  function assign(target, source) {
    var key;
    for (key in source) {
      if (Object.prototype.hasOwnProperty.call(source, key)) target[key] = source[key];
    }
    return target;
  }

  /* ── GET /pipeline, with its ETag (api.md §2) ───────────────────────────────
     The ETag covers pipeline hash, assumption set, engine version AND hold period,
     so it is cached per hold period: a client holding IRRs solved at ten years must
     not be handed a 304 when the slider says twenty. */

  var pipelineCache = {};
  var assumptionsCache = null;

  function pipelineBody(holdYears) {
    var key = String(holdYears);
    var cached = pipelineCache[key];
    var headers = cached ? { 'If-None-Match': cached.etag } : {};
    return request('/pipeline?holdYears=' + encodeURIComponent(holdYears), {
      headers: headers,
      onStatus: {
        /* Nothing moved. api.md §2 sends no body with a 304, so the cached one is
           the answer — and the four-part validator is what makes that safe. */
        304: function () {
          return cached.payload;
        },
        /* The ETag lives in a header, which `request` does not otherwise expose,
           so the 200 is read here rather than in the common path. */
        200: function (response) {
          var etag = response.headers.get('ETag');
          return parse(response).then(function (payload) {
            if (etag) cachePipeline(holdYears, etag, payload);
            return payload;
          });
        },
      },
    });
  }

  /**
   * The pipeline, with the assumption set folded in.
   *
   * `feasibility.js` reads its risk ceiling off `payload.assumptions.riskCaps`, and
   * deliberately refuses to carry one of its own (epic §5: every band is an auditable
   * configuration change). api.md §2 does not put an assumption set on `/pipeline`
   * and §10 gives it its own endpoint, where `riskCaps` carries **both** caps —
   * `project` is §5.3's per-project pre-screen and `portfolio` the objective's
   * capex-weighted penalty. The screens take the first. Joining the two responses
   * here rather than in mandate.js keeps every wire name in one file.
   */
  function getPipeline(holdYears) {
    if (offline()) return Promise.resolve(null);
    return Promise.all([pipelineBody(holdYears), getAssumptions()]).then(function (both) {
      var pipeline = both[0];
      var assumptions = both[1];
      if (!pipeline) return null;
      return withAssumptions(pipeline, assumptions);
    });
  }

  function withAssumptions(pipeline, assumptions) {
    var merged = assign({}, pipeline);
    var caps = (assumptions && assumptions.riskCaps) || {};
    merged.assumptions = {
      riskCaps: caps.project || null,
      portfolioRiskCaps: caps.portfolio || null,
      co2FactorTPerMwh: assumptions ? assumptions.co2FactorTPerMwh : null,
      set: assumptions || null,
    };
    return merged;
  }

  /** Records the ETag alongside the body, so the next call can ask for a 304. */
  function cachePipeline(holdYears, etag, payload) {
    pipelineCache[String(holdYears)] = { etag: etag, payload: payload };
  }

  function getAssumptions() {
    if (assumptionsCache) return Promise.resolve(assumptionsCache);
    return request('/assumptions').then(function (payload) {
      if (payload) assumptionsCache = payload;
      return payload;
    });
  }

  /* ── GET /pipeline/status and POST /pipeline/reload (api.md §3) ─────────────
     One shape for "what is the state of the pipeline". The mandate screen's only
     route to knowing a colleague's file failed its tie-outs. */

  function getPipelineStatus() {
    return request('/pipeline/status');
  }

  function reloadPipeline() {
    pipelineCache = {};
    return request('/pipeline/reload', { method: 'POST' });
  }

  /* ── GET /projects/{id}/statements (api.md §4) ──────────────────────────────
     Mandate-independent, so no hold period, and fetched on drawer open rather than
     with the pipeline: the arrays are four and a half times the whole rest of a
     300-project response. */

  var statementsCache = {};

  function getProjectStatements(projectId) {
    var cached = statementsCache[projectId];
    if (cached) return Promise.resolve(cached);
    return request('/projects/' + encodeURIComponent(projectId) + '/statements')
      .then(function (payload) {
        if (payload) statementsCache[projectId] = payload;
        return payload;
      });
  }

  /* ── POST /mandate/preview (api.md §5) ──────────────────────────────────────
     The screen computes this client-side against §12's 100 ms budget; this exists so
     the two can be held to the same answer, and #12 asserts they are. */

  function previewMandate(mandate, locks) {
    return request('/mandate/preview', {
      method: 'POST',
      body: {
        mandate: mandate,
        lockedIds: (locks && locks.lockedIds) || [],
        excludedIds: (locks && locks.excludedIds) || [],
      },
    });
  }

  /* ── POST /optimisations (api.md §6) ────────────────────────────────────────
     202 with the resolved seed, never a result. */

  function postOptimisation(payload) {
    return request('/optimisations', { method: 'POST', body: payload });
  }

  /* ── GET /optimisations/{id} and its stream (api.md §7, §8) ─────────────── */

  function getResult(runId) {
    return request('/optimisations/' + encodeURIComponent(runId));
  }

  /**
   * Subscribe to a run.
   *
   * The event log is the stream's source of truth, so a subscriber that joined late
   * — or after the run finished — replays every round and then receives one terminal
   * frame. That is what makes it safe to POST and then open the stream.
   *
   * Returns a handle with `close()`, or `null` when there is no `EventSource`.
   */
  function openStream(runId, handlers) {
    if (!streamable()) return null;
    var on = handlers || {};
    var source = new root.EventSource('/optimisations/' + encodeURIComponent(runId) + '/stream');
    var closed = false;

    function listen(name, handler) {
      if (!handler) return;
      source.addEventListener(name, function (event) {
        var data = null;
        try {
          data = JSON.parse(event.data);
        } catch (ignored) {
          return;
        }
        handler(data);
      });
    }

    listen('status', on.status);
    listen('generation', on.round);
    listen('done', on.done);
    listen('failed', on.failed);
    if (on.dropped) source.onerror = function () { if (!closed) on.dropped(); };

    return {
      close: function () {
        closed = true;
        source.close();
      },
    };
  }

  /* ── Exports (api.md §9) ────────────────────────────────────────────────────
     Plain GETs that answer with Content-Disposition, so these are hrefs for the
     browser to follow rather than anything to fetch and re-wrap. */

  function exportUrls(runId) {
    var base = '/optimisations/' + encodeURIComponent(runId);
    return {
      holdings: base + '/holdings.csv',
      cashflow: base + '/cashflow.csv',
      pack: base + '/pack',
    };
  }

  var api = {
    offline: offline,
    streamable: streamable,
    apiError: apiError,
    getPipeline: getPipeline,
    getAssumptions: getAssumptions,
    getPipelineStatus: getPipelineStatus,
    reloadPipeline: reloadPipeline,
    getProjectStatements: getProjectStatements,
    previewMandate: previewMandate,
    postOptimisation: postOptimisation,
    getResult: getResult,
    openStream: openStream,
    exportUrls: exportUrls,
    withAssumptions: withAssumptions,
    cachePipeline: cachePipeline,
  };

  root.TerraFolio = root.TerraFolio || {};
  root.TerraFolio.api = api;

  if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
