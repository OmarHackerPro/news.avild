/**
 * Search page: run search from ?q=, show loading/empty/error/results.
 * Fetches from /api/search/ and renders article cards.
 */
(function() {
  var form = document.getElementById('searchForm');
  var input = document.getElementById('searchInput');
  var resultsContainer = document.getElementById('searchResultsContainer');
  var resultsList = document.getElementById('searchResultsList');
  var resultsHeading = document.getElementById('searchResultsHeading');
  var stateEl = document.getElementById('searchState');

  if (!form || !input || !resultsContainer) return;

  function t(key) {
    return window.CyberNews && window.CyberNews.t ? window.CyberNews.t(key) : key;
  }

  function trackEvent(name, props) {
    if (window.CyberNews && window.CyberNews.analytics) {
      window.CyberNews.analytics.track(name, props);
    }
  }

  var lastState = null;
  var lastQuery = null;

  function showState(type, titleKey, msgKey, err) {
    lastState = { type: type, titleKey: titleKey, msgKey: msgKey, err: err };
    var title = titleKey ? t(titleKey) : '';
    var message = msgKey ? (err || t(msgKey)) : '';
    stateEl.className = 'search-state ' + type;
    var iconClass;
    if (type === 'error') iconClass = 'fas fa-exclamation-circle';
    else if (type === 'empty' && titleKey === 'search.noResultsTitle') iconClass = 'fas fa-folder-open';
    else iconClass = 'fas fa-search';
    var head = type === 'loading'
      ? '<div class="spinner"></div>'
      : '<div class="state-icon"><i class="' + iconClass + '"></i></div>';
    var actions = '';
    if (type === 'error') {
      actions = '<div class="state-actions"><button type="button" class="state-block-btn" id="searchRetryBtn"><i class="fas fa-redo"></i> ' + t('feed.retry') + '</button></div>';
    }
    stateEl.innerHTML =
      head +
      '<div class="state-title">' + (title || '') + '</div>' +
      (message ? '<div class="state-message">' + message + '</div>' : '') +
      actions;
    stateEl.hidden = false;
    if (resultsList) resultsList.innerHTML = '';
    if (resultsHeading) resultsHeading.hidden = true;

    var retry = document.getElementById('searchRetryBtn');
    if (retry) {
      retry.addEventListener('click', function() {
        if (lastQuery) runSearch(lastQuery);
      });
    }
  }

  function showResults(items, total, query) {
    lastState = null;
    stateEl.hidden = true;
    if (!items.length) {
      showState('empty', 'search.noResultsTitle', 'search.noResultsMsg');
      return;
    }
    if (resultsHeading) {
      resultsHeading.textContent = total + ' result' + (total === 1 ? '' : 's') + ' for "' + query + '"';
      resultsHeading.hidden = false;
    }
    resultsList.innerHTML = items.map(function(item) {
      var desc = (item.desc || '').slice(0, 160);
      if (item.desc && item.desc.length > 160) desc += '\u2026';
      var sevBadge = item.severity
        ? '<span class="result-sev sev-' + escapeHtml(item.severity) + '">' + escapeHtml(item.severity) + '</span>'
        : '';
      var tags = (item.raw_tags || item.tags || []).slice(0, 3)
        .map(function(tag) { return '<span class="result-tag">' + escapeHtml(tag) + '</span>'; })
        .join('');
      var meta = [
        item.source_name ? escapeHtml(item.source_name) : '',
        item.published_at ? timeAgo(item.published_at) : '',
      ].filter(Boolean).join(' \u00b7 ');
      return (
        '<a href="' + escapeHtml(item.source_url || '#') + '" target="_blank" rel="noopener" class="search-result-card">' +
          ((sevBadge || tags) ? '<div class="result-badges">' + sevBadge + tags + '</div>' : '') +
          '<div class="result-name">' + escapeHtml(item.title || '') + '</div>' +
          (desc ? '<div class="result-description">' + escapeHtml(desc) + '</div>' : '') +
          (meta ? '<div class="result-meta">' + meta + '</div>' : '') +
        '</a>'
      );
    }).join('');
  }

  function escapeHtml(s) {
    if (!s) return '';
    var div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  function timeAgo(isoStr) {
    if (!isoStr) return '';
    var diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
    if (diff < 0) diff = 0;
    if (diff < 60) return Math.floor(diff) + 's ago';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
  }

  function runSearch(query) {
    lastQuery = query;
    var q = (query || '').trim();
    if (!q) {
      showState('empty', 'search.emptyTitle', 'search.emptyMsg');
      return;
    }
    showState('loading', null, null);
    fetch('/api/search/?q=' + encodeURIComponent(q) + '&limit=20')
      .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function(data) {
        var items = data.items || [];
        showResults(items, data.total || items.length, q);
        trackEvent('search', {
          query: q,
          source: 'search_page',
          results_count: items.length,
          status: items.length ? 'results' : 'empty',
        });
      })
      .catch(function(err) {
        showState('error', 'search.failedTitle', 'search.failedMsg', err && err.message ? err.message : null);
        trackEvent('search', {
          query: q,
          source: 'search_page',
          status: 'error',
          error: err && err.message ? err.message : 'unknown',
        });
      });
  }

  form.addEventListener('submit', function(e) {
    e.preventDefault();
    var q = input.value.trim();
    if (typeof history.replaceState === 'function') {
      var url = '/search' + (q ? '?q=' + encodeURIComponent(q) : '');
      history.replaceState(null, '', url);
    }
    runSearch(q);
  });

  input.addEventListener('input', function() {
    if (!input.value.trim()) {
      showState('empty', 'search.emptyTitle', 'search.emptyMsg');
      if (resultsList) resultsList.innerHTML = '';
      if (resultsHeading) resultsHeading.hidden = true;
    }
  });

  // Re-render current state when language changes
  window.addEventListener('cybernews:languageChange', function() {
    if (lastState) {
      showState(lastState.type, lastState.titleKey, lastState.msgKey, lastState.err);
    } else if (resultsHeading && !resultsHeading.hidden && resultsList) {
      var count = resultsList.querySelectorAll('.search-result-card').length;
      if (count > 0) {
        resultsHeading.textContent = count === 1
          ? t('search.results.singular')
          : t('search.results.plural').replace('{n}', count);
      }
    }
  });

  function initFromUrl() {
    var params = new URLSearchParams(window.location.search);
    var q = params.get('q');
    if (q) {
      input.value = q;
      runSearch(q);
    } else {
      showState('empty', 'search.emptyTitle', 'search.emptyMsg');
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initFromUrl);
  } else {
    initFromUrl();
  }
})();
