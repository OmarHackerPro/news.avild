# Cluster Drawer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace card navigation with a slide-in drawer that shows cluster detail inline on the feed page, keeping the user on the same page.

**Architecture:** A self-contained `cluster-drawer.js` creates the drawer DOM on first open, fetches `/api/clusters/{id}`, and renders identical content to the existing cluster detail page. Card clicks trigger the drawer via a global `window.ClusterDrawer.open(id)` API. The Read button is removed from cards. The `/cluster` standalone page is left untouched.

**Tech Stack:** Vanilla JS, CSS custom properties (already defined in `main.css`), existing Font Awesome icons.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `static/css/components/cluster-drawer.css` | Drawer + backdrop styles, slide animation, responsive |
| Create | `static/js/features/cluster-drawer.js` | DOM creation, open/close, fetch, render — exposes `window.ClusterDrawer` |
| Modify | `static/js/features/news-modal.js` | Call `window.ClusterDrawer.open(id)` instead of navigating |
| Modify | `static/js/features/news-grid.js` | Remove Read button from `buildCard` |
| Modify | `static/js/core/loader.js` | Add `cluster-drawer.css` link and load `cluster-drawer.js` before `news-modal.js` |

---

## Task 1: Drawer CSS

**Files:**
- Create: `static/css/components/cluster-drawer.css`

- [ ] **Step 1: Create the stylesheet**

```css
/* ===== Cluster Drawer ===== */

/* Backdrop */
.cluster-drawer-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  z-index: 900;
  opacity: 0;
  transition: opacity 0.25s ease;
  pointer-events: none;
}
.cluster-drawer-backdrop.is-open {
  opacity: 1;
  pointer-events: auto;
}

/* Drawer panel */
.cluster-drawer {
  position: fixed;
  top: 0;
  right: 0;
  height: 100%;
  width: 520px;
  max-width: 100vw;
  background: var(--bg-card);
  border-left: 1px solid var(--border);
  z-index: 901;
  display: flex;
  flex-direction: column;
  transform: translateX(100%);
  transition: transform 0.28s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
}
.cluster-drawer.is-open {
  transform: translateX(0);
}

/* Drawer header bar */
.cluster-drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.875rem 1.25rem;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  gap: 0.75rem;
}
.cluster-drawer-full-link {
  font-size: 0.8125rem;
  color: var(--accent-blue);
  text-decoration: none;
  display: flex;
  align-items: center;
  gap: 0.35rem;
  white-space: nowrap;
}
.cluster-drawer-full-link:hover { color: var(--accent-green); }

.cluster-drawer-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  border-radius: var(--radius-sm);
  font-size: 1rem;
  transition: color var(--transition), background var(--transition);
  flex-shrink: 0;
}
.cluster-drawer-close:hover {
  color: var(--text);
  background: var(--bg-elevated);
}

/* Scrollable body */
.cluster-drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 1.5rem 1.25rem;
}

/* States */
.cluster-drawer-state {
  text-align: center;
  padding: 3rem 1rem;
}
.cluster-drawer-spinner {
  display: inline-block;
  width: 28px;
  height: 28px;
  border: 3px solid var(--border);
  border-top-color: var(--accent-green);
  border-radius: 50%;
  animation: cluster-drawer-spin 0.7s linear infinite;
  margin-bottom: 0.75rem;
}
@keyframes cluster-drawer-spin { to { transform: rotate(360deg); } }

.cluster-drawer-state-msg {
  font-size: 0.9rem;
  color: var(--text-muted);
  margin: 0.4rem 0 1rem;
}
.cluster-drawer-state-title {
  font-size: 1rem;
  font-weight: 600;
  margin-bottom: 0.35rem;
}

/* Content — reuse cluster-page classes where possible */
.cluster-drawer-body .cluster-header { margin-bottom: 1.25rem; }
.cluster-drawer-body .cluster-badges { margin-bottom: 0.6rem; }
.cluster-drawer-body .cluster-title { font-size: 1.35rem; }
.cluster-drawer-body .cluster-meta-row { margin-bottom: 1.25rem; }
.cluster-drawer-body .cluster-section { margin-bottom: 1.5rem; }

@media (max-width: 600px) {
  .cluster-drawer { width: 100vw; }
}
```

- [ ] **Step 2: Verify the file exists**

```bash
ls static/css/components/cluster-drawer.css
```
Expected: file listed.

---

## Task 2: Drawer JS

**Files:**
- Create: `static/js/features/cluster-drawer.js`

The module creates the drawer DOM once, exposes `window.ClusterDrawer.open(id)` and `window.ClusterDrawer.close()`. Rendering logic is ported from `cluster-page.js` — same `esc`, `timeAgo`, `formatDate`, `renderCluster` functions, adapted to write into the drawer body instead of fixed DOM IDs.

- [ ] **Step 1: Create the module**

```js
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

  // ── DOM creation ───────────────────────────────────────────────────────
  var backdrop, drawer, drawerBody, fullLink;

  function buildDOM() {
    if (backdrop) return; // already built

    // Inject stylesheet
    var link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/static/css/components/cluster-drawer.css';
    document.head.appendChild(link);

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
    fullLink.innerHTML = '<i class="fas fa-external-link-alt"></i> Open full page';

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

    document.body.appendChild(backdrop);
    document.body.appendChild(drawer);

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') close();
    });
  }

  // ── State helpers ───────────────────────────────────────────────────────
  function showLoading() {
    drawerBody.innerHTML =
      '<div class="cluster-drawer-state">' +
        '<div class="cluster-drawer-spinner"></div>' +
        '<p class="cluster-drawer-state-msg">Loading&hellip;</p>' +
      '</div>';
  }

  function showError(msg) {
    drawerBody.innerHTML =
      '<div class="cluster-drawer-state">' +
        '<div class="cluster-state-icon"><i class="fas fa-exclamation-triangle"></i></div>' +
        '<p class="cluster-drawer-state-title">Failed to load</p>' +
        '<p class="cluster-drawer-state-msg">' + esc(msg) + '</p>' +
      '</div>';
  }

  function showNotFound() {
    drawerBody.innerHTML =
      '<div class="cluster-drawer-state">' +
        '<div class="cluster-state-icon"><i class="fas fa-search"></i></div>' +
        '<p class="cluster-drawer-state-title">Cluster not found</p>' +
        '<p class="cluster-drawer-state-msg">This cluster may have been removed.</p>' +
      '</div>';
  }

  // ── Render ──────────────────────────────────────────────────────────────
  function renderCluster(cluster) {
    // Badges
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

    // Meta
    var metaParts = [];
    if (cluster.earliest_at) metaParts.push('<span><i class="far fa-clock"></i> First seen ' + esc(formatDate(cluster.earliest_at)) + '</span>');
    if (cluster.latest_at)   metaParts.push('<span><i class="fas fa-sync-alt"></i> Updated ' + esc(timeAgo(cluster.latest_at)) + '</span>');
    if (cluster.score != null) metaParts.push('<span><i class="fas fa-fire"></i> Score ' + Number(cluster.score).toFixed(1) + '</span>');
    var articleCount = cluster.articles ? cluster.articles.length : 0;
    if (articleCount > 0) metaParts.push('<span><i class="fas fa-layer-group"></i> ' + articleCount + ' source' + (articleCount !== 1 ? 's' : '') + '</span>');

    // Summary
    var summaryHtml = cluster.summary
      ? '<p class="cluster-summary-text">' + esc(cluster.summary) + '</p>'
      : '<p class="cluster-empty-state">Summary not yet available — analysis in progress.</p>';

    // Why it matters
    var whyHtml = cluster.why_it_matters
      ? '<p class="cluster-why-text">' + esc(cluster.why_it_matters) + '</p>'
      : '<p class="cluster-empty-state">Impact analysis not yet available — check back soon.</p>';

    // Tags
    var tagsSection = '';
    if (cluster.tags && cluster.tags.length > 0) {
      tagsSection =
        '<div class="cluster-section">' +
          '<div class="cluster-section-title"><i class="fas fa-tags"></i> Tags</div>' +
          '<div class="cluster-tags">' +
            cluster.tags.map(function (t) { return '<span class="cluster-tag">' + esc(t) + '</span>'; }).join('') +
          '</div>' +
        '</div>';
    }

    // Sources
    var articles = cluster.articles || [];
    var sourcesHtml = articles.length === 0
      ? '<p class="cluster-empty-state">No source articles available.</p>'
      : articles.map(function (a) {
          var url = a.source_url || '#';
          return '<a class="cluster-source-item" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' +
            '<div class="cluster-source-body">' +
              '<div class="cluster-source-title">' + esc(a.title || 'Untitled') + '</div>' +
              (a.source_name ? '<div class="cluster-source-name">' + esc(a.source_name) + '</div>' : '') +
            '</div>' +
            '<div class="cluster-source-time"><i class="far fa-clock"></i> ' + esc(timeAgo(a.published_at)) + '</div>' +
          '</a>';
        }).join('');

    drawerBody.innerHTML =
      '<div class="cluster-header">' +
        '<div class="cluster-badges">' + badgesHtml + '</div>' +
        '<h2 class="cluster-title">' + esc(cluster.label || 'Untitled cluster') + '</h2>' +
        '<div class="cluster-meta-row">' + metaParts.join('') + '</div>' +
      '</div>' +
      '<div class="cluster-section">' +
        '<div class="cluster-section-title"><i class="fas fa-align-left"></i> TL;DR</div>' +
        summaryHtml +
      '</div>' +
      '<div class="cluster-section">' +
        '<div class="cluster-section-title"><i class="fas fa-exclamation-circle"></i> Why it matters</div>' +
        whyHtml +
      '</div>' +
      tagsSection +
      '<div class="cluster-section">' +
        '<div class="cluster-section-title"><i class="fas fa-newspaper"></i> Sources <span style="font-size:0.85rem;font-weight:400;color:var(--text-muted);margin-left:0.3rem;">(' + articles.length + ')</span></div>' +
        '<div class="cluster-sources-list">' + sourcesHtml + '</div>' +
      '</div>';
  }

  // ── Public API ──────────────────────────────────────────────────────────
  function open(clusterId) {
    buildDOM();

    // Update "open full page" link
    fullLink.href = '/cluster?id=' + encodeURIComponent(clusterId);

    // Show panel
    backdrop.classList.add('is-open');
    drawer.classList.add('is-open');
    document.body.style.overflow = 'hidden';
    drawerBody.scrollTop = 0;

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
    document.body.style.overflow = '';
  }

  window.ClusterDrawer = { open: open, close: close };
})();
```

- [ ] **Step 2: Verify the file exists**

```bash
ls static/js/features/cluster-drawer.js
```
Expected: file listed.

---

## Task 3: Wire card clicks to the drawer

**Files:**
- Modify: `static/js/features/news-modal.js`

Replace the current navigation with a `ClusterDrawer.open()` call.

- [ ] **Step 1: Replace the file contents**

```js
/**
 * News card click: opens the cluster drawer for the clicked card.
 * Requires cluster-drawer.js to be loaded first (see loader.js).
 */
(function () {
  'use strict';

  var newsGrid = document.getElementById('newsGrid');
  if (!newsGrid) return;

  newsGrid.addEventListener('click', function (e) {
    var card = e.target && e.target.closest ? e.target.closest('.news-card') : null;
    if (!card) return;
    var clusterId = card.getAttribute('data-cluster-id') || '';
    if (!clusterId) return;
    if (window.ClusterDrawer) window.ClusterDrawer.open(clusterId);
  });
})();
```

---

## Task 4: Remove Read button from cards

**Files:**
- Modify: `static/js/features/news-grid.js`

Two changes: remove the button from `buildCard`'s HTML, and delete the Read button click handler.

- [ ] **Step 1: Remove the button from the card HTML**

In `buildCard` (around line 155–168), replace:

```js
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
```

With:

```js
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
      '</div>';
```

- [ ] **Step 2: Remove the Read button click handler**

Delete this block (around line 254–262):

```js
  // ── "Read" button → open source article in new tab ──
  if (newsGrid) {
    newsGrid.addEventListener('click', function (e) {
      var btn = e.target.closest('.card-read');
      if (!btn) return;
      e.stopPropagation();
      var id = btn.getAttribute('data-cluster-id');
      var cluster = loadedClusterList.find(function (x) { return (x.id || '') === id; });
      var url = cluster && cluster.top_article && cluster.top_article.source_url;
      if (url) window.open(url, '_blank', 'noopener');
    });
  }
```

---

## Task 5: Load drawer assets before news-modal.js

**Files:**
- Modify: `static/js/core/loader.js`

The drawer CSS is injected dynamically by `cluster-drawer.js` itself (no change needed there). We only need to ensure `cluster-drawer.js` loads before `news-modal.js` in `scriptOrder`.

- [ ] **Step 1: Insert cluster-drawer.js into the load order**

In `loader.js`, find `scriptOrder` and add `'features/cluster-drawer.js'` before `'features/news-modal.js'`:

```js
      var scriptOrder = [
        'data/translations.js',
        'components/nav.js',
        'components/language.js',
        'components/theme.js',
        'components/rss.js',
        'components/search-tooltip.js',
        'components/filters.js',
        'features/category-filter.js',
        'features/priority-filter.js',
        'features/news-grid.js',
        'features/cluster-drawer.js',
        'features/news-modal.js',
        'features/share-modal.js',
        'features/breaking.js',
        'features/sidebar.js'
      ];
```

---

## Task 6: Rebuild and verify

- [ ] **Step 1: Rebuild the frontend container**

```bash
docker compose build frontend && docker compose up -d frontend
```

Expected: `kiber-frontend-1 Started`

- [ ] **Step 2: Hard refresh and click a card**

Open `http://localhost`, press `Ctrl+Shift+R`, click any card body.
Expected: drawer slides in from the right with loading spinner, then cluster content.

- [ ] **Step 3: Verify close behaviors**

- Click the `×` button → drawer closes, feed scrolls again
- Click the backdrop (dark overlay) → drawer closes
- Press `Escape` → drawer closes

- [ ] **Step 4: Verify "Open full page" link**

In the open drawer, click "Open full page →".
Expected: `/cluster?id=<id>` opens in a new tab with the standalone cluster page.

- [ ] **Step 5: Verify no Read button exists on cards**

Inspect any card in DevTools — there should be no `.card-read` button element.

- [ ] **Step 6: Commit**

```bash
git add static/css/components/cluster-drawer.css \
        static/js/features/cluster-drawer.js \
        static/js/features/news-modal.js \
        static/js/features/news-grid.js \
        static/js/core/loader.js
git commit -m "Replace card navigation with inline cluster drawer"
```
