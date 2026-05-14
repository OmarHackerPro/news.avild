# Roundup Exclusion Design

**Date:** 2026-05-14  
**Status:** Approved

## Problem

Weekly/monthly digest articles (e.g. "March 2026 CVE Landscape: 31 High-Impact Vulnerabilities") form clusters that score artificially high due to large CVE counts and credible sources. These are low-nuance aggregates, not breaking news. They inflate the brief and pollute the website cluster list.

## Detection Heuristic

A cluster is a roundup if either condition is true at creation time:

1. Label (case-insensitive) contains any of: `patch tuesday`, `monthly`, `landscape`, `roundup`, `weekly digest`
2. `len(cve_ids) > 10`

Detection runs only in `create_cluster()`. A cluster's roundup status is set at birth and never re-evaluated.

## Changes

### 1. `app/db/opensearch.py`
Add to `_CLUSTERS_MAPPING` properties:
```python
"is_roundup": {"type": "boolean"},
```
`ensure_indexes()` calls `put_mapping` on startup — field goes live on next container restart, no rebuild required.

### 2. `app/ingestion/clusterer.py`
- Add `_is_roundup(label: str, cve_ids: list[str]) -> bool` helper
- Set `"is_roundup": _is_roundup(label, cve_ids)` in `create_cluster()` doc body

### 3. `app/briefing/selector.py`
Add to `fetch_top_clusters()` query:
```python
"must_not": [{"term": {"is_roundup": True}}]
```

### 4. `app/api/routes/clusters.py`
Add the same `must_not` clause permanently to `list_clusters()` — roundups never shown on the website.

## Behavior on Existing Data

Existing clusters have no `is_roundup` field. OpenSearch `term` on a missing field returns no match, so `must_not: term: is_roundup: true` passes them through — existing roundup clusters remain visible until the next `--reset` rebuild. No backfill needed.

## Out of Scope

- No API query param to opt-in to seeing roundups
- No change to `merge_into_cluster()` — roundup status is immutable after creation
- No change to the scoring function — exclusion is done at query time, not score time
