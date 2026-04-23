/**
 * Cluster drawer: slide-in panel that fetches and renders cluster detail
 * without leaving the feed page.
 *
 * Public API: window.ClusterDrawer.open(clusterId), window.ClusterDrawer.close()
 */
(function () {
  'use strict';

  // ── Helpers (mirrors cluster-page.js) ──────────────────────────────────
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
    } catch (e) { return isoStr; }
  }

  // ── Translation helpers ────────────────────────────────────────────────
  // Wrap translatable text in a <span data-drawer-tx> so it can be retranslated
  // later (e.g. when the user switches language while the drawer is open).
  function tx(text) {
    return '<span data-drawer-tx>' + esc(text) + '</span>';
  }

  function currentLang() {
    return window.currentLanguage || 'en';
  }

  function canTranslate() {
    var lang = currentLang();
    return lang && lang !== 'en' && window.Translator && window.Translator.isSupported(lang);
  }

  function applyDrawerTranslations() {
    if (!drawer) return;
    var lang = currentLang();
    var nodes = drawer.querySelectorAll('[data-drawer-tx]');
    nodes.forEach(function (n) {
      var orig = n.getAttribute('data-drawer-orig');
      if (orig === null) {
        orig = n.textContent;
        n.setAttribute('data-drawer-orig', orig);
      }
      if (!canTranslate()) {
        n.textContent = orig;
        return;
      }
      window.Translator.translateOne(orig, lang).then(function (translated) {
        if (n.getAttribute('data-drawer-orig') === orig) {
          n.textContent = translated || orig;
        }
      });
    });
  }

  // ── DOM creation ───────────────────────────────────────────────────────
  var backdrop, drawer, drawerBody, fullLink, resizeHandle;

  function positionResizeHandle() {
    if (!resizeHandle || !drawer) return;
    var left = window.innerWidth - drawer.offsetWidth;
    resizeHandle.style.left = (left - 6) + 'px';
  }

  function buildDOM() {
    if (backdrop) return; // already built

    // Inject stylesheets
    var link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/static/css/components/cluster-drawer.css?v=3';
    document.head.appendChild(link);

    var link2 = document.createElement('link');
    link2.rel = 'stylesheet';
    link2.href = '/static/css/pages/cluster.css';
    document.head.appendChild(link2);

    backdrop = document.createElement('div');
    backdrop.className = 'cluster-drawer-backdrop';
    backdrop.addEventListener('click', close);

    drawer = document.createElement('div');
    drawer.className = 'cluster-drawer';
    drawer.setAttribute('role', 'dialog');
    drawer.setAttribute('aria-modal', 'true');
    drawer.setAttribute('aria-label', 'Cluster detail');

    fullLink = document.createElement('a');
    fullLink.className = 'cluster-drawer-full-link';
    fullLink.target = '_blank';
    fullLink.rel = 'noopener';
    fullLink.innerHTML = '<i class="fas fa-external-link-alt"></i> ' + tx('Open full page');

    var closeBtn = document.createElement('button');
    closeBtn.className = 'cluster-drawer-close';
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', 'Close');
    closeBtn.innerHTML = '<i class="fas fa-times"></i>';
    closeBtn.addEventListener('click', close);

    var header = document.createElement('div');
    header.className = 'cluster-drawer-header';
    header.appendChild(fullLink);
    header.appendChild(closeBtn);

    drawerBody = document.createElement('div');
    drawerBody.className = 'cluster-drawer-body';

    drawer.appendChild(header);
    drawer.appendChild(drawerBody);

    // Resize handle — body-level fixed element, avoids stacking context issues
    resizeHandle = document.createElement('div');
    resizeHandle.className = 'cluster-drawer-resize';

    document.body.appendChild(backdrop);
    document.body.appendChild(drawer);
    document.body.appendChild(resizeHandle);

    resizeHandle.addEventListener('pointerdown', function (e) {
      e.preventDefault();
      resizeHandle.setPointerCapture(e.pointerId);
      document.documentElement.style.cursor = 'ew-resize';
      document.documentElement.style.userSelect = 'none';
    });
    resizeHandle.addEventListener('pointermove', function (e) {
      if (!resizeHandle.hasPointerCapture(e.pointerId)) return;
      var newWidth = window.innerWidth - e.clientX;
      newWidth = Math.max(360, Math.min(newWidth, Math.floor(window.innerWidth * 0.75)));
      drawer.style.width = newWidth + 'px';
      positionResizeHandle();
    });
    resizeHandle.addEventListener('pointerup', function () {
      document.documentElement.style.cursor = '';
      document.documentElement.style.userSelect = '';
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') close();
    });

    // Retranslate drawer contents when the user switches language
    window.addEventListener('cybernews:languageChange', function () {
      if (drawer && drawer.classList.contains('is-open')) applyDrawerTranslations();
    });
  }

  // ── State helpers ───────────────────────────────────────────────────────
  function showLoading() {
    drawerBody.innerHTML =
      '<div class="cluster-drawer-state">' +
        '<div class="cluster-drawer-spinner"></div>' +
        '<p class="cluster-drawer-state-msg">' + tx('Loading…') + '</p>' +
      '</div>';
    applyDrawerTranslations();
  }

  function showError(msg) {
    drawerBody.innerHTML =
      '<div class="cluster-drawer-state">' +
        '<div class="cluster-state-icon"><i class="fas fa-exclamation-triangle"></i></div>' +
        '<p class="cluster-drawer-state-title">' + tx('Failed to load') + '</p>' +
        '<p class="cluster-drawer-state-msg">' + tx(msg || '') + '</p>' +
      '</div>';
    applyDrawerTranslations();
  }

  function showNotFound() {
    drawerBody.innerHTML =
      '<div class="cluster-drawer-state">' +
        '<div class="cluster-state-icon"><i class="fas fa-search"></i></div>' +
        '<p class="cluster-drawer-state-title">' + tx('Cluster not found') + '</p>' +
        '<p class="cluster-drawer-state-msg">' + tx('This cluster may have been removed.') + '</p>' +
      '</div>';
    applyDrawerTranslations();
  }

  // ── Render ──────────────────────────────────────────────────────────────
  function renderCluster(cluster) {
    // Badges
    var stateLabels = { 'new': 'New', developing: 'Developing', confirmed: 'Confirmed', resolved: 'Resolved' };
    var stateLabel = stateLabels[cluster.state] || (cluster.state || '');
    var badgesHtml = '<span class="cluster-state cluster-state-' + esc(cluster.state) + '">' + tx(stateLabel) + '</span>';

    if (cluster.confidence) {
      var confIcons = { high: 'fas fa-shield-alt', medium: 'fas fa-adjust', low: 'fas fa-question-circle' };
      var icon = confIcons[cluster.confidence] || 'fas fa-circle';
      badgesHtml += '<span class="cluster-confidence"><i class="' + icon + '"></i> ' + tx(cluster.confidence + ' confidence') + '</span>';
    }
    if (cluster.categories && cluster.categories.length > 0) {
      cluster.categories.forEach(function (cat) {
        badgesHtml += '<span class="cluster-category-pill">' + esc(cat) + '</span>';
      });
    }

    // Meta (labels are translated, values — dates / numbers — stay as-is)
    var metaParts = [];
    if (cluster.earliest_at) metaParts.push('<span><i class="far fa-clock"></i> ' + tx('First seen') + ' ' + esc(formatDate(cluster.earliest_at)) + '</span>');
    if (cluster.latest_at)   metaParts.push('<span><i class="fas fa-sync-alt"></i> ' + tx('Updated') + ' ' + esc(timeAgo(cluster.latest_at)) + '</span>');
    if (cluster.score != null) metaParts.push('<span><i class="fas fa-fire"></i> ' + tx('Score') + ' ' + Number(cluster.score).toFixed(1) + '</span>');
    var articleCount = cluster.articles ? cluster.articles.length : 0;
    if (articleCount > 0) {
      var srcLabel = articleCount !== 1 ? 'sources' : 'source';
      metaParts.push('<span><i class="fas fa-layer-group"></i> ' + articleCount + ' ' + tx(srcLabel) + '</span>');
    }

    // Summary
    var summaryHtml = cluster.summary
      ? '<p class="cluster-summary-text">' + tx(cluster.summary) + '</p>'
      : '<p class="cluster-empty-state">' + tx('Summary not yet available — analysis in progress.') + '</p>';

    // Why it matters
    var whyHtml = cluster.why_it_matters
      ? '<p class="cluster-why-text">' + tx(cluster.why_it_matters) + '</p>'
      : '<p class="cluster-empty-state">' + tx('Impact analysis not yet available — check back soon.') + '</p>';

    // Tags (cap at 8 to avoid overwhelming the drawer)
    var tagsSection = '';
    if (cluster.tags && cluster.tags.length > 0) {
      var visibleTags = cluster.tags.slice(0, 8);
      var hiddenCount = cluster.tags.length - visibleTags.length;
      var tagBadges = visibleTags.map(function (t) { return '<span class="cluster-tag">' + esc(t) + '</span>'; }).join('');
      if (hiddenCount > 0) tagBadges += '<span class="cluster-tag" style="color:var(--text-muted)">+' + hiddenCount + ' ' + tx('more') + '</span>';
      tagsSection =
        '<div class="cluster-section">' +
          '<div class="cluster-section-title"><i class="fas fa-tags"></i> ' + tx('Tags') + '</div>' +
          '<div class="cluster-tags">' + tagBadges + '</div>' +
        '</div>';
    }

    // Sources
    var articles = cluster.articles || [];
    var sourcesHtml = articles.length === 0
      ? '<p class="cluster-empty-state">' + tx('No source articles available.') + '</p>'
      : articles.map(function (a) {
          var url = a.source_url || '#';
          return '<a class="cluster-source-item" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' +
            '<div class="cluster-source-body">' +
              '<div class="cluster-source-title">' + tx(a.title || 'Untitled') + '</div>' +
              (a.source_name ? '<div class="cluster-source-name">' + esc(a.source_name) + '</div>' : '') +
            '</div>' +
            '<div class="cluster-source-time"><i class="far fa-clock"></i> ' + esc(timeAgo(a.published_at)) + '</div>' +
          '</a>';
        }).join('');

    drawerBody.innerHTML =
      '<div class="cluster-header">' +
        '<div class="cluster-badges">' + badgesHtml + '</div>' +
        '<h2 class="cluster-title">' + tx(cluster.label || 'Untitled cluster') + '</h2>' +
        '<div class="cluster-meta-row">' + metaParts.join('') + '</div>' +
      '</div>' +
      '<div class="cluster-section">' +
        '<div class="cluster-section-title"><i class="fas fa-align-left"></i> ' + tx('TL;DR') + '</div>' +
        summaryHtml +
      '</div>' +
      '<div class="cluster-section">' +
        '<div class="cluster-section-title"><i class="fas fa-exclamation-circle"></i> ' + tx('Why it matters') + '</div>' +
        whyHtml +
      '</div>' +
      tagsSection +
      '<div class="cluster-section">' +
        '<div class="cluster-section-title"><i class="fas fa-newspaper"></i> ' + tx('Sources') + ' <span style="font-size:0.85rem;font-weight:400;color:var(--text-muted);margin-left:0.3rem;">(' + articles.length + ')</span></div>' +
        '<div class="cluster-sources-list">' + sourcesHtml + '</div>' +
      '</div>';

    applyDrawerTranslations();
  }

  // ── Public API ──────────────────────────────────────────────────────────
  function open(clusterId) {
    buildDOM();

    // Update "open full page" link
    fullLink.href = '/cluster?id=' + encodeURIComponent(clusterId);

    // Lock scroll on both html and body; compensate width to avoid layout shift
    var scrollbarWidth = window.innerWidth - document.documentElement.clientWidth;
    document.documentElement.style.overflow = 'hidden';
    document.documentElement.style.paddingRight = scrollbarWidth + 'px';
    backdrop.classList.add('is-open');
    drawer.classList.add('is-open');
    drawerBody.scrollTop = 0;
    // Defer handle positioning one frame so CSS width is applied before measuring
    requestAnimationFrame(function () {
      positionResizeHandle();
      resizeHandle.style.display = 'block';
    });

    showLoading();

    fetch('/api/clusters/' + encodeURIComponent(clusterId))
      .then(function (r) {
        if (r.status === 404) { showNotFound(); return null; }
        if (!r.ok) throw new Error('API returned ' + r.status);
        return r.json();
      })
      .then(function (cluster) {
        if (cluster) renderCluster(cluster);
      })
      .catch(function (err) {
        showError(err.message || 'Could not fetch cluster data.');
      });
  }

  function close() {
    if (!backdrop) return;
    backdrop.classList.remove('is-open');
    drawer.classList.remove('is-open');
    resizeHandle.style.display = 'none';
    document.documentElement.style.overflow = '';
    document.documentElement.style.paddingRight = '';
  }

  window.ClusterDrawer = { open: open, close: close };
})();
