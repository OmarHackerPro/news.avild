# Wire Home Feed to Real API (FE-03)

**Date:** 2026-03-20
**Status:** Approved
**Kanban:** FE-03 Home Feed page (global + personal tabs, sorting, filters)

## Goal

Replace mock data in `static/js/features/news-grid.js` with real `fetch()` calls to `GET /api/feed`. Each card represents a **cluster** (not a raw article). This proves the full pipeline end-to-end: ingestion -> clustering -> API -> screen.

## Data Flow

1. Page loads -> `fetch('/api/feed?view=global&sort=latest&limit=10&offset=0')`
2. Response: `{ items: ClusterSummary[], total: int }`
3. Each `ClusterSummary` has a `top_article` (NewsItem) — card renders from that
4. Scroll triggers next page: `offset += 10`, append results
5. Stop when `offset >= total`

## Card Field Mapping

| Card field | Source | Notes |
|---|---|---|
| Title | `item.top_article.title` | Direct render, no i18n lookup (real data replaces mock translation keys) |
| Description | `item.top_article.desc` | Same — raw from API |
| Tags | `item.top_article.tags` | Rendered as colored tag spans (same as current) |
| Keywords | `item.top_article.keywords` | Rendered as keyword pills (same as current), first highlighted |
| Severity badge | `item.top_article.severity` | critical/high/medium/low with icon (same as current) |
| Time | Compute client-side from `item.top_article.published_at` | Use relative time helper ("15m", "3h", "2d") computed from ISO-8601 `published_at`. Do NOT use `item.top_article.time` — it's stale if cached. |
| Source name | `item.top_article.source_name` | |
| Category pill | `item.categories[0]` | |
| Source count (new) | `item.article_count` | Show "N sources" badge. Hide when `article_count <= 1` (adds no info) |
| Cluster state (new) | `item.state` | Badge: new/developing/confirmed/resolved |
| Score (new) | `item.score` | Visible when sort=score |
| Confidence | Reserved for cluster detail page only | Not shown on cards |

### Card click behavior

Clicking a card (or the "Read" button) navigates to `/cluster/{item.id}`. The `data-news-id` attribute becomes `data-cluster-id`. The cluster detail page (FE-04) will handle rendering — for now, if FE-04 isn't built yet, clicking can open the `top_article.source_url` as a fallback link.

### i18n changes

The current `buildCard()` looks up `news.{id}.title` / `news.{id}.desc` in a translations dictionary. With real API data, these keys won't exist. Remove the i18n lookup for card title/desc — real content comes from the API in its original language. Keep i18n for static UI strings (button labels like "Read").

## Filters

| UI filter | API param | Notes |
|---|---|---|
| Time range (1h/24h/7d/all) | `date_from` = now minus duration | Compute ISO-8601 client-side. "all" omits `date_from`. Note: API filters on cluster `latest_at`, so "last 1h" = clusters with a recent article in the last hour (cluster may be older). |
| Category (URL-based) | `category` | The URL `?category=X` maps to cluster `categories` values. Reconcile the current URL-to-category map: `threat-intel`/`apt`/`pentest` -> `deep-dives`, `malware` -> `research`, `breaches` -> `dark-web`, `bug-bounty` -> `beginner`. Send the mapped value to the API. |
| Sort toggle (Latest / Top) | `sort=latest` or `sort=score` | New UI toggle added to filter bar |
| Priority pills | **Dropped** | Hide severity pills. Re-add when cluster-level severity exists (BE-07) |
| Content type (news/analysis/report/advisory) | **Dropped** | The API doesn't support `type` filtering on clusters. Hide the Content filter group. Re-add when supported. |
| Sources filter | **Dropped** | The API doesn't support source filtering on clusters. Hide the Sources filter group. Re-add when supported. |

Filter changes trigger a full re-fetch (reset offset to 0, clear grid, fetch page 1). Use `AbortController` to cancel in-flight requests when filters change, preventing stale responses from overwriting fresh data.

## Personal vs Global View

The API supports `view=global` and `view=personal`, but personal feed filtering is not yet implemented (backend TODO — currently falls through to global). For now: always send `view=global`. Do NOT render a personal/global tab toggle. Add it when personal feed logic lands (requires auth + preferences integration).

## Pagination

Keep existing infinite scroll mechanism. Replace mock template cycling with offset-based API fetching:
- Initial load: `offset=0, limit=10`
- Scroll trigger: `offset += limit`, append new cards
- Terminal state: stop fetching when `offset >= total`, show "No more results"
- Loading state: show spinner in existing load indicator element
- Debounce scroll events (~200ms) to avoid rapid-fire requests

## Error and Empty States

- **Loading:** skeleton cards or spinner using existing load indicator
- **Empty (no results):** "No clusters found" message with suggestion to adjust filters
- **API error:** "Failed to load feed" with retry button
- **Network failure:** graceful message, no broken UI

## Files Changed

### `static/js/features/news-grid.js` (major rewrite)
- Remove all mock article templates and `generateMockArticle()` logic
- Add `fetchFeed(params)` function that calls `GET /api/feed` with query params
- Update `buildCard()` to map from `ClusterSummary` shape instead of mock shape
- Add new card elements: source count badge, cluster state indicator
- Wire infinite scroll to call `fetchFeed` with incremented offset
- Add error/empty state rendering
- Track current `offset`, `total`, and active filter state

### `static/js/components/filters.js` (moderate changes)
- Wire filter change events to trigger `fetchFeed()` with updated params
- Remove client-side filtering logic (API handles filtering now)
- Add sort toggle (latest vs score)
- Compute `date_from` ISO-8601 from time range selection (1h/24h/7d)

### `static/js/features/priority-filter.js` (minor)
- Hide or disable severity pills (no API support for cluster severity yet)
- Also hide Content type and Sources filter groups from the main filter dropdown
- Keep code structure for future re-enablement

### `static/partials/layout/content.html` (minor)
- Add HTML structure for source count badge and cluster state indicator on cards
- Add empty state and error state container elements

## Files NOT Changed

- Backend API (already implemented and working)
- Auth flow (feed is public/global view)
- Cluster detail page (FE-04, separate task)
- Search (FE-05, separate task)
- Other JS modules (nav, theme, language, etc.)

## Acceptance Criteria

1. Home feed loads real cluster data from `GET /api/feed` on page load
2. Each card shows cluster info (title, desc, source count, state) from the top article
3. Infinite scroll fetches next pages and appends cards
4. Time range filter triggers re-fetch with correct `date_from`
5. Category filter triggers re-fetch with correct `category` param
6. Sort toggle switches between latest and score ordering
7. Empty state shown when no clusters match filters
8. Error state shown with retry on API failure
9. No mock data remains in the rendering path
