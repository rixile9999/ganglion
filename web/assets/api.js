/* ============================================================
   GANGLION OPERATOR CONSOLE  ·  shared page-side API client
   spec: docs/tasks/console_operator.md (§Scope, "Page-side half
   of the API contract") — the console server is ganglion/console.

   window.GanglionAPI = {
     get, post,            // /api/* JSON calls (Promise, typed errors)
     ApiError,             // thrown by get/post on a non-2xx response
     ctx, loadCtx, saveCtx,// {catalog, model, session, run} in ?query + localStorage
     fmt,                  // rate / ms / int / tokens / ts / json / short
     planCalls,            // plan object -> [{action, args}]
     renderPlanCalls,      // plan object -> numbered call-list markup
     diffPlans,            // (a, b) -> {equal, changes, leftHtml, rightHtml}
     escapeHtml,           // string -> HTML-safe string
     renderError,          // Error -> .callout markup
     showError, clearError // paint/clear an error into a container element
   }

   Vanilla ES2018. No build step, no external libraries. Every value
   that reaches innerHTML goes through escapeHtml first (prompts and
   model output are untrusted text).
   ============================================================ */
(function () {
  'use strict';

  // ---- errors ------------------------------------------------
  /** One non-2xx /api/* response: {"error","detail"} plus the status code. */
  function ApiError(status, error, detail) {
    var message = detail || error || ('HTTP ' + status);
    var self = new Error(message);
    self.name = 'ApiError';
    self.status = status;
    self.error = error || 'http_error';
    self.detail = detail || '';
    return self;
  }

  // ---- transport ---------------------------------------------
  /** Build "path?k=v&…" from a params object, skipping null/undefined/"". */
  function withQuery(path, params) {
    if (!params) return path;
    var parts = [];
    Object.keys(params).forEach(function (key) {
      var value = params[key];
      if (value === null || value === undefined || value === '') return;
      parts.push(encodeURIComponent(key) + '=' + encodeURIComponent(String(value)));
    });
    if (!parts.length) return path;
    return path + (path.indexOf('?') === -1 ? '?' : '&') + parts.join('&');
  }

  /** Shared fetch wrapper: JSON in, JSON out, ApiError on any non-2xx. */
  function request(method, path, body) {
    var init = { method: method, headers: { 'Accept': 'application/json' } };
    if (body !== undefined && body !== null) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    return fetch(path, init).then(function (response) {
      return response.text().then(function (text) {
        var payload = null;
        if (text) {
          try { payload = JSON.parse(text); } catch (err) { payload = null; }
        }
        if (!response.ok) {
          var error = payload && payload.error ? payload.error : 'http_error';
          var detail = payload && payload.detail ? payload.detail : (text || '').slice(0, 400);
          throw ApiError(response.status, error, detail);
        }
        return payload;
      });
    }, function (err) {
      // Network / server-down: fetch rejects before any status is known.
      throw ApiError(0, 'unreachable', 'console server unreachable (' + err.message + ')');
    });
  }

  /** GET one /api/* route; `params` is an optional query object. */
  function get(path, params) { return request('GET', withQuery(path, params), null); }

  /** POST one /api/* route with a JSON body. */
  function post(path, body) { return request('POST', path, body); }

  // ---- context (catalog / model / session / run) --------------
  var CTX_KEYS = ['catalog', 'model', 'session', 'run'];
  var CTX_STORAGE_KEY = 'ganglion.ctx';
  var ctx = { catalog: '', model: '', session: '', run: '' };

  /** localStorage may throw (private window, blocked site data) — never fatal. */
  function readStore() {
    try {
      var raw = window.localStorage.getItem(CTX_STORAGE_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch (err) { return {}; }
  }

  function writeStore(value) {
    try { window.localStorage.setItem(CTX_STORAGE_KEY, JSON.stringify(value)); } catch (err) { /* ignore */ }
  }

  /** URL query wins over localStorage; both are optional. Returns `ctx`. */
  function loadCtx() {
    var stored = readStore();
    var params;
    try { params = new URLSearchParams(window.location.search); } catch (err) { params = null; }
    CTX_KEYS.forEach(function (key) {
      var fromUrl = params ? params.get(key) : null;
      ctx[key] = fromUrl || stored[key] || '';
    });
    return ctx;
  }

  /** Merge `patch` into ctx, persist it and rewrite ?query without navigating. */
  function saveCtx(patch) {
    if (patch) {
      CTX_KEYS.forEach(function (key) {
        if (Object.prototype.hasOwnProperty.call(patch, key) && patch[key] !== undefined) {
          ctx[key] = patch[key] === null ? '' : String(patch[key]);
        }
      });
    }
    writeStore(ctx);
    try {
      var params = new URLSearchParams(window.location.search);
      CTX_KEYS.forEach(function (key) {
        if (ctx[key]) params.set(key, ctx[key]); else params.delete(key);
      });
      var query = params.toString();
      window.history.replaceState(null, '', window.location.pathname + (query ? '?' + query : ''));
    } catch (err) { /* ignore — ctx is still in localStorage */ }
    return ctx;
  }

  // ---- escaping ----------------------------------------------
  var ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

  /** HTML-escape any value (null/undefined -> ""). Use on EVERY interpolation. */
  function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value).replace(/[&<>"']/g, function (ch) { return ESCAPES[ch]; });
  }

  // ---- formatting --------------------------------------------
  var fmt = {
    /** 0.8714 -> "0.871"; null/NaN -> "—". */
    rate: function (value, digits) {
      if (value === null || value === undefined || isNaN(Number(value))) return '—';
      return Number(value).toFixed(digits === undefined ? 3 : digits);
    },
    /** 412.4 -> "412 ms"; 1610.9 -> "1.61 s"; null -> "—". */
    ms: function (value) {
      if (value === null || value === undefined || isNaN(Number(value))) return '—';
      var n = Number(value);
      if (n >= 1000) return (n / 1000).toFixed(2) + ' s';
      return Math.round(n) + ' ms';
    },
    /** 12401 -> "12,401"; null -> "—". */
    int: function (value) {
      if (value === null || value === undefined || isNaN(Number(value))) return '—';
      return Math.round(Number(value)).toLocaleString('en-US');
    },
    /** (312, 41) -> "in 312 / out 41"; nulls -> "in — / out —". */
    tokens: function (input, output) {
      return 'in ' + fmt.int(input) + ' / out ' + fmt.int(output);
    },
    /** "2026-09-16T02:26:28Z" -> "2026-09-16 02:26:28"; "" -> "—". */
    ts: function (iso) {
      if (!iso) return '—';
      return String(iso).replace('T', ' ').replace('Z', '').split('.')[0];
    },
    /** Compact one-line JSON (sorted keys are the server's job, not ours). */
    json: function (value) {
      if (value === null || value === undefined) return 'null';
      try { return JSON.stringify(value); } catch (err) { return String(value); }
    },
    /** Pretty 2-space JSON for editors and RAW panes. */
    pretty: function (value) {
      if (value === null || value === undefined) return 'null';
      try { return JSON.stringify(value, null, 2); } catch (err) { return String(value); }
    },
    /** "tr-932047841328f972" -> "tr-93204784…" (long ids in tight cells). */
    short: function (value, keep) {
      var text = value === null || value === undefined ? '' : String(value);
      var n = keep === undefined ? 12 : keep;
      return text.length > n ? text.slice(0, n) + '…' : text;
    },
    /** mm:ss elapsed from a millisecond count (label timer). */
    clock: function (millis) {
      var total = Math.max(0, Math.floor(Number(millis || 0) / 1000));
      var mm = Math.floor(total / 60);
      var ss = total % 60;
      return mm + ':' + (ss < 10 ? '0' : '') + ss;
    }
  };

  // ---- plans --------------------------------------------------
  /** Normalise any Action IR shape to [{action, args}] (never throws). */
  function planCalls(plan) {
    if (!plan || typeof plan !== 'object') return [];
    var raw = Array.isArray(plan) ? plan : plan.calls;
    if (!Array.isArray(raw)) {
      // The validator also accepts a bare {"action":…, "args":…}.
      if (plan.action) return [{ action: plan.action, args: plan.args || {} }];
      return [];
    }
    return raw.map(function (call) {
      if (!call || typeof call !== 'object') return { action: String(call), args: {} };
      return { action: call.action || call.name || '', args: call.args || call.arguments || {} };
    });
  }

  /** "set_light(room=living, state=on)" as coloured, escaped markup. */
  function callSignature(call) {
    var args = call.args && typeof call.args === 'object' ? call.args : {};
    var keys = Object.keys(args);
    var parts = keys.map(function (key) {
      var value = args[key];
      var text = (typeof value === 'object' && value !== null) ? fmt.json(value) : String(value);
      return '<span class="t-dim">' + escapeHtml(key) + '=</span><span class="t-bright">' +
        escapeHtml(text) + '</span>';
    });
    return '<span class="t-bright" style="font-weight:600">' + escapeHtml(call.action) + '</span>' +
      '<span class="t-dim">(</span>' + parts.join('<span class="t-dim">, </span>') + '<span class="t-dim">)</span>';
  }

  /**
   * Numbered call list (mockups/Main.dc.html §c1). Execution order = list
   * order, because ActionPlan.calls is an ordered tuple.
   * opts: {empty: string shown when the plan has no calls}
   */
  function renderPlanCalls(plan, opts) {
    injectCss();
    var options = opts || {};
    var calls = planCalls(plan);
    if (!calls.length) {
      var empty = options.empty || (plan ? 'no calls — {"calls": []} (abstention)' : 'no plan');
      return '<div class="calls__empty t-mute">' + escapeHtml(empty) + '</div>';
    }
    var rows = calls.map(function (call, index) {
      return '<div class="callrow">' +
        '<div class="callrow__n">' + (index + 1) + '</div>' +
        '<div class="callrow__sig">' + callSignature(call) + '</div>' +
        '</div>';
    });
    return '<div class="calls">' + rows.join('') + '</div>';
  }

  // ---- plan diff ----------------------------------------------
  function deepEqual(a, b) {
    if (a === b) return true;
    if (typeof a !== typeof b) return false;
    if (a === null || b === null || typeof a !== 'object') return false;
    if (Array.isArray(a) !== Array.isArray(b)) return false;
    var keysA = Object.keys(a);
    var keysB = Object.keys(b);
    if (keysA.length !== keysB.length) return false;
    for (var i = 0; i < keysA.length; i += 1) {
      if (!Object.prototype.hasOwnProperty.call(b, keysA[i])) return false;
      if (!deepEqual(a[keysA[i]], b[keysA[i]])) return false;
    }
    return true;
  }

  var MISSING = { __missing__: true };

  function collectChanges(a, b, path, out) {
    if (deepEqual(a, b)) return;
    // MISSING is a sentinel, not a value: an absent side must never be
    // descended into, or its own `__missing__` key leaks out as a phantom
    // change and the present side's contents are never reported.
    var aGone = a === MISSING;
    var bGone = b === MISSING;
    var aObj = !aGone && a && typeof a === 'object';
    var bObj = !bGone && b && typeof b === 'object';
    if (aObj && bObj && Array.isArray(a) === Array.isArray(b)) {
      var keys = Object.keys(a).concat(Object.keys(b)).filter(function (key, index, all) {
        return all.indexOf(key) === index;
      });
      keys.forEach(function (key) {
        var childPath = path ? path + '.' + key : key;
        var left = Object.prototype.hasOwnProperty.call(a, key) ? a[key] : MISSING;
        var right = Object.prototype.hasOwnProperty.call(b, key) ? b[key] : MISSING;
        collectChanges(left, right, childPath, out);
      });
      return;
    }
    out.push({
      path: path || '(root)',
      from: aGone ? null : a,
      to: bGone ? null : b,
      kind: aGone ? 'added' : (bGone ? 'removed' : 'changed')
    });
  }

  /** Pretty-print `value`, wrapping leaves that differ from `other` in `cls`. */
  function renderJsonHtml(value, other, cls, indent) {
    var pad = new Array(indent + 1).join('  ');
    var padIn = new Array(indent + 2).join('  ');
    var changed = !deepEqual(value, other);
    if (value === MISSING) return '';
    if (value === null || typeof value !== 'object') {
      var text = JSON.stringify(value === undefined ? null : value);
      return changed ? '<span class="' + cls + '">' + escapeHtml(text) + '</span>' : escapeHtml(text);
    }
    var otherObj = (other !== MISSING && other && typeof other === 'object') ? other : {};
    if (Array.isArray(value)) {
      if (!value.length) return '[]';
      var items = value.map(function (item, index) {
        var sibling = Array.isArray(other) && index < other.length ? other[index] : MISSING;
        return padIn + renderJsonHtml(item, sibling, cls, indent + 1);
      });
      return '[\n' + items.join(',\n') + '\n' + pad + ']';
    }
    var keys = Object.keys(value);
    if (!keys.length) return '{}';
    var entries = keys.map(function (key) {
      var sibling = Object.prototype.hasOwnProperty.call(otherObj, key) ? otherObj[key] : MISSING;
      var keyHtml = escapeHtml(JSON.stringify(key));
      if (sibling === MISSING) keyHtml = '<span class="' + cls + '">' + keyHtml + '</span>';
      return padIn + keyHtml + ': ' + renderJsonHtml(value[key], sibling, cls, indent + 1);
    });
    return '{\n' + entries.join(',\n') + '\n' + pad + '}';
  }

  /**
   * Structural diff of two Action IR plans (or any two JSON values).
   * Returns {equal, changes: [{path, from, to, kind}], leftHtml, rightHtml}.
   * leftHtml marks its differing leaves vermillion, rightHtml chartreuse —
   * the F⁰ / Fᴷ convention of mockups/Main.dc.html.
   */
  function diffPlans(a, b) {
    var changes = [];
    collectChanges(a === undefined ? null : a, b === undefined ? null : b, '', changes);
    return {
      equal: changes.length === 0,
      changes: changes,
      leftHtml: renderJsonHtml(a === undefined ? null : a, b === undefined ? null : b, 't-verm', 0),
      rightHtml: renderJsonHtml(b === undefined ? null : b, a === undefined ? null : a, 't-chart', 0)
    };
  }

  // ---- error surface ------------------------------------------
  /** One `.callout` describing a failed call — never an alert(), never silent. */
  function renderError(err) {
    var status = err && err.status ? ' · HTTP ' + err.status : '';
    var name = err && err.error ? err.error : (err && err.name) || 'error';
    var detail = err && err.detail ? err.detail : (err && err.message) || String(err);
    return '<div class="callout t-verm"><b>' + escapeHtml(name) + escapeHtml(status) + '</b><br>' +
      '<span class="t-dim">' + escapeHtml(detail) + '</span></div>';
  }

  /** Paint `err` into a container element (hidden when cleared). */
  function showError(element, err) {
    if (!element) return;
    element.innerHTML = renderError(err);
    element.hidden = false;
  }

  function clearError(element) {
    if (!element) return;
    element.innerHTML = '';
    element.hidden = true;
  }

  // ---- css for the markup this module generates ----------------
  var CSS = [
    '.calls{display:flex;flex-direction:column;border-top:1px solid var(--rule)}',
    '.callrow{display:grid;grid-template-columns:28px minmax(0,1fr);gap:12px;align-items:center;',
    'padding:8px 0;border-bottom:1px dashed var(--rule)}',
    '.callrow__n{width:28px;height:28px;display:flex;align-items:center;justify-content:center;',
    'border:1px solid var(--teal-dim);background:var(--ink-raised);color:var(--teal);',
    'font-family:var(--mono);font-size:12px;font-weight:500}',
    '.callrow__sig{font-family:var(--mono);font-size:14px;overflow-wrap:anywhere}',
    '.calls__empty{font-family:var(--mono);font-size:12px;font-style:italic;padding:10px 0;',
    'border-top:1px solid var(--rule)}'
  ].join('');

  var cssInjected = false;

  /** api.js owns the CSS for its own generated markup (injected once). */
  function injectCss() {
    if (cssInjected || !document.head) return;
    cssInjected = true;
    var style = document.createElement('style');
    style.id = 'ganglion-api-css';
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  window.GanglionAPI = {
    ApiError: ApiError,
    get: get,
    post: post,
    withQuery: withQuery,
    ctx: ctx,
    loadCtx: loadCtx,
    saveCtx: saveCtx,
    fmt: fmt,
    planCalls: planCalls,
    renderPlanCalls: renderPlanCalls,
    diffPlans: diffPlans,
    escapeHtml: escapeHtml,
    renderError: renderError,
    showError: showError,
    clearError: clearError
  };

  loadCtx();
}());
