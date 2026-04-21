/**
 * News grid: fetches clusters from /api/feed, builds cards, infinite scroll.
 * Depends on: priority-filter.js (globals: mainFilterTime, currentSort)
 */
(function () {
  'use strict';

  // ── State ──
  var PAGE_SIZE = 10;
  var offset = 0;
  var total = 0;
  var loading = false;
  var abortCtrl = null;
  var loadedClusterList = [];
  window.loadedClusterList = loadedClusterList;

  // ── DOM refs ──
  var newsGrid = document.getElementById('newsGrid');
  var loadIndicator = document.getElementById('loadIndicator');
  var feedEmpty = document.getElementById('feedEmpty');
  var feedError = document.getElementById('feedError');
  var feedRetryBtn = document.getElementById('feedRetryBtn');

  // ── Category mapping (URL param -> API category value) ──
  var CATEGORY_MAP = {
    'threat-intel': 'deep-dives',
    'apt': 'deep-dives',
    'pentest': 'deep-dives',
    'malware': 'research',
    'breaches': 'dark-web',
    'bug-bounty': 'beginner',
    'deep-dives': 'deep-dives',
    'beginner': 'beginner',
    'research': 'research',
    'dark-web': 'dark-web',
    'breaking': 'breaking'
  };

  function getCategory() {
    var params = new URLSearchParams(location.search);
    var urlCat = params.get('category');
    if (!urlCat) return null;
    return CATEGORY_MAP[urlCat] || null;
  }

  // ── Relative time helper ──
  function timeAgo(isoStr) {
    if (!isoStr) return '';
    var diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
    if (diff < 0) diff = 0;
    if (diff < 60) return Math.floor(diff) + 's';
    if (diff < 3600) return Math.floor(diff / 60) + 'm';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h';
    return Math.floor(diff / 86400) + 'd';
  }

  // ── Compute date_from from time filter value ──
  function dateFromFilter() {
    var tf = window.mainFilterTime || '24h';
    if (tf === 'all') return null;
    var ms = { '1h': 3600000, '24h': 86400000, '7d': 604800000 };
    var delta = ms[tf] || 86400000;
    return new Date(Date.now() - delta).toISOString();
  }

  // ── Build query params ──
  function buildParams(pageOffset) {
    var params = new URLSearchParams();
    params.set('view', 'global');
    params.set('sort', window.currentSort || 'latest');
    params.set('limit', String(PAGE_SIZE));
    params.set('offset', String(pageOffset));
    var cat = getCategory();
    if (cat) params.set('category', cat);
    var df = dateFromFilter();
    if (df) params.set('date_from', df);
    return params;
  }

  // ── Fetch a page of clusters ──
  async function fetchPage(pageOffset) {
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();

    var url = '/api/feed?' + buildParams(pageOffset).toString();
    var resp = await fetch(url, { signal: abortCtrl.signal });
    if (!resp.ok) throw new Error('API returned ' + resp.status);
    return resp.json();
  }

  // ── Escape HTML to prevent XSS (safe for element content and attributes) ──
  function esc(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // ── Build a card from a ClusterSummary ──
  function buildCard(cluster, index) {
    var a = cluster.top_article || {};
    var tags = a.tags || [];
    var keywords = a.keywords || [];
    var severity = a.severity;

    var card = document.createElement('article');
    card.className = 'news-card';
    card.setAttribute('data-cluster-id', cluster.id || '');
    card.style.animationDelay = (index % 12) * 0.03 + 's';

    var t = (window.CyberNews && window.CyberNews.t) ? window.CyberNews.t.bind(window.CyberNews) : function(k) { return k; };

    // Priority badges: severity, state, category (always shown, up to 3 slots)
    var prioritySpans = [];
    var sevLabels = { critical: t('severity.critical'), high: t('severity.high'), medium: t('severity.medium'), low: t('severity.low') };
    var sevIcons = { critical: 'fas fa-skull-crossbones', high: 'fas fa-exclamation-triangle', medium: 'fas fa-exclamation-circle', low: 'fas fa-info-circle' };
    if (severity && sevLabels[severity]) {
      prioritySpans.push('<span class="card-tag sev-' + esc(severity) + '"><i class="' + sevIcons[severity] + '"></i> ' + sevLabels[severity] + '</span>');
    }
    var stateLabels = { 'new': t('cluster.state.new'), developing: t('cluster.state.developing'), confirmed: t('cluster.state.confirmed'), resolved: t('cluster.state.resolved') };
    if (cluster.state && stateLabels[cluster.state]) {
      prioritySpans.push('<span class="card-tag cluster-state-' + esc(cluster.state) + '">' + stateLabels[cluster.state] + '</span>');
    }
    if (cluster.categories && cluster.categories.length > 0) {
      prioritySpans.push('<span class="card-tag card-category">' + esc(cluster.categories[0]) + '</span>');
    }

    // Tags fill remaining slots up to a total of 6
    var maxTags = Math.max(0, 5 - prioritySpans.length);
    var tagSpans = tags.slice(0, maxTags).map(function (tag) {
      var c = tag.toLowerCase().replace(/[^a-z0-9-]/g, '');
      return '<span class="card-tag ' + c + '">' + esc(tag) + '</span>';
    }).join('') + prioritySpans.join('');

    // Keywords
    var keywordSpans = keywords.slice(0, 3).map(function (k, i) {
      var cl = i === 0 ? 'card-keyword highlight' : 'card-keyword';
      return '<span class="' + cl + '">' + esc(k) + '</span>';
    }).join('');

    // Source count badge
    var sourceCountHtml = '';
    if (cluster.article_count > 1) {
      var sourcesLabel = (window.CyberNews && window.CyberNews.t) ? window.CyberNews.t('card.sources') : 'sources';
      sourceCountHtml = '<span class="card-sources"><i class="fas fa-layer-group"></i> ' + cluster.article_count + ' ' + sourcesLabel + '</span>';
    }

    // Score (shown when sorting by score)
    var scoreHtml = '';
    if ((window.currentSort === 'score') && cluster.score != null) {
      scoreHtml = '<span class="card-score"><i class="fas fa-fire"></i> ' + Number(cluster.score).toFixed(1) + '</span>';
    }

    var readLabel = (window.CyberNews && window.CyberNews.t) ? window.CyberNews.t('card.read') : 'Read';

    var rawTitle = a.title || '';
    var rawDesc  = (function (s) {
      var limit = 150;
      s = s || '';
      return s.length > limit ? s.slice(0, limit).trimEnd() + '…' : s;
    })(a.desc);

    card.setAttribute('data-orig-title', rawTitle);
    card.setAttribute('data-orig-desc', rawDesc);
    card.innerHTML =
      '<div class="card-tags">' + tagSpans + '</div>' +
      '<h3 class="card-title">' + esc(rawTitle) + '</h3>' +
      '<p class="card-desc">' + esc(rawDesc) + '</p>' +
      '<div class="card-keywords">' + keywordSpans + '</div>' +
      '<div class="card-meta">' +
        '<span><i class="far fa-clock"></i> ' + timeAgo(a.published_at) + '</span>' +
        (a.source_name ? '<span class="card-source-name">' + esc(a.source_name) + '</span>' : '') +
        sourceCountHtml +
        scoreHtml +
      '</div>';

    return card;
  }

  // ── Show/hide state containers ──
  function showState(state) {
    if (feedEmpty) feedEmpty.hidden = (state !== 'empty');
    if (feedError) feedError.hidden = (state !== 'error');
    if (loadIndicator) loadIndicator.classList.toggle('hidden', state !== 'loading');
  }

  // ── Load a page and render ──
  async function loadPage(append) {
    if (loading && append) return; // only guard scroll-appends, not full refreshes
    loading = true;

    if (!append) {
      offset = 0;
      loadedClusterList.length = 0;
      if (newsGrid) newsGrid.innerHTML = '';
    }

    showState('loading');

    try {
      var data = await fetchPage(offset);
      total = data.total || 0;
      var items = data.items || [];

      if (!append && items.length === 0) {
        showState('empty');
        loading = false;
        return;
      }

      if (feedEmpty) feedEmpty.hidden = true;
      if (feedError) feedError.hidden = true;

      var newCards = [];
      items.forEach(function (cluster, i) {
        loadedClusterList.push(cluster);
        var card = buildCard(cluster, offset + i);
        newCards.push(card);
        if (newsGrid) newsGrid.appendChild(card);
      });

      offset += items.length;

      // Translate newly added cards if a non-English language is active
      var activeLang = window.currentLanguage || 'en';
      translateCards(newCards, activeLang);

      // Show spinner if more pages remain, hide if all loaded
      if (loadIndicator) {
        loadIndicator.classList.toggle('hidden', offset >= total);
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        loading = false;
        return;
      }
      console.error('[feed]', err);
      if (!append && loadedClusterList.length === 0) {
        showState('error');
      }
    }

    loading = false;
  }

  // ── Infinite scroll (debounced) ──
  var scrollTimer = null;
  var SCROLL_THRESHOLD = 400;
  function onScroll() {
    if (scrollTimer) return;
    scrollTimer = setTimeout(function () {
      scrollTimer = null;
      if (loading || offset >= total) return;
      if (!loadIndicator) return;
      var rect = loadIndicator.getBoundingClientRect();
      if (rect.top < window.innerHeight + SCROLL_THRESHOLD) {
        loadPage(true);
      }
    }, 200);
  }
  window.addEventListener('scroll', onScroll, { passive: true });

  // ── Public: full refresh (called by filter changes) ──
  window.refreshFeed = function () {
    loadPage(false);
  };


  // ── Retry button ──
  if (feedRetryBtn) {
    feedRetryBtn.addEventListener('click', function () {
      loadPage(false);
    });
  }

  // ── Translate a set of card elements into the given language ──
  function translateCards(cards, lang) {
    if (!lang || lang === 'en' || !window.Translator || !window.Translator.isSupported(lang)) return;
    cards.forEach(function (card) {
      var titleEl = card.querySelector('.card-title');
      var descEl  = card.querySelector('.card-desc');
      var origTitle = card.getAttribute('data-orig-title') || '';
      var origDesc  = card.getAttribute('data-orig-desc')  || '';
      if (titleEl && origTitle) {
        titleEl.style.opacity = '0.5';
        window.Translator.translateOne(origTitle, lang).then(function (tx) {
          titleEl.textContent = tx;
          titleEl.style.opacity = '';
        });
      }
      if (descEl && origDesc) {
        window.Translator.translateOne(origDesc, lang).then(function (tx) {
          descEl.textContent = tx;
        });
      }
    });
  }

  // ── Public: retranslate all rendered cards (called by language switcher) ──
  window.translateAllNewsCards = function (lang) {
    var cards = newsGrid ? Array.prototype.slice.call(newsGrid.querySelectorAll('.news-card')) : [];
    if (!lang || lang === 'en') {
      cards.forEach(function (card) {
        var titleEl = card.querySelector('.card-title');
        var descEl  = card.querySelector('.card-desc');
        var t = card.getAttribute('data-orig-title') || '';
        var d = card.getAttribute('data-orig-desc')  || '';
        if (titleEl) titleEl.textContent = t;
        if (descEl)  descEl.textContent  = d;
      });
    } else {
      translateCards(cards, lang);
    }
  };

  // ── Initial load ──
  loadPage(false);
})();
