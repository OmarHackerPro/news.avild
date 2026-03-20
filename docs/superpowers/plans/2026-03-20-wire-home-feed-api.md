# Wire Home Feed to Real API (FE-03) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace mock data in the home feed with real `GET /api/feed` calls so the grid displays live cluster data from OpenSearch.

**Architecture:** The feed JS module (`news-grid.js`) becomes an API client that fetches `ClusterSummary[]` from `/api/feed`, builds cards from the `top_article` field on each cluster, and paginates via offset. Filters (time, category, sort) translate to API query params. Client-side filtering is removed entirely — the API handles it.

**Tech Stack:** Vanilla JS (no framework), Fetch API, AbortController, existing CSS classes.

**Spec:** `docs/superpowers/specs/2026-03-20-wire-home-feed-api-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `static/js/features/news-grid.js` | Rewrite | API client, card builder, infinite scroll, error/empty states |
| `static/js/features/priority-filter.js` | Modify | Hide dropped filters (priority pills, content type, sources), wire remaining filters to trigger API re-fetch |
| `static/js/features/news-modal.js` | Modify | Update to read from `window.loadedClusterList` instead of `window.loadedNewsList`, use cluster data shape |
| `static/js/components/filters.js` | Modify (minor) | Update `applyFilters` call to use new `window.refreshFeed()` |
| `static/partials/layout/content.html` | Modify | Add sort toggle markup, empty/error state containers |
| `static/css/components/news-grid.css` | Modify | Add styles for sort toggle, cluster state badges, source count, score badge, empty/error states |

---

### Task 1: Add sort toggle and state containers to content.html

**Files:**
- Modify: `static/partials/layout/content.html:43-98`

This task adds the HTML scaffolding that later tasks depend on: a sort toggle in the filter bar, and empty/error state containers in the grid area.

- [ ] **Step 1: Add sort toggle buttons after the filter-main-wrap div**

In `static/partials/layout/content.html`, find the closing `</div>` of `filter-main-wrap` (after the main filter dropdown). Add a sort toggle group right after it, still inside the `news-priority-bar` div:

```html
      <div class="sort-toggle">
        <button class="sort-btn active" data-sort="latest"><i class="far fa-clock"></i> Latest</button>
        <button class="sort-btn" data-sort="score"><i class="fas fa-fire"></i> Top</button>
      </div>
```

- [ ] **Step 2: Add empty state and error state containers after the newsGrid div**

In `static/partials/layout/content.html`, find `<div class="news-grid" id="newsGrid"></div>`. Add these containers right after it, before the `load-indicator` div:

```html
    <div class="feed-empty" id="feedEmpty" hidden>
      <i class="fas fa-inbox"></i>
      <p>No clusters found. Try adjusting your filters or check back later.</p>
    </div>
    <div class="feed-error" id="feedError" hidden>
      <i class="fas fa-exclamation-triangle"></i>
      <p>Failed to load feed.</p>
      <button class="feed-error-retry" id="feedRetryBtn">Retry</button>
    </div>
```

- [ ] **Step 3: Verify the HTML is well-formed**

Open `templates/index.html` in a browser (or via Docker) and verify the page still renders without errors. The sort toggle should appear (unstyled initially — it will use the same pill pattern as priority pills). The empty/error containers should be hidden.

- [ ] **Step 4: Commit**

```bash
git add static/partials/layout/content.html
git commit -m "Add sort toggle and feed state containers to content.html"
```

---

### Task 2: Add CSS for new card elements and feed states

**Files:**
- Modify: `static/css/components/news-grid.css`

Add styles for the sort toggle, cluster state badges, source count badge, score badge, and empty/error state containers. Follow the existing pattern in this file (CSS variables from `variables.css`, same sizing/spacing conventions).

- [ ] **Step 1: Add styles to the end of news-grid.css**

Append to `static/css/components/news-grid.css`:

```css
/* ── Sort toggle ── */
.sort-toggle {
  display: flex;
  gap: 4px;
  margin-left: auto;
}
.sort-btn {
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text-muted);
  padding: 4px 10px;
  font-size: 0.78rem;
  cursor: pointer;
  transition: all 0.15s;
}
.sort-btn:hover { color: var(--text-primary); border-color: var(--text-muted); }
.sort-btn.active { background: var(--accent-blue); color: white; border-color: var(--accent-blue); }

/* ── Cluster state badges ── */
.card-tag.cluster-state-new        { background: rgba(59,130,246,0.15); color: #60a5fa; border: 1px solid rgba(59,130,246,0.35); }
.card-tag.cluster-state-developing { background: rgba(251,191,36,0.12); color: #fbbf24; border: 1px solid rgba(251,191,36,0.3); }
.card-tag.cluster-state-confirmed  { background: rgba(34,197,94,0.15); color: #4ade80; border: 1px solid rgba(34,197,94,0.35); }
.card-tag.cluster-state-resolved   { background: rgba(139,148,158,0.12); color: var(--text-muted); border: 1px solid var(--border); }

/* ── Category pill ── */
.card-tag.card-category { background: rgba(139,148,158,0.1); color: var(--text-secondary); border: 1px solid var(--border); text-transform: capitalize; }

/* ── Source count & score badges in card-meta ── */
.card-sources, .card-score {
  font-size: 0.75rem;
  color: var(--text-muted);
}
.card-sources i, .card-score i { margin-right: 3px; }
.card-score { color: var(--accent-orange); }
.card-source-name { font-size: 0.75rem; color: var(--text-muted); }

/* ── Feed empty & error states ── */
.feed-empty, .feed-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 48px 24px;
  color: var(--text-muted);
  text-align: center;
}
.feed-empty i, .feed-error i { font-size: 2rem; opacity: 0.5; }
.feed-error-retry {
  background: var(--accent-blue);
  color: white;
  border: none;
  border-radius: 6px;
  padding: 8px 16px;
  cursor: pointer;
  font-size: 0.85rem;
}
.feed-error-retry:hover { opacity: 0.9; }
```

- [ ] **Step 2: Verify styles render correctly**

Open the page. The sort toggle should appear as pill-style buttons (matching the filter bar aesthetic). Temporarily remove `hidden` from the empty state div in DevTools to confirm it renders centered with the inbox icon.

- [ ] **Step 3: Commit**

```bash
git add static/css/components/news-grid.css
git commit -m "Add CSS for sort toggle, cluster badges, source count, feed states"
```

---

### Task 3: Hide dropped filters in priority-filter.js

**Files:**
- Modify: `static/js/features/priority-filter.js`

The spec drops priority pills, content type filter, and sources filter from the UI. This task hides them and wires the remaining filters (time, sort) to call `window.refreshFeed()` instead of `window.applyFilters()`.

- [ ] **Step 1: Hide priority pills, content type group, and sources group on load**

At the top of the IIFE in `static/js/features/priority-filter.js`, after the global declarations (`window.selectedPriority`, etc.), add:

```javascript
  // Hide filters not supported by cluster feed API
  var priorityPills = document.querySelector('.priority-pills');
  if (priorityPills) priorityPills.hidden = true;

  // Hide Content type and Sources groups in filter dropdown
  document.querySelectorAll('.fmd-group').forEach(function(group) {
    var label = group.querySelector('.fmd-group-label');
    if (!label) return;
    var text = label.textContent.trim().toLowerCase();
    if (text.indexOf('content') !== -1 || text.indexOf('sources') !== -1) {
      group.hidden = true;
    }
  });
```

- [ ] **Step 2: Change reapply() to call window.refreshFeed()**

Replace the `reapply` function body:

```javascript
  function reapply() {
    if (typeof window.refreshFeed === 'function') window.refreshFeed();
    else if (typeof window.applyFilters === 'function') window.applyFilters();
  }
```

This gracefully falls back to the old behavior if `refreshFeed` isn't defined yet.

- [ ] **Step 3: Wire the sort toggle buttons**

Add a click handler for the sort toggle at the bottom of the IIFE, before the closing `})();`:

```javascript
  /* ── Sort toggle ── */
  window.currentSort = 'latest';
  document.addEventListener('click', function(e) {
    var btn = e.target.closest('.sort-btn');
    if (!btn) return;
    document.querySelectorAll('.sort-btn').forEach(function(b) {
      b.classList.remove('active');
    });
    btn.classList.add('active');
    window.currentSort = btn.getAttribute('data-sort') || 'latest';
    reapply();
  });
```

- [ ] **Step 4: Verify page still loads without JS errors**

Open the page in a browser. Priority pills should be hidden. Content and Sources filter groups should be hidden. Time filter and sort toggle should be visible. Clicking sort/time should not crash (they call `reapply()` which falls back to `applyFilters`).

- [ ] **Step 5: Commit**

```bash
git add static/js/features/priority-filter.js
git commit -m "Hide dropped filters, add sort toggle handler, wire to refreshFeed"
```

---

### Task 4: Rewrite news-grid.js to fetch from the API

**Files:**
- Rewrite: `static/js/features/news-grid.js`

This is the core task. Replace all mock data with real API fetching, build cards from `ClusterSummary` shape, implement infinite scroll with offset pagination, and handle error/empty states.

- [ ] **Step 1: Write the new news-grid.js**

Replace the entire contents of `static/js/features/news-grid.js` with:

```javascript
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
    var catPill = '';
    if (cluster.categories && cluster.categories.length > 0) {
      catPill = '<span class="card-tag card-category">' + esc(cluster.categories[0]) + '</span>';
      tagSpans += catPill;
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
```

- [ ] **Step 2: Verify the feed loads from the API**

Start the Docker stack (`docker compose up`) and open the home page. Open browser DevTools Network tab. Verify:
- A request to `/api/feed?view=global&sort=latest&limit=10&offset=0` fires on page load
- Cards render from real cluster data (titles from real articles, not mock templates)
- If no clusters exist yet, the empty state ("No clusters found") is shown
- If the API is down, the error state with retry button is shown

- [ ] **Step 3: Verify infinite scroll**

Scroll to the bottom of the page. Verify:
- A second request fires with `offset=10` (if total > 10)
- New cards append below existing ones
- Scrolling stops fetching when all results are loaded

- [ ] **Step 4: Verify time filter works**

Click the Time filter and select "Last 1h". Verify:
- A new request fires with `date_from` set to ~1 hour ago (ISO-8601)
- The grid resets and shows only matching clusters
- Selecting "All time" omits the `date_from` param

- [ ] **Step 5: Verify sort toggle works**

Click the "Top" sort button. Verify:
- A new request fires with `sort=score`
- Cards reorder by score
- Score badges appear on cards
- Switching back to "Latest" restores time-based order

- [ ] **Step 6: Verify category filter from URL**

Navigate to `/?category=malware`. Verify:
- The request includes `category=research` (mapped from `malware`)
- Only matching clusters are shown

- [ ] **Step 7: Commit**

```bash
git add static/js/features/news-grid.js
git commit -m "Rewrite news-grid.js to fetch from /api/feed with real cluster data"
```

---

### Task 5: Update news-modal.js for cluster card click navigation

**Files:**
- Modify: `static/js/features/news-modal.js`

The spec says clicking a card navigates to `/cluster/{id}`, with a fallback to `top_article.source_url` if the cluster detail page (FE-04) isn't built yet. Replace the modal's card click handler with navigation logic.

- [ ] **Step 1: Replace the card click handler with navigation**

In `static/js/features/news-modal.js`, replace the `newsGrid.addEventListener('click', ...)` block (lines 70-80) with:

```javascript
  if (newsGrid) {
    newsGrid.addEventListener('click', function(e) {
      var card = e.target && e.target.closest ? e.target.closest('.news-card') : null;
      if (!card) return;
      e.preventDefault();
      var clusterId = card.getAttribute('data-cluster-id') || '';
      if (!clusterId) return;

      // Try cluster detail page first; fallback to top article source URL
      var list = window.loadedClusterList || [];
      var cluster = list.find(function(x) { return (x.id || '') === clusterId; });
      var clusterUrl = '/cluster/' + encodeURIComponent(clusterId);

      // Check if cluster detail page exists (FE-04). For now, fallback to source_url.
      if (cluster && cluster.top_article && cluster.top_article.source_url) {
        window.open(cluster.top_article.source_url, '_blank', 'noopener');
      } else {
        window.location.href = clusterUrl;
      }
    });
  }
```

Note: Once FE-04 (cluster detail page) is implemented, remove the `source_url` fallback and always navigate to `/cluster/{id}`.

- [ ] **Step 2: Verify card click opens the source article in a new tab**

Click on a card. The top article's source URL should open in a new browser tab. If the article has no source_url, it should navigate to `/cluster/{id}`.

- [ ] **Step 3: Commit**

```bash
git add static/js/features/news-modal.js
git commit -m "Wire card click to navigate to source URL (cluster detail page fallback)"
```

---

### Task 6: Update filters.js to call refreshFeed

**Files:**
- Modify: `static/js/components/filters.js`

The `filters.js` module calls `window.applyFilters()` when filter options change. It needs to call `window.refreshFeed()` instead, so filter changes trigger an API re-fetch.

- [ ] **Step 1: Replace applyFilters calls with refreshFeed**

In `static/js/components/filters.js`, find all occurrences of:

```javascript
if (typeof window.applyFilters === 'function') window.applyFilters();
```

Replace each with:

```javascript
if (typeof window.refreshFeed === 'function') window.refreshFeed();
else if (typeof window.applyFilters === 'function') window.applyFilters();
```

There are 3 occurrences: line 62 (option click handler), line 103 (clearAllFilters), and line 164 (more dropdown option click).

- [ ] **Step 2: Verify filters trigger API re-fetch**

Open DevTools Network tab. Change a filter. Verify a new `/api/feed` request fires with the correct params rather than client-side filtering.

- [ ] **Step 3: Commit**

```bash
git add static/js/components/filters.js
git commit -m "Wire filter changes to refreshFeed for server-side filtering"
```

---

### Task 7: End-to-end verification and cleanup

**Files:**
- All modified files from previous tasks

This task verifies all acceptance criteria from the spec.

- [ ] **Step 1: Verify all acceptance criteria**

With Docker stack running and some cluster data ingested, verify each criterion:

1. Home feed loads real cluster data from `GET /api/feed` on page load
2. Each card shows cluster info (title, desc, tags, keywords, source count, state)
3. Infinite scroll fetches next pages and appends cards
4. Time range filter triggers re-fetch with correct `date_from`
5. Category filter triggers re-fetch with correct `category` param
6. Sort toggle switches between latest and score ordering
7. Empty state shown when no clusters match filters
8. Error state shown with retry on API failure (test by stopping the backend)
9. No mock data remains in the rendering path

- [ ] **Step 2: Check for leftover mock data references**

Search `static/js/` for any remaining references to `newsTemplates`, `getNextNewsItem`, `loadedNewsList`, or `generateMockArticle`. These should all be gone.

```bash
grep -r "newsTemplates\|getNextNewsItem\|loadedNewsList\|generateMockArticle" static/js/
```

Expected: no results (or only the old test fixtures if any).

- [ ] **Step 3: Verify no console errors**

Open the page with DevTools console open. Navigate between pages, change filters, scroll. There should be no JS errors.

- [ ] **Step 4: Commit final state**

If any cleanup was needed:

```bash
git add -A
git commit -m "FE-03 complete: home feed wired to real API"
```
