# Design: Normalizer Consolidation & Feed Robustness

## Problem

The ingestion pipeline has 7 normalizer functions, but 4 of them (`thn`, `bleepingcomputer`, `securityweek`, `krebs`) are nearly identical to `generic`. The only meaningful differences — WordPress footer stripping (SecurityWeek) and image extraction from `content:encoded` (Krebs) — should be universal behaviors. Additionally, 12 new feeds need to be integrated without writing per-feed normalizers, and the pipeline has no protection against unknown fields crashing OpenSearch indexing.

## Decisions

1. **Drop 4 invalid URLs** from the feed list: podcast feed, HTML page, dead site, duplicate.
2. **Extract images** from `media:thumbnail`, `media:content`, `enclosure`, `featuredImage`, and `<img>` tag fallback in the generic normalizer.
3. **Move WordPress footer stripping** into the generic normalizer (harmless on non-WP feeds).
4. **Consolidate normalizers**: delete `normalize_thn`, `normalize_bleepingcomputer`, `normalize_securityweek`, `normalize_krebs`. Alias old registry keys to `normalize_generic`. Keep `normalize_cisa_news` and `normalize_cisa_advisory`.
5. **Add field validation** in `_prepare_article_doc()` to strip unknown keys before indexing.
6. **Entity extraction unchanged** — CVEs, vendors, products, actors, malware, tools stay as-is.

## Feed Audit

### Feeds dropped

| URL | Reason |
|---|---|
| `unit42.paloaltonetworks.com/tag/threat-assessment/` | HTML page, not a feed |
| `feeds.megaphone.fm/unit42threatvector` | Podcast feed (audio episodes) |
| `isc.sans.edu/rssfeed.xml` (2nd occurrence) | Duplicate of existing entry |
| `threatpost.com/feed/` | Dead site, last published August 2022 |

### Feed content richness

| Feed | Format | Full Body? | Images? | WP Footer? |
|---|---|---|---|---|
| Krebs | RSS 2.0 | Yes (content:encoded) | Yes (img tags) | No |
| Schneier | Atom | Yes (content element) | No | No |
| Unit 42 | RSS 2.0 | No (teaser) | Yes (featuredImage) | Yes |
| DFIR Report | RSS 2.0 | No (truncated) | No | No |
| SANS ISC | RSS 2.0 | Partial (content:encoded) | No | No |
| Troy Hunt | RSS 2.0 | Unknown (FeedBurner) | Unknown | Unknown |
| Didier Stevens | Atom | Yes (content element) | No | Yes (WP.com) |
| Dark Reading | RSS 2.0 | No (teaser) | Yes (media:thumbnail) | No |
| Google TI | RSS 2.0 | Yes (content:encoded) | Yes (media) | No |
| PortSwigger | RSS 2.0 | No (teaser) | Yes (media:thumbnail) | No |
| Recorded Future | RSS 2.0 | Yes (content:encoded) | Yes (enclosure) | No |
| Red Canary | RSS 2.0 | Teaser (brief desc) | No | Likely |
| CyberScoop | Atom | Yes (content element) | No | No |

## Changes by File

### `app/ingestion/normalizer.py`

**New helper: `_strip_wp_footer(text)`**

Strips WordPress syndication footers from text. Applied after HTML stripping, so the pattern matches plain text like "The post Title appeared first on SiteName." Uses `\Z` (end-of-string anchor) instead of `$` to avoid partial matches at line boundaries:
```python
re.sub(r"\s*The post .+? appeared first on .+?\.\s*\Z", "", text).strip()
```

Applied to both `desc` and `summary` in the generic normalizer.

**New helper: `_extract_image_url(entry, content_html)`**

Extracts article image URL with priority:
1. `entry.media_thumbnail` — feedparser normalizes `media:thumbnail` here
2. `entry.media_content` — feedparser normalizes `media:content` here
3. `entry.links` — look for enclosures with `type` starting with `image/`
4. `entry.featuredimage` or `entry.featuredImage` — custom field (Unit 42)
5. `_extract_first_image(content_html)` — fallback, scans `<img>` tags

First match wins. Returns `Optional[str]`.

Note: `featuredImage` access via feedparser needs verification against actual Unit 42 feed XML at implementation time. feedparser may normalize it to `entry.featuredimage` (lowercase) or may not expose it at all if it's in a custom namespace. The `<img>` tag fallback (step 5) covers this case if feedparser doesn't expose the custom field.

**Enhanced `normalize_generic`**

Content body extraction priority:

- If `entry.content[0].value` exists, always prefer it as `content_html` (this is where feedparser puts `content:encoded` for RSS and `<content>` for Atom). Derive `summary` from stripped text.
- Use `entry.summary` / `entry.description` for `desc` (short excerpt for list views)
- If `entry.content` is absent, fall back to `entry.summary` / `entry.description` for both `content_html` and `summary`
- This correctly handles RSS 2.0 with `content:encoded`, Atom with `content` element, and plain RSS with only `description`

**CVE extraction in generic normalizer:** The old `normalize_krebs` extracted CVE IDs from content and tags into the article doc's `cve_ids` field. The enhanced generic normalizer will also do this — call `_extract_cve_ids()` on `content_html` + tag text and populate `cve_ids`. This ensures CVE IDs are available both on the article doc (for OpenSearch filtering) and in the entities index (via downstream entity extraction).

WordPress footer stripping applied to `desc` and `summary`.

Image extraction via `_extract_image_url()`.

**Deleted functions:**
- `normalize_thn`
- `normalize_bleepingcomputer`
- `normalize_securityweek`
- `normalize_krebs`

**Updated registry:**
```python
NORMALIZER_REGISTRY = {
    "generic":          normalize_generic,
    "thn":              normalize_generic,
    "bleepingcomputer": normalize_generic,
    "securityweek":     normalize_generic,
    "krebs":            normalize_generic,
    "cisa_news":        normalize_cisa_news,
    "cisa_advisory":    normalize_cisa_advisory,
}
```

### `app/ingestion/ingester.py`

**Field validation in `_prepare_article_doc()`:**

```python
from app.db.opensearch import NEWS_MAPPING
_ALLOWED_FIELDS = frozenset(NEWS_MAPPING["mappings"]["properties"].keys())
```

Requires renaming `_NEWS_MAPPING` to `NEWS_MAPPING` in `opensearch.py` (or adding it to `__all__`) to make it importable.

After building the doc dict, strip any keys not in `_ALLOWED_FIELDS` with a warning log per removed key. This prevents `dynamic: "strict"` indexing crashes from unexpected normalizer output.

Note: the `source_id` field in the mapping is intentionally unused — normalizers set `source_name` (string) instead. It remains in the mapping for potential future use.

### `app/ingestion/sources.py`

**12 new entries added to `SEED_SOURCES`:**

| name | url | default_type | default_category | normalizer |
|---|---|---|---|---|
| Schneier on Security | schneier.com/feed/atom/ | analysis | deep-dives | generic |
| Unit 42 | unit42.paloaltonetworks.com/feed/ | analysis | research | generic |
| The DFIR Report | thedfirreport.com/feed/ | report | deep-dives | generic |
| SANS ISC | isc.sans.edu/rssfeed_full.xml | analysis | research | generic |
| Troy Hunt | feeds.feedburner.com/TroyHunt | analysis | deep-dives | generic |
| Didier Stevens | blog.didierstevens.com/feed/atom/ | analysis | research | generic |
| Dark Reading | darkreading.com/rss.xml | news | breaking | generic |
| Google Threat Intelligence | cloudblog.withgoogle.com/topics/threat-intelligence/rss/ | report | research | generic |
| PortSwigger Research | portswigger.net/research/rss | analysis | research | generic |
| Recorded Future | recordedfuture.com/feed | report | research | generic |
| Red Canary | redcanary.com/blog/feed/ | analysis | research | generic |
| CyberScoop | cyberscoop.com/feed/atom/ | news | breaking | generic |

All use `default_severity=None`.

### `app/db/opensearch.py`

Rename `_NEWS_MAPPING` to `NEWS_MAPPING` so it can be imported by `ingester.py` for field validation. No mapping changes — all needed fields already exist.

### Files NOT changed

- `entity_extractor.py` — no changes
- API routes / models — no new fields

## Deployment

The 4 "dropped" feeds were candidates considered during the feed audit but were never added to `SEED_SOURCES` or the database — no removal needed.

To deploy the 12 new feeds on an existing environment:

1. Re-run `python scripts/seed_sources.py` — inserts new sources by name (existing sources are skipped via `on_conflict_do_nothing`).
2. Note: `seed_sources.py` uses `on_conflict_do_nothing(index_elements=["name"])`. If you need to update an existing source's URL, do it manually in Postgres — re-seeding will not overwrite existing records.
3. Restart the app to pick up normalizer code changes. `ensure_indexes()` handles any mapping updates on startup.

## What this does NOT include

- No scraping for teaser-only feeds (deferred — feeds like THN, Dark Reading, DFIR Report will have short summaries until scraping is added)
- No NIST API integration for CVSS scores (deferred)
- No MITRE ATT&CK technique extraction (deferred)
- No AI-generated summaries (deferred)

## Verification

1. After changes, run ingestion for all 18 feeds — verify no indexing errors
2. Spot-check a Krebs article: should have `content_html`, `summary`, `image_url` populated (same as before consolidation)
3. Spot-check a Unit 42 article: should have `image_url` from `featuredImage`, WP footer stripped from `desc`/`summary`
4. Spot-check a Dark Reading article: should have `image_url` from `media:thumbnail`
5. Spot-check a Schneier article: should have full `content_html` from Atom `content` element
6. Intentionally add a bad field to a test article dict — verify it's stripped with warning log, not crash
7. Existing articles (ingested before consolidation) should be unaffected — `op_type="create"` skips duplicates
