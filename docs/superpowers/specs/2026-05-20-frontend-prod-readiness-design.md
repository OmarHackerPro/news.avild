# Frontend Prod-Readiness Design
**Date:** 2026-05-20  
**Scope:** Frontend-only. No backend changes.  
**Approach:** Option B — Wired MVP

---

## Context

Visual audit of the frontend against the live local stack (`http://localhost/`) revealed four confirmed URL/asset bugs, two features backed by mock data that have real API counterparts, dead navbar links, and five pages with no backend support that need "coming soon" treatment before going to prod.

---

## Section 1: Bug Fixes

Four targeted fixes. Each is a one-line or near-one-line change.

### 1a. Sidebar "Today's Digest" cluster links → 404
**File:** `static/js/features/sidebar.js:59`  
**Bug:** `a.href = '/cluster/' + cluster.id` — path-style URL has no FastAPI route.  
**Fix:** `a.href = '/cluster?id=' + cluster.id`

### 1b. Search form URL rewrite uses wrong path
**File:** `static/js/features/search-page.js:137`  
**Bug:** `history.replaceState` pushes `'search.html?q=...'` — a stale static-file path.  
**Fix:** Change to `'/search?q=' + encodeURIComponent(q)`

### 1c. Entity result links from search → 404
**File:** `static/js/features/search-page.js:80`  
**Bug:** `href = 'entity.html?id=' + ...` — path 404s; FastAPI route is `/entity?id=`.  
**Fix:** Absorbed into Section 2 — the entire `showResults()` renderer is replaced; the old entity link code at line 80 is deleted as part of that rewrite. No standalone fix needed.

### 1d. Missing OG image
**File:** `pages.py` references `/static/img/og-default.png`; `static/img/` directory does not exist.  
**Fix:** Create `static/img/og-default.png` — a minimal 1200×630 PNG (dark background, "avild.news" wordmark). Can be a static asset committed to the repo.

---

## Section 2: Wire Search Page to Real API

### Problem
`search-page.js` calls `window.CyberNews.mockEntities.searchEntities()` — a client-side filter over 8 hardcoded records. Users searching "ransomware" get two mock results (LockBit 3.0, Black Basta). The real `/api/search/` endpoint does full-text across 2,000+ real articles.

### Design
**Endpoint:** `GET /api/search/?q={q}&limit=20`  
**Response shape:** `{items: NewsItem[], total: int, query: str, facets: {...}}`

**`search-page.js` changes:**
- Remove `mock-entities.js` dependency entirely.
- Rewrite `runSearch(q)` to `fetch('/api/search/?q=' + encodeURIComponent(q) + '&limit=20')`.
- Rewrite `showResults(items, query)` to render **article cards** using `NewsItem` fields:
  - Title (`item.title`)
  - Description (`item.desc`, truncated to 160 chars)
  - Source + time-ago (`item.source_name`, `item.published_at`)
  - Severity badge if present (`item.severity`)
  - Tag pills from `item.tags` (first 3)
  - Link: `item.source_url` (opens external article in new tab)
- Show total count in heading: `"{total} results for '{q}'"`.
- Empty state unchanged. Error state unchanged.

**`search.html` changes:**
- Remove `<script src="/static/js/data/mock-entities.js">` tag.
- Update `<input>` placeholder: `"Search security news, CVEs, threat actors…"`
- Update `.search-hint` text: `"Full-text search across articles, advisories, and threat reports."`

### Result
Users searching "ransomware" get real articles — BleepingComputer, The Hacker News, CISA advisories. The mock entity cards (LockBit profile, Black Basta profile) are replaced by real news items.

---

## Section 3: Wire Entity Page to Real API

### Problem
`entity-page.js` calls `window.CyberNews.mockEntities.getEntityById(id)`. The "entity" at `?id=e4` (LockBit 3.0) is a mock record. The real `/api/entities/{id}` endpoint serves real entities extracted by NER — CVEs, actors, malware, products, tools — with linked articles.

### Design
**Endpoint:** `GET /api/entities/{id}`  
**Response shape:** `EntityDetail {id, type, name, normalized_key, aliases[], description, cvss_score, first_seen, last_seen, article_count, articles: NewsItem[]}`

**`entity-page.js` changes:**
- Remove `mock-entities.js` dependency.
- Replace `getEntity()` with `async fetchEntity(id)` → `fetch('/api/entities/' + encodeURIComponent(id))`.
- On 404, call `showState('notfound')` — already handled.
- Update `render(entity)` to map real fields:
  - Header: `entity.name`, type badge (`entity.type`)
  - Meta: `first_seen`, `last_seen`, `article_count`
  - Description: `entity.description` (may be null — show placeholder if so)
  - Aliases: `entity.aliases[]` shown as a chip list (hide section if empty)
  - CVSS badge: show if `entity.cvss_score` is present
  - **"Related Articles"** section: render `entity.articles[]` as compact rows (title + source + time) linking to `source_url`; hide section if empty
  - Remove "Related Clusters" section (not in API)
  - Remove "Related Entities" section (not in API — cross-entity links are not yet modelled)

**`entity.html` changes:**
- Remove `<script src="/static/js/data/mock-entities.js">` tag.
- Rename "Related Clusters" section label to "Related Articles" in the template markup.
- Remove "Related Entities" section markup entirely.

**Note:** `pages.py` entity SSR remains generic (no backend changes this session). Crawlers see a shell; JS hydrates with real data. Acceptable for entity pages at this stage.

---

## Section 4: Navbar RSS Dropdown + RSS Config

### Problem
The navbar RSS dropdown (`index.html`) has four `href="#"` dead links. The real public RSS feed is at `GET /api/rss` with optional `?category=` and `?severity=` filters.

### Design
The nav is **not** a shared partial — it is duplicated in each template. RSS hrefs must be fixed in all five templates that carry the full navbar: `index.html`, `cluster.html`, `search.html`, `entity.html`, `category.html`.

| Label | Current | Fixed |
|---|---|---|
| All feeds | `#` | `/api/rss` |
| Breaking | `#` | `/api/rss?category=breaking` |
| Threat Intel | `#` | `/api/rss?category=deep-dives` |
| Malware | `#` | `/api/rss?category=research` |

All open in a new tab (`target="_blank"`) since they return XML.

**`/rss-config` page** — add "Coming Soon" banner (see Section 5). Personalised RSS (per-user token) requires auth which is not yet live.

---

## Section 5: "Coming Soon" Banners + Navbar Indicators

### Affected pages
| Page | Reason |
|---|---|
| `/digest` | No email delivery infrastructure |
| `/preferences` | Requires user accounts; current mock has wrong categories/sources |
| `/webhooks` | No backend at all |
| `/rss-config` | Personalised RSS requires auth |

### Banner component
A single reusable partial: `static/partials/components/coming-soon-banner.html`

```html
<div class="coming-soon-banner">
  <span class="coming-soon-icon">🚧</span>
  <span>This feature is in development and coming soon.</span>
</div>
```

With CSS in `static/css/components/coming-soon-banner.css`:
- Subtle warm-yellow tinted bar across the top of the content area
- Matches dark theme
- No z-index overlay — content remains readable beneath it

Each affected template includes the partial at the top of `<main>`.

### Navbar coming-soon indicators
In `index.html` (and other templates with the full nav), add `class="nav-link-soon"` and `title="Coming soon"` to:
- "My Stack" link
- "Webhooks" link  
- "Subscribe" link

CSS for `.nav-link-soon`: reduced opacity (0.5), `cursor: not-allowed`, grey colour. Links still navigate to the page where the banner explains the status.

**Auth pages** (`/login`, `/signup`, `/profile`): no coming-soon treatment — auth backend exists and the navbar user widget is already wired to `/api/auth/me`.

---

## Out of Scope (this sprint)

- Backend changes of any kind
- Entity SSR in `pages.py` (no backend changes)
- Email delivery for digest
- User account persistence (preferences, bookmarks)
- XML sitemap generation
- Webhook delivery infrastructure
- Per-user RSS tokens

---

## Files Changed Summary

| File | Change |
|---|---|
| `static/js/features/sidebar.js` | Fix cluster URL format |
| `static/js/features/search-page.js` | Fix URL rewrite + wire to `/api/search/` + new renderer |
| `static/js/features/entity-page.js` | Wire to `/api/entities/{id}` + remove mock dependency |
| `static/img/og-default.png` | Create new asset |
| `templates/search.html` | Update placeholder/hint text, remove mock script tag |
| `templates/entity.html` | Update section labels, remove mock script tag |
| `templates/index.html` | Fix RSS dropdown hrefs, add nav-link-soon classes |
| `templates/cluster.html` | Fix RSS dropdown hrefs |
| `templates/search.html` | Fix RSS dropdown hrefs, update placeholder/hint, remove mock script |
| `templates/entity.html` | Fix RSS dropdown hrefs, update section labels, remove mock script |
| `templates/category.html` | Fix RSS dropdown hrefs |
| `templates/digest.html` | Include coming-soon banner partial |
| `templates/preferences.html` | Include coming-soon banner partial |
| `templates/webhooks.html` | Include coming-soon banner partial |
| `templates/rss-config.html` | Include coming-soon banner partial |
| `static/partials/components/coming-soon-banner.html` | New partial |
| `static/css/components/coming-soon-banner.css` | New stylesheet |
