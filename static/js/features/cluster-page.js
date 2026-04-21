/**
 * Cluster detail page: fetches GET /api/clusters/{id} on load, renders all fields.
 */
(function () {
  'use strict';

  // ── DOM refs ──
  var loadingEl   = document.getElementById('clusterLoading');
  var errorEl     = document.getElementById('clusterError');
  var errorMsgEl  = document.getElementById('clusterErrorMsg');
  var notFoundEl  = document.getElementById('clusterNotFound');
  var contentEl   = document.getElementById('clusterContent');
  var retryBtn    = document.getElementById('clusterRetryBtn');

  function showState(state) {
    if (loadingEl)  loadingEl.hidden  = (state !== 'loading');
    if (errorEl)    errorEl.hidden    = (state !== 'error');
    if (notFoundEl) notFoundEl.hidden = (state !== 'notfound');
    if (contentEl)  contentEl.hidden  = (state !== 'content');
  }

  function esc(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function timeAgo(isoStr) {
    if (!isoStr) return '';
    var diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
    if (diff < 0) diff = 0;
    if (diff < 60)    return Math.floor(diff) + 's ago';
    if (diff < 3600)  return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
  }

  function formatDate(isoStr) {
    if (!isoStr) return '';
    try {
      return new Date(isoStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    } catch (e) {
      return isoStr;
    }
  }

  function getClusterId() {
    var params = new URLSearchParams(location.search);
    return params.get('id') || '';
  }

  async function fetchCluster(id) {
    var resp = await fetch('/api/clusters/' + encodeURIComponent(id));
    if (resp.status === 404) return null;
    if (!resp.ok) throw new Error('API returned ' + resp.status);
    return resp.json();
  }

  function renderCluster(cluster) {
    // ── Badges ──
    var badgesEl = document.getElementById('clusterBadges');
    if (badgesEl) {
      var stateLabels = { 'new': 'New', developing: 'Developing', confirmed: 'Confirmed', resolved: 'Resolved' };
      var stateLabel = stateLabels[cluster.state] || esc(cluster.state);
      var badgesHtml = '<span class="cluster-state cluster-state-' + esc(cluster.state) + '">' + stateLabel + '</span>';

      if (cluster.confidence) {
        var confIcons = { high: 'fas fa-shield-alt', medium: 'fas fa-adjust', low: 'fas fa-question-circle' };
        var icon = confIcons[cluster.confidence] || 'fas fa-circle';
        badgesHtml += '<span class="cluster-confidence"><i class="' + icon + '"></i> ' + esc(cluster.confidence) + ' confidence</span>';
      }

      if (cluster.categories && cluster.categories.length > 0) {
        cluster.categories.forEach(function (cat) {
          badgesHtml += '<span class="cluster-category-pill">' + esc(cat) + '</span>';
        });
      }

      badgesEl.innerHTML = badgesHtml;
    }

    // ── Title ──
    var titleEl = document.getElementById('clusterTitle');
    if (titleEl) titleEl.textContent = cluster.label || 'Untitled cluster';

    // ── Meta row ──
    var metaEl = document.getElementById('clusterMeta');
    if (metaEl) {
      var metaParts = [];
      if (cluster.earliest_at) {
        metaParts.push('<span><i class="far fa-clock"></i> First seen ' + esc(formatDate(cluster.earliest_at)) + '</span>');
      }
      if (cluster.latest_at) {
        metaParts.push('<span><i class="fas fa-sync-alt"></i> Updated ' + esc(timeAgo(cluster.latest_at)) + '</span>');
      }
      if (cluster.score != null) {
        metaParts.push('<span><i class="fas fa-fire"></i> Score ' + Number(cluster.score).toFixed(1) + '</span>');
      }
      var articleCount = cluster.articles ? cluster.articles.length : 0;
      if (articleCount > 0) {
        metaParts.push('<span><i class="fas fa-layer-group"></i> ' + articleCount + ' source' + (articleCount !== 1 ? 's' : '') + '</span>');
      }
      metaEl.innerHTML = metaParts.join('');
    }

    // ── TL;DR ──
    var summaryEl = document.getElementById('clusterSummary');
    if (summaryEl) {
      if (cluster.summary) {
        summaryEl.innerHTML = '<p class="cluster-summary-text">' + esc(cluster.summary) + '</p>';
      } else {
        summaryEl.innerHTML = '<p class="cluster-empty-state">Summary not yet available — analysis in progress.</p>';
      }
    }

    // ── Why it matters ──
    var whyEl = document.getElementById('clusterWhy');
    if (whyEl) {
      if (cluster.why_it_matters) {
        whyEl.innerHTML = '<p class="cluster-why-text">' + esc(cluster.why_it_matters) + '</p>';
      } else {
        whyEl.innerHTML = '<p class="cluster-empty-state">Impact analysis not yet available — check back soon.</p>';
      }
    }

    // ── Tags ──
    var tagsEl = document.getElementById('clusterTags');
    var tagsSectionEl = document.getElementById('clusterTagsSection');
    if (tagsEl && cluster.tags && cluster.tags.length > 0) {
      tagsEl.innerHTML = cluster.tags.slice(0, 6).map(function (t) {
        return '<span class="cluster-tag">' + esc(t) + '</span>';
      }).join('');
    } else if (tagsSectionEl) {
      tagsSectionEl.hidden = true;
    }

    // ── Sources ──
    var sourcesEl = document.getElementById('clusterSources');
    var sourceCountEl = document.getElementById('clusterSourceCount');
    if (sourcesEl) {
      var articles = cluster.articles || [];
      if (sourceCountEl) sourceCountEl.textContent = '(' + articles.length + ')';

      if (articles.length === 0) {
        sourcesEl.innerHTML = '<p class="cluster-empty-state">No source articles available.</p>';
      } else {
        sourcesEl.innerHTML = articles.map(function (a) {
          var url = a.source_url || '#';
          return '<a class="cluster-source-item" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' +
            '<div class="cluster-source-body">' +
              '<div class="cluster-source-title">' + esc(a.title || 'Untitled') + '</div>' +
              (a.source_name ? '<div class="cluster-source-name">' + esc(a.source_name) + '</div>' : '') +
            '</div>' +
            '<div class="cluster-source-time"><i class="far fa-clock"></i> ' + esc(timeAgo(a.published_at)) + '</div>' +
          '</a>';
        }).join('');
      }
    }

    // ── Update page title ──
    document.title = esc(cluster.label) + ' — avild.news';

    showState('content');
  }

  async function load() {
    var id = getClusterId();
    if (!id) {
      showState('notfound');
      return;
    }

    showState('loading');
    try {
      var cluster = await fetchCluster(id);
      if (!cluster) {
        showState('notfound');
        return;
      }
      renderCluster(cluster);
    } catch (err) {
      console.error('[cluster-page]', err);
      if (errorMsgEl) errorMsgEl.textContent = err.message || 'Could not fetch cluster data.';
      showState('error');
    }
  }

  if (retryBtn) {
    retryBtn.addEventListener('click', load);
  }

  load();
})();
