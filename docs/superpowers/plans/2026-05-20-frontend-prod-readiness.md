# Frontend Prod-Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four URL/asset bugs, wire search and entity pages to real APIs, fix dead RSS nav links, and add "coming soon" banners to unimplemented pages — all frontend-only changes.

**Architecture:** Vanilla JS, no build step. Changes are direct edits to `.js` files in `static/js/features/`, HTML template files in `templates/`, and new CSS/partial files in `static/`. No backend changes.

**Tech Stack:** Vanilla JS (ES5/ES6), Jinja2 HTML templates, CSS. Backend APIs at `/api/search/`, `/api/entities/{id}`, `/api/rss`. Live dev stack at `http://localhost/`.

---

## File Map

| File | Action | What changes |
|---|---|---|
| `static/css/components/coming-soon-banner.css` | **Create** | Banner + nav-link-soon styles |
| `static/partials/components/coming-soon-banner.html` | **Create** | Reusable banner partial |
| `templates/digest.html` | Modify | Add banner `<link>` + `{% include %}` |
| `templates/preferences.html` | Modify | Add banner `<link>` + `{% include %}` |
| `templates/webhooks.html` | Modify | Add banner `<link>` + `{% include %}` |
| `templates/rss-config.html` | Modify | Add banner `<link>` + `{% include %}` |
| `static/js/features/sidebar.js` | Modify | Fix cluster URL at line 59 |
| `templates/index.html` | Modify | RSS hrefs + nav-link-soon classes |
| `templates/cluster.html` | Modify | RSS hrefs + nav-link-soon classes |
| `templates/search.html` | Modify | RSS hrefs + nav-link-soon classes + hint text + remove mock script |
| `templates/entity.html` | Modify | RSS hrefs + nav-link-soon classes + remove mock script |
| `templates/category.html` | Modify | RSS hrefs + nav-link-soon classes |
| `static/img/og-default.png` | **Create** | OG image placeholder |
| `static/js/features/search-page.js` | Modify | Full rewrite of `runSearch()` + `showResults()` |
| `static/js/features/entity-page.js` | Modify | Full rewrite of `init()` + `render()` |

---

## Task 1: Create "Coming Soon" Banner Component

**Files:**
- Create: `static/css/components/coming-soon-banner.css`
- Create: `static/partials/components/coming-soon-banner.html`

- [ ] **Step 1: Create the CSS file**

Create `static/css/components/coming-soon-banner.css` with this exact content:

```css
.coming-soon-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px;
  background: rgba(251, 191, 36, 0.08);
  border-bottom: 1px solid rgba(251, 191, 36, 0.25);
  color: #fbbf24;
  font-size: 0.875rem;
  font-weight: 500;
}

.coming-soon-banner .coming-soon-icon {
  font-style: normal;
  flex-shrink: 0;
}

/* Nav links for coming-soon features */
.nav-link-soon {
  opacity: 0.45 !important;
}

.nav-link-soon:hover {
  opacity: 0.65 !important;
}
```

- [ ] **Step 2: Create the HTML partial**

Create `static/partials/components/coming-soon-banner.html` with this exact content:

```html
<div class="coming-soon-banner" role="status">
  <span class="coming-soon-icon" aria-hidden="true">🚧</span>
  <span>This feature is in development and coming soon.</span>
</div>
```

- [ ] **Step 3: Commit**

```bash
git add static/css/components/coming-soon-banner.css static/partials/components/coming-soon-banner.html
git commit -m "feat(frontend): add coming-soon banner component"
```

---

## Task 2: Apply Banner to Four Pages + Fix Sidebar URL Bug

**Files:**
- Modify: `templates/digest.html`
- Modify: `templates/preferences.html`
- Modify: `templates/webhooks.html`
- Modify: `templates/rss-config.html`
- Modify: `static/js/features/sidebar.js`

The four affected templates each need (a) a `<link>` to load the banner CSS and (b) a Jinja2 `{% include %}` at the top of their `<main>` content.

- [ ] **Step 1: Apply banner to `templates/digest.html`**

After the existing `<link rel="stylesheet" href="/static/css/pages/digest.css">` line, add:
```html
  <link rel="stylesheet" href="/static/css/components/coming-soon-banner.css">
```

Find the opening `<main` tag (looks like `<main class="...">`) and insert immediately after it:
```html
    {% include "partials/components/coming-soon-banner.html" %}
```

- [ ] **Step 2: Apply banner to `templates/preferences.html`**

After the existing `<link rel="stylesheet" href="/static/css/pages/preferences.css">` line, add:
```html
  <link rel="stylesheet" href="/static/css/components/coming-soon-banner.css">
```

Find the opening `<main` tag and insert immediately after it:
```html
    {% include "partials/components/coming-soon-banner.html" %}
```

- [ ] **Step 3: Apply banner to `templates/webhooks.html`**

After the existing `<link rel="stylesheet" href="/static/css/pages/webhooks.css">` line, add:
```html
  <link rel="stylesheet" href="/static/css/components/coming-soon-banner.css">
```

Find the opening `<main` tag and insert immediately after it:
```html
    {% include "partials/components/coming-soon-banner.html" %}
```

- [ ] **Step 4: Apply banner to `templates/rss-config.html`**

After the existing `<link rel="stylesheet" href="/static/css/pages/rss-config.css">` line, add:
```html
  <link rel="stylesheet" href="/static/css/components/coming-soon-banner.css">
```

Find the opening `<main` tag and insert immediately after it:
```html
    {% include "partials/components/coming-soon-banner.html" %}
```

- [ ] **Step 5: Fix sidebar.js cluster URL (line 59)**

In `static/js/features/sidebar.js`, find:
```js
          a.href = '/cluster/' + cluster.id;
```
Replace with:
```js
          a.href = '/cluster?id=' + cluster.id;
```

- [ ] **Step 6: Verify in browser**

Navigate to `http://localhost/digest` — expect a yellow-tinted banner at the top of the content area reading "This feature is in development and coming soon."

Navigate to `http://localhost/preferences`, `/webhooks`, `/rss-config` — same banner should appear on each.

Navigate to `http://localhost/` — scroll to "Today's Digest" in the sidebar. Hover over an item. The link in the status bar should show `/cluster?id=...` format (not `/cluster/...`). Click one — it should load the cluster detail page, not a 404.

- [ ] **Step 7: Commit**

```bash
git add templates/digest.html templates/preferences.html templates/webhooks.html templates/rss-config.html static/js/features/sidebar.js
git commit -m "feat(frontend): coming-soon banners on unimplemented pages; fix sidebar cluster links"
```

---

## Task 3: Fix Nav in All Five Full-Nav Templates

The top navbar (`My Stack`, `Webhooks`, `RSS Feed`, `Subscribe`) is duplicated across five templates. This task fixes the dead RSS dropdown links and dims the coming-soon nav items in all five.

**Files:**
- Modify: `templates/index.html`
- Modify: `templates/cluster.html`
- Modify: `templates/search.html`
- Modify: `templates/entity.html`
- Modify: `templates/category.html`

The change is identical in every template. In the `<div class="nav-actions">` block, find this block (lines ~59–75 in each file):

**Before (in each template):**
```html
        <a href="/preferences" class="nav-subscribe-btn nav-pref-link nav-pref-mystack"><i class="fas fa-layer-group"></i> <span data-i18n-key="nav.myStack">My Stack</span></a>
        <a href="/webhooks" class="nav-subscribe-btn nav-pref-link nav-pref-webhooks"><i class="fas fa-plug"></i> <span data-i18n-key="nav.webhooks">Webhooks</span></a>
        <a href="/rss-config" class="nav-subscribe-btn nav-pref-link nav-pref-rss"><i class="fas fa-rss"></i> <span data-i18n-key="nav.rssConfig">RSS Feed</span></a>
        <div class="nav-rss-wrap">
          <button type="button" class="nav-rss-btn" id="rssTrigger" aria-expanded="false" aria-haspopup="true">
            <i class="fas fa-rss nav-rss-icon"></i> RSS <i class="fas fa-chevron-down"></i>
          </button>
          <div class="nav-rss-dropdown" id="rssDropdown" role="menu">
            <a href="#" class="nav-rss-option" data-i18n-key="rss.allFeeds">All feeds</a>
            <a href="#" class="nav-rss-option" data-i18n-key="rss.breaking">Breaking</a>
            <a href="#" class="nav-rss-option" data-i18n-key="rss.threatIntel">Threat Intel</a>
            <a href="#" class="nav-rss-option" data-i18n-key="rss.malware">Malware</a>
          </div>
        </div>
        <button type="button" class="nav-share-btn" id="navShareBtn" aria-label="Share insight"><i class="fas fa-share-alt"></i> <span data-i18n-key="nav.share">Share</span></button>
        <a href="/digest" class="nav-subscribe-btn" aria-label="Subscribe">
```

**After (in each template):**
```html
        <a href="/preferences" class="nav-subscribe-btn nav-pref-link nav-pref-mystack nav-link-soon" title="Coming soon"><i class="fas fa-layer-group"></i> <span data-i18n-key="nav.myStack">My Stack</span></a>
        <a href="/webhooks" class="nav-subscribe-btn nav-pref-link nav-pref-webhooks nav-link-soon" title="Coming soon"><i class="fas fa-plug"></i> <span data-i18n-key="nav.webhooks">Webhooks</span></a>
        <a href="/rss-config" class="nav-subscribe-btn nav-pref-link nav-pref-rss nav-link-soon" title="Coming soon"><i class="fas fa-rss"></i> <span data-i18n-key="nav.rssConfig">RSS Feed</span></a>
        <div class="nav-rss-wrap">
          <button type="button" class="nav-rss-btn" id="rssTrigger" aria-expanded="false" aria-haspopup="true">
            <i class="fas fa-rss nav-rss-icon"></i> RSS <i class="fas fa-chevron-down"></i>
          </button>
          <div class="nav-rss-dropdown" id="rssDropdown" role="menu">
            <a href="/api/rss" target="_blank" rel="noopener" class="nav-rss-option" data-i18n-key="rss.allFeeds">All feeds</a>
            <a href="/api/rss?category=breaking" target="_blank" rel="noopener" class="nav-rss-option" data-i18n-key="rss.breaking">Breaking</a>
            <a href="/api/rss?category=deep-dives" target="_blank" rel="noopener" class="nav-rss-option" data-i18n-key="rss.threatIntel">Threat Intel</a>
            <a href="/api/rss?category=research" target="_blank" rel="noopener" class="nav-rss-option" data-i18n-key="rss.malware">Malware</a>
          </div>
        </div>
        <button type="button" class="nav-share-btn" id="navShareBtn" aria-label="Share insight"><i class="fas fa-share-alt"></i> <span data-i18n-key="nav.share">Share</span></button>
        <a href="/digest" class="nav-subscribe-btn nav-link-soon" title="Coming soon" aria-label="Subscribe">
```

Also add a `<link>` for the banner CSS in the `<head>` of each of the five templates (so `.nav-link-soon` styles load):
```html
  <link rel="stylesheet" href="/static/css/components/coming-soon-banner.css">
```

- [ ] **Step 1: Apply changes to `templates/index.html`** (nav block + head link)

- [ ] **Step 2: Apply changes to `templates/cluster.html`** (nav block + head link)

- [ ] **Step 3: Apply changes to `templates/search.html`** (nav block + head link)

- [ ] **Step 4: Apply changes to `templates/entity.html`** (nav block + head link)

- [ ] **Step 5: Apply changes to `templates/category.html`** (nav block + head link)

- [ ] **Step 6: Verify in browser**

Navigate to `http://localhost/`. The My Stack, Webhooks, RSS Feed, and Subscribe nav buttons should appear dimmed.

Click the RSS dropdown — "All feeds" should open `/api/rss` in a new tab returning XML. "Breaking" should open `/api/rss?category=breaking`.

Hover over My Stack — tooltip should read "Coming soon".

- [ ] **Step 7: Commit**

```bash
git add templates/index.html templates/cluster.html templates/search.html templates/entity.html templates/category.html
git commit -m "feat(frontend): fix RSS dropdown links; dim coming-soon nav items"
```

---

## Task 4: Create OG Image Placeholder

**Files:**
- Create: `static/img/og-default.png`

- [ ] **Step 1: Create the `static/img/` directory and generate the PNG**

Run this Python script from the project root. It uses only stdlib — no Pillow needed:

```python
import struct
import zlib
import os

os.makedirs('static/img', exist_ok=True)

def make_png(width, height, r, g, b, path):
    def chunk(tag, data):
        raw = tag + data
        return struct.pack('>I', len(data)) + raw + struct.pack('>I', zlib.crc32(raw) & 0xffffffff)

    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    # One row: filter byte (0) + RGB pixels
    row = b'\x00' + bytes([r, g, b]) * width
    idat = chunk(b'IDAT', zlib.compress(row * height, 9))
    iend = chunk(b'IEND', b'')
    with open(path, 'wb') as f:
        f.write(sig + ihdr + idat + iend)

# Dark background matching the site theme (#0f0f16 ≈ rgb(15, 15, 22))
make_png(1200, 630, 15, 15, 22, 'static/img/og-default.png')
print('Created static/img/og-default.png')
```

Run it:
```bash
python scripts/make_og_image.py
```

Or inline:
```bash
python -c "
import struct, zlib, os
os.makedirs('static/img', exist_ok=True)
def chunk(tag, data):
    raw = tag + data
    return struct.pack('>I', len(data)) + raw + struct.pack('>I', zlib.crc32(raw) & 0xffffffff)
sig = b'\x89PNG\r\n\x1a\n'
ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', 1200, 630, 8, 2, 0, 0, 0))
row = b'\x00' + bytes([15, 15, 22]) * 1200
idat = chunk(b'IDAT', zlib.compress(row * 630, 9))
iend = chunk(b'IEND', b'')
open('static/img/og-default.png','wb').write(sig + ihdr + idat + iend)
print('done')
"
```

- [ ] **Step 2: Verify**

```bash
python -c "
with open('static/img/og-default.png','rb') as f:
    sig = f.read(8)
assert sig == b'\x89PNG\r\n\x1a\n', 'Not a valid PNG'
print('PNG signature OK, size:', open(\"static/img/og-default.png\",'rb').seek(0,2) or open(\"static/img/og-default.png\",'rb').read().__len__(), 'bytes')
"
```

Expected: prints "PNG signature OK" without error.

Navigate to `http://localhost/static/img/og-default.png` — browser should display a dark rectangle (1200×630).

- [ ] **Step 3: Commit**

```bash
git add static/img/og-default.png
git commit -m "feat(frontend): add OG image placeholder"
```

---

## Task 5: Wire Search Page to Real API

**Files:**
- Modify: `static/js/features/search-page.js`
- Modify: `templates/search.html`

The current `search-page.js` calls `window.CyberNews.mockEntities.searchEntities()` which searches 8 hardcoded records. Replace with a fetch to `/api/search/?q=...` and a new article-card renderer.

- [ ] **Step 1: Verify the real API works**

```bash
curl -s "http://localhost/api/search/?q=ransomware&limit=3" | python -m json.tool | head -40
```

Expected: JSON with `items` array containing articles with `title`, `desc`, `source_name`, `source_url`, `published_at`, `severity`, `tags` fields.

- [ ] **Step 2: Replace `runSearch()` in `search-page.js`**

Find the existing `function runSearch(query)` block (lines ~98–131) and replace the entire function with:

```js
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
```

- [ ] **Step 3: Replace `showResults()` in `search-page.js`**

Find the existing `function showResults(results, query)` block (lines ~61–88) and replace the entire function with:

```js
  function showResults(items, total, query) {
    lastState = null;
    stateEl.hidden = true;
    if (!items.length) {
      showState('empty', 'search.noResultsTitle', 'search.noResultsMsg');
      return;
    }
    if (resultsHeading) {
      resultsHeading.textContent = total + ' result' + (total === 1 ? '' : 's') + ' for “' + query + '”';
      resultsHeading.hidden = false;
    }
    resultsList.innerHTML = items.map(function(item) {
      var desc = (item.desc || '').slice(0, 160);
      if (item.desc && item.desc.length > 160) desc += '…';
      var sevBadge = item.severity
        ? '<span class="result-sev sev-' + escapeHtml(item.severity) + '">' + escapeHtml(item.severity) + '</span>'
        : '';
      var tags = (item.tags || []).slice(0, 3)
        .map(function(tag) { return '<span class="result-tag">' + escapeHtml(tag) + '</span>'; })
        .join('');
      var meta = [
        item.source_name ? escapeHtml(item.source_name) : '',
        item.published_at ? timeAgo(item.published_at) : '',
      ].filter(Boolean).join(' · ');
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
```

- [ ] **Step 4: Fix the `history.replaceState` URL in `search-page.js`**

Find (line ~137):
```js
      var url = 'search.html' + (q ? '?q=' + encodeURIComponent(q) : '');
```
Replace with:
```js
      var url = '/search' + (q ? '?q=' + encodeURIComponent(q) : '');
```

- [ ] **Step 5: Update `templates/search.html`**

Remove the mock-entities script tag (line ~155):
```html
  <script src="/static/js/data/mock-entities.js"></script>
```
Delete that line entirely.

Update the search input placeholder (find `placeholder="Search threat actors, CVEs, malware, IOCs…"` and replace with):
```html
placeholder="Search security news, CVEs, threat actors…"
```

Update the hint text (find `<p class="search-hint"` and update its text content):
```html
        <p class="search-hint" data-i18n-key="search.hint">Full-text search across articles, advisories, and threat reports.</p>
```

- [ ] **Step 6: Verify in browser**

Navigate to `http://localhost/search`.

Type `ransomware` and press Enter. Expect:
- URL changes to `/search?q=ransomware` (not `search.html?q=ransomware`)
- Results appear: multiple real articles with source names like "BleepingComputer", "The Hacker News"
- Each result card shows title, description snippet, source · time-ago, and severity badge if present
- Clicking a result opens the source article in a new tab

Type a query with no results (e.g. `xyzzy123abc`). Expect: "No results" empty state.

- [ ] **Step 7: Commit**

```bash
git add static/js/features/search-page.js templates/search.html
git commit -m "feat(frontend): wire search to /api/search/; replace mock entity results with real articles"
```

---

## Task 6: Wire Entity Page to Real API

**Files:**
- Modify: `static/js/features/entity-page.js`
- Modify: `templates/entity.html`

The current entity page calls `window.CyberNews.mockEntities.getEntityById(id)` with fake mock IDs (e1–e8). Replace with an async fetch to `/api/entities/{id}` and a new renderer that maps real `EntityDetail` fields.

- [ ] **Step 1: Verify the real API works**

First find a real entity ID from the entities index:

```bash
curl -s "http://localhost/api/entities/?limit=3" | python -m json.tool | head -40
```

Expected: JSON with `items` array. Note an `id` value (e.g. `"a1B2c3d4xyz"`).

```bash
curl -s "http://localhost/api/entities/THAT_ID" | python -m json.tool | head -30
```

Expected: `{id, type, name, normalized_key, aliases, description, cvss_score, first_seen, last_seen, article_count, articles}`.

- [ ] **Step 2: Replace `entity-page.js` with the new implementation**

Replace the entire contents of `static/js/features/entity-page.js` with:

```js
/**
 * Entity page: load entity by ?id=, render title, type, description, aliases,
 * CVSS score, and linked articles from /api/entities/{id}.
 */
(function() {
  var container = document.getElementById('entityPageContent');
  var notFoundEl = document.getElementById('entityNotFound');
  var loadingEl = document.getElementById('entityLoading');
  var errorEl = document.getElementById('entityError');
  var errorMsgEl = document.getElementById('entityErrorMsg');
  var retryBtn = document.getElementById('entityRetryBtn');
  if (!container) return;

  var TYPE_LABELS = {
    cve: 'CVE', vendor: 'Vendor', product: 'Product',
    actor: 'Threat Actor', malware: 'Malware', tool: 'Tool',
    campaign: 'Campaign', vuln_alias: 'Vulnerability',
  };

  function showState(state) {
    if (loadingEl)  loadingEl.hidden  = (state !== 'loading');
    if (errorEl)    errorEl.hidden    = (state !== 'error');
    if (notFoundEl) notFoundEl.hidden = (state !== 'notfound');
    if (container)  container.hidden  = (state !== 'content');
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

  function render(entity) {
    var typeLabel = TYPE_LABELS[entity.type] || entity.type || 'Entity';

    var cvssHtml = entity.cvss_score != null
      ? '<span class="entity-cvss-badge">CVSS ' + Number(entity.cvss_score).toFixed(1) + '</span>'
      : '';

    var aliasesHtml = '';
    if (entity.aliases && entity.aliases.length) {
      aliasesHtml =
        '<section class="entity-section">' +
          '<h2 class="entity-section-title">Also known as</h2>' +
          '<div class="entity-aliases">' +
            entity.aliases.map(function(a) {
              return '<span class="entity-alias-chip">' + escapeHtml(a) + '</span>';
            }).join('') +
          '</div>' +
        '</section>';
    }

    var metaParts = [
      entity.first_seen
        ? '<span class="entity-meta-item"><strong>First seen</strong> ' + timeAgo(entity.first_seen) + '</span>'
        : null,
      entity.last_seen
        ? '<span class="entity-meta-item"><strong>Last seen</strong> ' + timeAgo(entity.last_seen) + '</span>'
        : null,
      entity.article_count != null
        ? '<span class="entity-meta-item"><strong>Articles</strong> ' + entity.article_count + '</span>'
        : null,
    ].filter(Boolean);

    var articlesHtml = '';
    if (entity.articles && entity.articles.length) {
      articlesHtml =
        '<section class="entity-section">' +
          '<h2 class="entity-section-title"><i class="fas fa-newspaper"></i> Related Articles</h2>' +
          '<div class="related-articles-list">' +
            entity.articles.map(function(a) {
              var meta = [
                a.source_name ? escapeHtml(a.source_name) : '',
                a.published_at ? timeAgo(a.published_at) : '',
              ].filter(Boolean).join(' · ');
              return (
                '<a href="' + escapeHtml(a.source_url || '#') + '" target="_blank" rel="noopener" class="related-article-row">' +
                  '<div class="article-title">' + escapeHtml(a.title || '') + '</div>' +
                  (meta ? '<div class="article-meta">' + meta + '</div>' : '') +
                '</a>'
              );
            }).join('') +
          '</div>' +
        '</section>';
    }

    container.innerHTML =
      '<a href="/search" class="entity-back"><i class="fas fa-arrow-left"></i> Back to Search</a>' +
      '<header class="entity-header">' +
        '<h1 class="entity-title">' + escapeHtml(entity.name || '') + '</h1>' +
        '<span class="entity-type-badge">' + escapeHtml(typeLabel) + '</span>' +
        cvssHtml +
        (entity.description
          ? '<div class="entity-description">' + escapeHtml(entity.description) + '</div>'
          : '<div class="entity-description entity-description--empty">No description available yet.</div>') +
        (metaParts.length ? '<div class="entity-meta">' + metaParts.join('') + '</div>' : '') +
      '</header>' +
      aliasesHtml +
      articlesHtml;

    showState('content');
  }

  function showNotFound() {
    if (notFoundEl) {
      notFoundEl.innerHTML =
        '<div class="state-block-icon"><i class="fas fa-question-circle"></i></div>' +
        '<p class="state-block-title">Entity not found</p>' +
        '<p class="state-block-message">The requested entity may not exist or the link may be invalid.</p>' +
        '<div class="state-block-actions">' +
          '<a href="/search" class="state-block-btn"><i class="fas fa-arrow-left"></i> Back to Search</a>' +
        '</div>';
      notFoundEl.classList.add('state-block', 'is-empty');
    }
    showState('notfound');
  }

  function showError(message) {
    if (errorMsgEl && message) errorMsgEl.textContent = message;
    showState('error');
  }

  async function init() {
    showState('loading');
    var params = new URLSearchParams(window.location.search);
    var id = params.get('id');
    if (!id) { showNotFound(); return; }
    try {
      var resp = await fetch('/api/entities/' + encodeURIComponent(id));
      if (resp.status === 404) { showNotFound(); return; }
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      var entity = await resp.json();
      render(entity);
    } catch (e) {
      showError(e && e.message ? e.message : null);
    }
  }

  if (retryBtn) retryBtn.addEventListener('click', init);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
```

- [ ] **Step 3: Update `templates/entity.html`**

Remove the mock-entities script tag (find and delete):
```html
  <script src="/static/js/data/mock-entities.js"></script>
```

- [ ] **Step 4: Verify in browser using a real entity ID**

From step 1, take a real entity ID. Navigate to `http://localhost/entity?id=REAL_ID`.

Expect:
- Loading spinner briefly, then content appears
- Entity name in `<h1>`, type badge (e.g. "CVE", "Malware", "Threat Actor")
- CVSS badge present if it's a CVE with a score
- Description text or "No description available yet."
- "Also known as" section with alias chips, if aliases exist
- "Related Articles" section with real article rows, each linking to an external source URL in a new tab
- "Back to Search" link navigates to `/search`

Navigate to `http://localhost/entity?id=nonexistent` — expect the "Entity not found" state with the back link.

- [ ] **Step 5: Commit**

```bash
git add static/js/features/entity-page.js templates/entity.html
git commit -m "feat(frontend): wire entity page to /api/entities/{id}; remove mock data dependency"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] 1a: Sidebar cluster URL — Task 2, Step 5
- [x] 1b: Search URL rewrite — Task 5, Step 4
- [x] 1c: Entity link from search — absorbed into Task 5 (old renderer deleted)
- [x] 1d: OG image — Task 4
- [x] Search wired to `/api/search/` — Task 5, Steps 2–3
- [x] Entity wired to `/api/entities/{id}` — Task 6, Steps 2–3
- [x] RSS dropdown hrefs fixed in all 5 nav templates — Task 3
- [x] Coming-soon banner on digest/preferences/webhooks/rss-config — Tasks 1–2
- [x] nav-link-soon styling — Task 3 (CSS in Task 1 file, applied in Task 3)
- [x] `mock-entities.js` removed from search.html + entity.html — Tasks 5+6

**Placeholder scan:** No TBDs. All code blocks are complete. All file paths are exact.

**Type consistency:** `showResults(items, total, query)` signature used in Task 5 Steps 2–3. `render(entity)` signature unchanged. `init()` becomes async in Task 6 — the `retryBtn` click handler calls `init()` directly, which works fine with async functions.
