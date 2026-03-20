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

    // Tags
    var tagSpans = tags.map(function (t) {
      var c = t.toLowerCase().replace(/\s/g, '');
      return '<span class="card-tag ' + esc(c) + '">' + esc(t) + '</span>';
    }).join('');

    // Severity badge
    var sevLabels = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low' };
    var sevIcons = { critical: 'fas fa-skull-crossbones', high: 'fas fa-exclamation-triangle', medium: 'fas fa-exclamation-circle', low: 'fas fa-info-circle' };
    if (severity && sevLabels[severity]) {
      tagSpans += '<span class="card-tag sev-' + esc(severity) + '"><i class="' + sevIcons[severity] + '"></i> ' + sevLabels[severity] + '</span>';
    }

    // Cluster state badge
    var stateLabels = { 'new': 'New', developing: 'Developing', confirmed: 'Confirmed', resolved: 'Resolved' };
    if (cluster.state && stateLabels[cluster.state]) {
      tagSpans += '<span class="card-tag cluster-state-' + esc(cluster.state) + '">' + stateLabels[cluster.state] + '</span>';
    }

    // Category pill (first cluster category)
    if (cluster.categories && cluster.categories.length > 0) {
      tagSpans += '<span class="card-tag card-category">' + esc(cluster.categories[0]) + '</span>';
    }

    // Keywords
    var keywordSpans = keywords.map(function (k, i) {
      var cl = i === 0 ? 'card-keyword highlight' : 'card-keyword';
      return '<span class="' + cl + '">' + esc(k) + '</span>';
    }).join('');

    // Source count badge
    var sourceCountHtml = '';
    if (cluster.article_count > 1) {
      sourceCountHtml = '<span class="card-sources"><i class="fas fa-layer-group"></i> ' + cluster.article_count + ' sources</span>';
    }

    // Score (shown when sorting by score)
    var scoreHtml = '';
    if ((window.currentSort === 'score') && cluster.score != null) {
      scoreHtml = '<span class="card-score"><i class="fas fa-fire"></i> ' + Number(cluster.score).toFixed(1) + '</span>';
    }

    var readLabel = (window.CyberNews && window.CyberNews.t) ? window.CyberNews.t('card.read') : 'Read';

    card.innerHTML =
      '<div class="card-tags">' + tagSpans + '</div>' +
      '<h3 class="card-title">' + esc(a.title) + '</h3>' +
      '<p class="card-desc">' + esc(a.desc || '') + '</p>' +
      '<div class="card-keywords">' + keywordSpans + '</div>' +
      '<div class="card-meta">' +
        '<span><i class="far fa-clock"></i> ' + timeAgo(a.published_at) + '</span>' +
        (a.source_name ? '<span class="card-source-name">' + esc(a.source_name) + '</span>' : '') +
        sourceCountHtml +
        scoreHtml +
        '<button class="card-read" data-cluster-id="' + esc(cluster.id || '') + '">' + readLabel + '</button>' +
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
    if (loading) return;
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

      showState('none');

      items.forEach(function (cluster, i) {
        loadedClusterList.push(cluster);
        if (newsGrid) newsGrid.appendChild(buildCard(cluster, offset + i));
      });

      offset += items.length;

      // Hide load indicator if we've loaded everything
      if (offset >= total) {
        if (loadIndicator) loadIndicator.classList.add('hidden');
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

  // ── Initial load ──
  loadPage(false);
})();
