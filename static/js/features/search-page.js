/**
 * Search page: run search from ?q=, show loading/empty/error/results.
 * Depends on mock-entities.js (or future API).
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

  function showResults(results, query) {
    lastState = null;
    stateEl.hidden = true;
    if (!results.length) {
      showState('empty', 'search.noResultsTitle', 'search.noResultsMsg');
      return;
    }
    if (resultsHeading) {
      resultsHeading.textContent = results.length === 1
        ? t('search.results.singular')
        : t('search.results.plural').replace('{n}', results.length);
      resultsHeading.hidden = false;
    }
    var mock = window.CyberNews && window.CyberNews.mockEntities;
    var getTypeLabel = mock ? mock.getEntityTypeLabel.bind(mock) : function(tp) { return tp || 'Entity'; };
    resultsList.innerHTML = results.map(function(e) {
      var typeLabel = getTypeLabel(e.type);
      var desc = (e.description || '').slice(0, 160);
      if (e.description && e.description.length > 160) desc += '\u2026';
      var href = 'entity.html?id=' + encodeURIComponent(e.id);
      return (
        '<a href="' + href + '" class="search-result-card">' +
          '<div class="result-name">' + escapeHtml(e.name) + '</div>' +
          '<div class="result-meta"><span class="result-type">' + escapeHtml(typeLabel) + '</span></div>' +
          '<div class="result-description">' + escapeHtml(desc) + '</div>' +
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

  function runSearch(query) {
    lastQuery = query;
    var q = (query || '').trim();
    if (!q) {
      showState('empty', 'search.emptyTitle', 'search.emptyMsg');
      return;
    }
    showState('loading', null, null);
    var mock = window.CyberNews && window.CyberNews.mockEntities;
    if (!mock || !mock.searchEntities) {
      showResults([]);
      return;
    }
    mock.searchEntities(q)
      .then(function(data) {
        var results = data.results || [];
        showResults(results, data.query);
        trackEvent('search', {
          query: q,
          source: 'search_page',
          results_count: results.length,
          status: results.length ? 'results' : 'empty',
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
      var url = 'search.html' + (q ? '?q=' + encodeURIComponent(q) : '');
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
