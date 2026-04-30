/**
 * Frontend analytics: page_view, search, export_click, digest_action.
 * Stores events in localStorage (capped) and emits a CustomEvent for the debug panel.
 * Backend ingest is best-effort — silently skipped if /api/analytics/events 404s.
 */
(function() {
  'use strict';

  var STORAGE_KEY = 'cybernews_analytics_events';
  var SESSION_KEY = 'cybernews_analytics_session';
  var ANON_KEY = 'cybernews_analytics_anon';
  var MAX_EVENTS = 200;
  var SESSION_TTL_MS = 30 * 60 * 1000; // 30 min idle window
  var INGEST_URL = '/api/analytics/events';

  var ingestDisabled = false;

  function uid() {
    if (window.crypto && window.crypto.randomUUID) {
      try { return window.crypto.randomUUID(); } catch (e) {}
    }
    return 'a-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
  }

  function safeRead(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }
  function safeWrite(key, val) {
    try { localStorage.setItem(key, val); } catch (e) {}
  }

  function getAnonId() {
    var id = safeRead(ANON_KEY);
    if (!id) { id = uid(); safeWrite(ANON_KEY, id); }
    return id;
  }

  function getSessionId() {
    var raw = safeRead(SESSION_KEY);
    var now = Date.now();
    if (raw) {
      try {
        var parsed = JSON.parse(raw);
        if (parsed && parsed.id && (now - (parsed.last || 0)) < SESSION_TTL_MS) {
          parsed.last = now;
          safeWrite(SESSION_KEY, JSON.stringify(parsed));
          return parsed.id;
        }
      } catch (e) {}
    }
    var fresh = { id: uid(), start: now, last: now };
    safeWrite(SESSION_KEY, JSON.stringify(fresh));
    return fresh.id;
  }

  function loadEvents() {
    var raw = safeRead(STORAGE_KEY);
    if (!raw) return [];
    try {
      var parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) { return []; }
  }

  function saveEvents(list) {
    if (list.length > MAX_EVENTS) list = list.slice(list.length - MAX_EVENTS);
    safeWrite(STORAGE_KEY, JSON.stringify(list));
  }

  function clearEvents() {
    try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
    dispatch({ type: 'cleared' });
  }

  function dispatch(detail) {
    try {
      window.dispatchEvent(new CustomEvent('cybernews:analytics', { detail: detail }));
    } catch (e) {}
  }

  function sendBeacon(payload) {
    if (ingestDisabled) return;
    try {
      var body = JSON.stringify(payload);
      if (navigator.sendBeacon) {
        var blob = new Blob([body], { type: 'application/json' });
        navigator.sendBeacon(INGEST_URL, blob);
        return;
      }
      fetch(INGEST_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body,
        keepalive: true,
      }).catch(function() { ingestDisabled = true; });
    } catch (e) { ingestDisabled = true; }
  }

  function track(name, props) {
    if (!name) return;
    var event = {
      id: uid(),
      name: String(name),
      props: props && typeof props === 'object' ? props : {},
      ts: new Date().toISOString(),
      path: location.pathname + location.search,
      session: getSessionId(),
      anon: getAnonId(),
    };
    var list = loadEvents();
    list.push(event);
    saveEvents(list);
    dispatch({ type: 'event', event: event });
    if (window.console && console.info) {
      console.info('[analytics]', event.name, event.props, '·', event.path);
    }
    sendBeacon(event);
    return event;
  }

  function pageView(extra) {
    var props = Object.assign({
      title: document.title || '',
      referrer: document.referrer || '',
      lang: (window.currentLanguage || document.documentElement.lang || 'en'),
      viewport: window.innerWidth + 'x' + window.innerHeight,
    }, extra || {});
    return track('page_view', props);
  }

  function getEvents() {
    return loadEvents();
  }

  function print(limit) {
    var list = loadEvents();
    if (limit && limit > 0) list = list.slice(-limit);
    if (!console || !console.table) {
      console.log(list);
      return list;
    }
    console.table(list.map(function(e) {
      return { name: e.name, path: e.path, ts: e.ts, props: JSON.stringify(e.props) };
    }));
    return list;
  }

  // ---------- Floating debug panel (UI affordance) ----------
  // Visible when:
  //   1) URL has ?analytics=1, or
  //   2) localStorage 'analytics_debug' === '1' (toggled by Ctrl+Shift+A)
  function shouldShowPanel() {
    if (location.search.indexOf('analytics=1') >= 0) {
      safeWrite('analytics_debug', '1');
      return true;
    }
    return safeRead('analytics_debug') === '1';
  }

  function setPanelEnabled(on) {
    safeWrite('analytics_debug', on ? '1' : '0');
    var panel = document.getElementById('cn-analytics-panel');
    var fab = document.getElementById('cn-analytics-fab');
    if (on) {
      if (!fab) mountPanel();
    } else {
      if (panel) panel.remove();
      if (fab) fab.remove();
    }
  }

  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fmtProps(props) {
    if (!props || !Object.keys(props).length) return '';
    try { return JSON.stringify(props); } catch (e) { return ''; }
  }

  function mountPanel() {
    if (document.getElementById('cn-analytics-fab')) return;

    var fab = document.createElement('button');
    fab.id = 'cn-analytics-fab';
    fab.type = 'button';
    fab.setAttribute('aria-label', 'Toggle analytics debug panel');
    fab.title = 'Analytics events (Ctrl+Shift+A to hide)';
    fab.innerHTML = '<i class="fas fa-chart-line"></i><span class="cn-analytics-fab-count" id="cn-analytics-count">0</span>';
    document.body.appendChild(fab);

    var panel = document.createElement('aside');
    panel.id = 'cn-analytics-panel';
    panel.setAttribute('aria-label', 'Analytics events debug panel');
    panel.hidden = true;
    panel.innerHTML =
      '<header class="cn-analytics-head">' +
        '<span class="cn-analytics-title"><i class="fas fa-chart-line"></i> Analytics events</span>' +
        '<div class="cn-analytics-actions">' +
          '<button type="button" id="cn-analytics-clear" class="cn-analytics-btn" title="Clear events">Clear</button>' +
          '<button type="button" id="cn-analytics-close" class="cn-analytics-btn" aria-label="Close">×</button>' +
        '</div>' +
      '</header>' +
      '<div class="cn-analytics-meta" id="cn-analytics-meta"></div>' +
      '<ul class="cn-analytics-list" id="cn-analytics-list"></ul>';
    document.body.appendChild(panel);

    fab.addEventListener('click', function() {
      panel.hidden = !panel.hidden;
      if (!panel.hidden) renderPanel();
    });
    panel.querySelector('#cn-analytics-close').addEventListener('click', function() {
      panel.hidden = true;
    });
    panel.querySelector('#cn-analytics-clear').addEventListener('click', function() {
      clearEvents();
      renderPanel();
    });

    window.addEventListener('cybernews:analytics', function() {
      updateCount();
      if (!panel.hidden) renderPanel();
    });

    updateCount();
  }

  function updateCount() {
    var el = document.getElementById('cn-analytics-count');
    if (!el) return;
    el.textContent = String(loadEvents().length);
  }

  function renderPanel() {
    var listEl = document.getElementById('cn-analytics-list');
    var metaEl = document.getElementById('cn-analytics-meta');
    if (!listEl) return;
    var events = loadEvents().slice().reverse();
    if (metaEl) {
      metaEl.textContent = events.length
        ? events.length + ' events · session ' + getSessionId().slice(0, 8)
        : 'No events yet — interact with the page to capture some.';
    }
    if (!events.length) {
      listEl.innerHTML = '';
      return;
    }
    listEl.innerHTML = events.slice(0, 50).map(function(e) {
      var time = e.ts ? e.ts.split('T')[1].slice(0, 8) : '';
      var props = fmtProps(e.props);
      return (
        '<li class="cn-analytics-item cn-ev-' + escHtml(e.name) + '">' +
          '<div class="cn-analytics-row">' +
            '<span class="cn-analytics-name">' + escHtml(e.name) + '</span>' +
            '<span class="cn-analytics-time">' + escHtml(time) + '</span>' +
          '</div>' +
          '<div class="cn-analytics-path">' + escHtml(e.path || '') + '</div>' +
          (props ? '<pre class="cn-analytics-props">' + escHtml(props) + '</pre>' : '') +
        '</li>'
      );
    }).join('');
  }

  function bindKeyToggle() {
    document.addEventListener('keydown', function(e) {
      if (e.ctrlKey && e.shiftKey && (e.key === 'A' || e.key === 'a')) {
        e.preventDefault();
        var on = safeRead('analytics_debug') !== '1';
        setPanelEnabled(on);
      }
    });
  }

  // ---------- Public API ----------
  window.CyberNews = window.CyberNews || {};
  window.CyberNews.analytics = {
    track: track,
    pageView: pageView,
    getEvents: getEvents,
    clear: clearEvents,
    print: print,
    showPanel: function() { setPanelEnabled(true); },
    hidePanel: function() { setPanelEnabled(false); },
    sessionId: getSessionId,
    anonId: getAnonId,
  };

  function init() {
    pageView();
    bindKeyToggle();
    if (shouldShowPanel()) mountPanel();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
