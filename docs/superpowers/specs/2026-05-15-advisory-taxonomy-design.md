# Advisory Content Type Taxonomy — Design

> Approved: 2026-05-14 (brainstorm session). Implementation planned: 2026-05-15.

## Problem

Advisory feeds create three distinct noise problems in the cluster listing:

1. **ICS advisory noise** — CISA Advisories (`ICSA-*`) produce ~146 solo clusters about Siemens, Yokogawa, etc. with no narrative value for a general security audience.
2. **KEV catalog merging** — "CISA Adds N Known Exploited Vulnerabilities to Catalog" articles share CVE IDs with real incident clusters, causing false merges and diluting cluster quality.
3. **Product advisory seeding** — Cisco Security Advisories and Microsoft MSRC articles seed one-off clusters without corroborating news coverage.

## Solution: `content_type` field on articles

A `content_type: keyword` field is added to the `news_articles` OpenSearch mapping. It is inferred at normalization time based on source and title pattern, never stored in the feed source config.

| content_type | Sources | Cluster behavior |
|---|---|---|
| `news` | All generic sources (default) | Full participation — creates and merges |
| `threat_advisory` | CISA News, NCSC UK | Full participation (these are narrative, high-value) |
| `ics_advisory` | CISA Advisories feed (`cisa_advisory` normalizer) | Creates cluster with `is_advisory: true` |
| `product_advisory` | Cisco Security Advisories, Microsoft MSRC | Merge only — never seeds new cluster |
| `kev_catalog` | Any source whose title matches "Adds N Known Exploited...Catalog" | Annotates matching clusters with `cisa_kev: true`; no cluster created |

## Inference logic

`_infer_content_type(article, normalizer_key)` in `normalizer.py`:

1. Title matches `r"adds\s+(?:\d+|one|two|three)\s+known\s+exploited"` (case-insensitive) → `kev_catalog`
2. `normalizer_key == "cisa_advisory"` → `ics_advisory`
3. `source_name in {"Cisco Security Advisories", "Microsoft MSRC"}` → `product_advisory`
4. `normalizer_key == "cisa_news"` or `source_name == "NCSC UK"` → `threat_advisory`
5. Default → `news`

## Cluster visibility

`is_advisory: bool` is added to `_CLUSTERS_MAPPING`. Set to `True` in `create_cluster()` when the seeding article has `content_type == "ics_advisory"`. The `/api/clusters/` endpoint adds `must_not: [{term: {is_advisory: True}}]` to its query — ICS advisory clusters exist in OpenSearch but are hidden from the main listing. The `is_roundup` precedent is followed exactly.

## JPCERT/CC cleanup

JPCERT/CC publishes primarily in Japanese. The source is disabled (`is_active=False` in Postgres) and its articles are removed from OpenSearch. Solo clusters (article_count=1) whose sole member is a JPCERT article are also deleted. Source remains in SEED_SOURCES with a comment (historical record); the seed script does not touch `is_active`, so re-seeding is safe.

## Out of scope (future)

- ICS advisory dedicated page/section
- Corroboration badge UI ("CISA KEV ✓", "MSRC ✓") on cluster cards
- "Unpatched" indicator when Microsoft has not yet issued a fix
- MSRC re-ingestion flow now that articles are `product_advisory` (merge-only)
