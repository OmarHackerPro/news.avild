# Article Body Extraction Pipeline

**Date:** 2026-05-07
**Status:** Approved
**Depends on:** —
**Unblocks:** Daily WhatsApp Brief pipeline (separate spec, future)

---

## Problem

Article body coverage in `news_articles` is too low to support LLM-grounded editorial generation. Measurement on 2026-05-07 (300 most-recent articles):

| Metric | Value |
|---|---|
| Median `content_html` length | 371 chars |
| ≥500 chars (minimum useful) | 23% |
| ≥1500 chars (LLM-groundable) | 18% |
| ≥3000 chars (full article) | 14% |
| `body_quality="empty"` (extraction attempted, failed) | 27% |
| No `body_quality` field at all (never ran) | 72% |

The vast majority of articles are RSS excerpts. Without full body, downstream features that require article-level text (notably the planned daily brief's LLM editorial step) can't function without high hallucination risk.

Goal: build a body extraction pipeline that fetches and stores clean main-content text for ~80%+ of articles from high-credibility sources, with safety rails so it can run without disrupting live ingestion.

---

## Scope

In scope:

- Fetch + extract main article text from URLs already known to the system (i.e., URLs ingested via existing RSS feeds)
- Inline extraction at ingest for new articles
- One-shot batch backfill for existing ~1,848 articles
- Per-source quality thresholds and failure handling
- Standard anti-bot tier (browser headers + cookie persistence + TLS fingerprinting)
- Robots.txt respect
- Idempotent retry with exponential backoff

Out of scope (deferred sub-projects):

- **Crawling/discovery** (Scrapy framework) — no spidering, no following links, no discovering new URLs without RSS. Future sub-project; this spec leaves the door open architecturally (Trafilatura is the extraction step Scrapy would call).
- **JS-rendered sites** via Playwright — opt-in fallback. Added later once measurement reveals which sources need it.
- **Daily brief pipeline** — separate spec; this work is its dependency.
- **Ingest of new sources without RSS** (Twitter/X, Telegram, GitHub Security Advisories) — separate sub-projects.

---

## Decisions

- **Extraction library:** Trafilatura (Python). Purpose-built for news main-content extraction, fast, accurate on 80%+ of news sites. Returns clean text + extraction quality metadata.
- **Architecture:** Hybrid — inline at ingest for new articles + one-shot batch script for backfill of existing articles.
- **Anti-bot tier:** Standard — browser User-Agent, standard headers (`Accept`, `Accept-Language`, `Accept-Encoding`), cookie persistence per host, curl-cffi for TLS fingerprinting, exponential backoff on `429`/`503`. No CAPTCHA solving, no proxy rotation.
- **Storage:** `desc` keeps the RSS-feed-provided summary (already populated). `content_html` becomes "best available body" — RSS-provided content if it exceeds the per-source threshold, otherwise extraction result, otherwise the RSS excerpt. Brief pipeline reads `content_html`, falls back to `desc`.
- **Quality classification:** Char-length thresholds with per-source overrides.
- **Retry policy:** Exponential backoff — 3 attempts: immediate → 1h → 24h.
- **Robots.txt:** Respected by default. Cached per host (TTL 24h) to avoid repeated fetches. Disallowed URLs marked `body_quality="failed"`, `body_fetch_error="robots-disallowed"`.
- **Skip-when-RSS-is-good:** If RSS-provided content is already ≥ per-source threshold, skip extraction (mark `body_source="rss-full"`). Avoids redundant fetches.
- **Rollout:** Pilot on ~100 stratified articles → measure → fix obvious issues → full backfill.
- **Launch criteria for brief unblock:** ≥50% of articles in last 7 days have `body_quality="ok"` AND top 5 high-credibility sources at ≥80%.

---

## Quality Classification

Three tiers based on extracted body char length:

| Tier | Default threshold | Meaning |
|---|---|---|
| `ok` | `len ≥ 1500` chars | LLM-groundable; brief pipeline uses for editorial |
| `weak` | `500 ≤ len < 1500` | Indexed for search, not used for LLM grounding |
| `empty` | `len < 500` chars | RSS excerpt only; treated as failure for grounding |

`failed` is a fourth state (orthogonal): extraction attempted and produced an error (HTTP 4xx/5xx, timeout, robots disallow, Cloudflare challenge).

**Per-source overrides:** New `feed_sources.min_body_chars` column (nullable int). When set, overrides the global `1500` threshold for that source. Initial values to seed:

| Source pattern | min_body_chars | Reason |
|---|---|---|
| MSRC (Microsoft Security Response Center) | 200 | Short technical advisories are normal |
| NVD (NIST National Vulnerability Database) | 200 | Short CVE entries are normal |
| CISA advisories | 400 | Mid-length advisories |
| Krebs on Security | 800 | Mix of short alerts and deep-dives |
| (everything else) | 1500 (global default) | Standard news article length |

Per-source values are seeded by Alembic migration; future tuning happens via Postgres update.

---

## Architecture

### Inline path (new articles)

After `upsert_article()` in `app/ingestion/ingester.py`, add an extraction step:

```python
async def maybe_extract_body(article_doc: dict, source: dict) -> dict:
    """Returns updates dict to apply to article doc."""
    threshold = source.get("min_body_chars") or 1500

    # Skip if RSS already provided enough content
    rss_content = article_doc.get("content_html") or ""
    if len(rss_content) >= threshold:
        return {
            "body_source": "rss-full",
            "body_quality": _classify_length(len(rss_content), threshold),
            # content_html stays as-is
        }

    # Otherwise: fetch + extract
    return await _fetch_and_extract(article_doc["source_url"], threshold)
```

`maybe_extract_body()` runs synchronously inside the existing per-source ingest cycle. Per-fetch hard timeout: 8 seconds. Failure marks `body_quality="failed"` and the article still gets stored with the RSS excerpt — extraction is best-effort.

### Backfill path (one-shot script)

New file: `scripts/backfill_body_extraction.py`

Runs in its own ephemeral container (not `kiber-ingestion-1`) so it doesn't conflict with live ingest. Manually triggered (no auto-cron for now).

Logic:

```
1. Query OpenSearch: articles WHERE body_quality IS NULL OR body_quality='failed'
2. Yield in batches of 50, sorted by published_at DESC
3. asyncio.Semaphore(10) for global concurrency cap
4. Per-host concurrency = 2 (a separate per-host semaphore dict)
5. For each article: same maybe_extract_body() flow as inline
6. Buffer 50 article updates → bulk-upsert to OpenSearch
7. Log progress every 100 articles processed (count, success rate, current source)
8. Resumable: re-running picks up where left off (idempotent on body_quality state)
```

### Failure metadata schema

New fields on `news_articles` mapping:

```json
{
  "body_quality": "ok | weak | empty | failed | null",
  "body_source": "rss-full | rss-excerpt | trafilatura | failed | null",
  "body_fetch_error": "string (HTTP code or error class)",
  "last_fetch_attempt_at": "date",
  "fetch_attempt_count": "integer"
}
```

`body_fetch_error` examples: `"403"`, `"404"`, `"timeout"`, `"cloudflare-challenge"`, `"empty-result"`, `"robots-disallowed"`, `"connection-error"`.

### Retry policy (r3)

`fetch_attempt_count` tracks attempt count. `last_fetch_attempt_at` tracks when. Eligibility for retry:

| Attempt # | Eligible after |
|---|---|
| 1 | Immediate (initial attempt) |
| 2 | 1h after attempt 1 |
| 3 | 24h after attempt 2 |
| ≥3 | No more retries; manual intervention required |

The retry scheduler is the same backfill script — it just queries `body_quality='failed' AND last_fetch_attempt_at < (now - threshold)`. No separate retry queue.

---

## Anti-Bot Tier (Standard)

Implementation: dedicated `app/ingestion/body_fetcher.py` module.

**Fetch client:** `curl_cffi.AsyncSession` with browser TLS fingerprint (Chrome 120 default). Falls back to `httpx.AsyncClient` if `curl-cffi` import fails (graceful degradation).

**Headers:**

```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
            (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8
Accept-Language: en-US,en;q=0.9
Accept-Encoding: gzip, deflate, br
DNT: 1
Connection: keep-alive
Upgrade-Insecure-Requests: 1
```

**Cookie jar:** Persistent per host (in-memory dict keyed by hostname, lifetime = process lifetime). Sites that set tracking cookies on first request get them on subsequent requests, looking more like a real browser session.

**HTTP/2:** Enabled (curl-cffi defaults).

**Retry on transient errors:**

- `429 Too Many Requests`: respect `Retry-After`, exponential backoff, max 1 retry within the same fetch attempt
- `503 Service Unavailable`: same
- `502/504 Bad Gateway/Gateway Timeout`: 1 retry with 2s delay
- Connection errors: 1 retry with 2s delay

**Cloudflare challenge detection:** If response HTML contains `<title>Just a moment...</title>` or `cf-chl-` script reference, mark `body_fetch_error="cloudflare-challenge"`. No solver. Source flagged for future Playwright opt-in. Add to TODO

**Robots.txt:** Fetched once per host, cached 24h. URL is checked against parsed rules before fetch. Disallowed → `body_fetch_error="robots-disallowed"`, no fetch attempt.

---

## Rollout

Three phases.

### Phase 1: Pilot (~1 hour)

`scripts/backfill_body_extraction.py --pilot` runs against a stratified sample: 3–5 articles per source (top 25 sources by article count). Total ~100 fetches.

Expected output:

- Per-source success rate
- Per-source error code distribution
- Average extracted length per source
- Cloudflare/anti-bot hits per source

Used to:

- Adjust `feed_sources.min_body_chars` if a source has systematic short bodies
- Identify sources needing future Playwright opt-in (>50% Cloudflare hits)
- Find any obvious bugs (regex, parser failures, etc.)

### Phase 2: Full backfill (~30–60 min)

After pilot fixes, run `scripts/backfill_body_extraction.py` on all eligible articles. Manual kick-off, ideally overnight in low-traffic window. Resumable on interruption.

### Phase 3: Inline extraction live

Inline extraction enabled in `ingester.py` after backfill confirms working pipeline. New articles get body extraction during normal ingest cycle. No separate launch event — it's just code that runs.

### Launch criteria (unblocks Daily Brief sub-project)

Brief Phase 1 work begins when measurement query returns:

1. ≥50% of articles published in last 7 days have `body_quality="ok"`, AND
2. Top 5 sources by `credibility_weight` each have ≥80% `body_quality="ok"` for last-7-day articles

Both must hold. (1) without (2) means extraction works broadly but misses high-credibility sources we'd actually pick from. (2) without (1) means extraction works only for premium sources but we'd want broader coverage.

---

## Database Migration

New Alembic migration (Postgres):

```sql
ALTER TABLE feed_sources
ADD COLUMN min_body_chars INTEGER NULL;

UPDATE feed_sources SET min_body_chars = 200 WHERE name ILIKE '%MSRC%' OR name ILIKE '%Microsoft Security%';
UPDATE feed_sources SET min_body_chars = 200 WHERE name ILIKE '%NVD%' OR url LIKE '%nist.gov%';
UPDATE feed_sources SET min_body_chars = 400 WHERE name ILIKE '%CISA%';
UPDATE feed_sources SET min_body_chars = 800 WHERE name ILIKE '%Krebs%';
```

Other sources remain `NULL`, falling back to global default.

OpenSearch index mapping update for `news_articles` (add new fields). Idempotent — existing fields stay; new fields:

```json
{
  "body_fetch_error": { "type": "keyword" },
  "last_fetch_attempt_at": { "type": "date" },
  "fetch_attempt_count": { "type": "integer" }
}
```

`body_quality`, `body_source` already exist in mapping.

---

## Failure Modes

| Failure | Behavior |
|---|---|
| Source URL returns 4xx | Mark `failed` with HTTP code; retry per r3 schedule |
| Source URL returns 5xx | Mark `failed` with HTTP code; retry per r3 schedule |
| Connection timeout (>8s) | Mark `failed` with `"timeout"`; retry per r3 |
| Cloudflare challenge detected | Mark `failed` with `"cloudflare-challenge"`; flag source for review |
| Trafilatura returns empty result | Mark `failed` with `"empty-result"`; retry per r3 |
| Robots.txt disallows URL | Mark `failed` with `"robots-disallowed"`; do not retry (no point) |
| Extraction succeeds but body < threshold | Mark `weak` or `empty`; do not retry (already got the content, it's just short) |
| Source becomes systematically blocked (>20% failure rate over 100 articles) | Future Playwright opt-in candidate; alert during pilot phase |

---

## Observability

Backfill script prints to stdout (captured by container logs):

```
[2026-05-07 22:14:01] PROGRESS 100/1848 (5.4%) | success: 76 | weak: 8 | failed: 16
                       current_source: BleepingComputer | rate: 28 articles/min
[2026-05-07 22:15:30] WARNING source 'XYZ Blog' has 4/5 cloudflare-challenge — flag for Playwright
```

Inline path uses existing logger (`app.ingestion.ingester`):

```
INFO  ingester.body_extraction: source=Krebs slug=foo result=ok length=2843
WARN  ingester.body_extraction: source=XYZ slug=bar result=failed error=cloudflare-challenge
```

No metrics export to Prometheus/etc. for this version — log-based monitoring sufficient at current scale.

---

## Open Items / Future Work

- Playwright fallback (per-source opt-in via `feed_sources.fetch_strategy`) — added when pilot reveals which sources warrant the cost
- `feed_sources.disabled_extraction` flag — for sources we've decided are paywalled or otherwise unfetchable, to skip retry attempts
- Incremental re-extraction when Trafilatura version changes (currently no version tracking on extraction results)
- Per-host rate limit tuning (currently 2 concurrent — may need lower for some sources, higher for others)

---

## Acceptance Criteria

The body extraction pipeline is "done" when:

1. Inline extraction is live in `ingester.py`; new articles get body extraction during normal ingest
2. Backfill script has been run successfully against existing articles
3. Launch criteria met: ≥50% of last-7-day articles at `body_quality="ok"`, top 5 credibility sources at ≥80%
4. Failure modes from the table above are observable in logs (not silent)
5. Robots.txt is respected (verifiable on a robots-blocked test URL)
6. The pipeline is documented enough that another engineer can answer: "what library does extraction?", "where does it run?", "how do I run a backfill?"
