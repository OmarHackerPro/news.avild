# Clustering False Merge Fix

**Date:** 2026-04-20  
**Status:** Approved

## Problem

The entity-overlap clustering step causes completely unrelated articles to merge into the same cluster. Three failure modes identified from live data:

1. **Generic vendor buckets** — `vendor`-type entities like `microsoft`, `apache`, `signal`, `google` appear in almost every security article. Any two articles sharing 2+ of these get merged regardless of topic. Examples: 11-source "Researcher Discovers WhatsApp View Once Bypass" bucket (entities: `adobe`, `zoom`), 13-source "Threat Intelligence Automation" bucket (entities: `citrix`, `netscaler`).

2. **CISA advisory pile-ups** — ICS advisories for completely unrelated hardware products (EV chargers, SCADA controllers, IP cameras) merge because their articles mention the same generic software vendors as components. Example: 35-source "EV2GO ev2go.io" cluster containing Hitachi Energy, PX4 Autopilot, Yokogawa, and Ivanti advisories.

3. **CVE roundup sweeping** — An article with many CVEs (Patch Tuesday, CISA KEV batch) seeds a cluster with 80+ CVE IDs. Any later article mentioning any of those CVEs gets pulled in. Example: 42-source "Patch Tuesday, April 2026" cluster containing unrelated stories like "North Korea Axios npm" and "nginx-ui exploit" that happened to mention a shared CVE.

## Non-goals

- Under-clustering of same-story articles without shared specific entities (e.g., two outlets covering the same policy announcement). This is deferred to a future AI/semantic similarity layer.
- No schema changes to stored cluster documents — `entity_keys` on cluster docs stays as-is for display and search.

## Design

### Two changes, two files

**`app/ingestion/clusterer.py`** — matching logic  
**`app/ingestion/ingester.py`** — one call site update

Nothing else changes. Stored cluster documents, entity display, search, and API responses are unaffected.

---

### Change 1: Entity type filter (fixes failure modes 1 & 2)

Add a constant defining which entity types carry enough specificity to be clustering signals:

```python
_SIGNAL_TYPES = frozenset({"cve", "product", "malware", "actor", "tool"})
```

`vendor` is deliberately excluded. Vendors like Microsoft, Google, Apache, and Signal appear in nearly every security article and are meaningless as cluster discriminators. Products (FortiGate, vCenter, Exchange), malware families (LockBit, Emotet), threat actors (Lazarus Group, Scattered Spider), and CVE IDs are specific enough.

**`cluster_article()` signature change:**

```python
# Before
async def cluster_article(article, slug, entity_keys: list[str]) -> None:

# After
async def cluster_article(article, slug, entities: list[dict]) -> None:
```

Internally derives two lists:
- `entity_keys` = all normalized keys → used for storage in `create_cluster` / `merge_into_cluster` (no change to what gets stored)
- `signal_keys` = keys where `entity["type"] in _SIGNAL_TYPES` → used for `find_cluster_by_entities` matching only

**`ingester.py` call site:**

```python
# Before
entity_keys = [e["normalized_key"] for e in entities]
await cluster_article(article, article["slug"], entity_keys)

# After
await cluster_article(article, article["slug"], entities)
```

---

### Change 2: CVE count cap (fixes failure mode 3)

```python
_MAX_ARTICLE_CVES_FOR_MATCHING = 3
```

In `cluster_article`, CVE-based cluster lookup is skipped when the article has more than 3 CVEs:

```python
if cve_ids and len(cve_ids) <= _MAX_ARTICLE_CVES_FOR_MATCHING:
    cluster_id = await find_cluster_by_cve(cve_ids)
```

Rationale: an article with >3 CVEs is a roundup (Patch Tuesday, CISA KEV batch, vendor patch summary). These should not trigger CVE-based clustering because their CVE lists are too broad to be a meaningful topic signal. Single-CVE and dual-CVE articles (specific advisories, targeted exploits) still cluster correctly.

---

## Expected outcomes

| Cluster (before fix) | Root cause | After fix |
|---|---|---|
| "Researcher Discovers WhatsApp Bypass" (11src) | `adobe`, `zoom` are vendor type | Vendors excluded from signal_keys → entity match doesn't fire |
| "Threat Intelligence Automation" (13src) | `citrix` vendor, `netscaler` product — only 1 signal key | Already fails ≥2 requirement; no change needed |
| "EV2GO ev2go.io" (35src) | Generic vendor/product overlap across ICS advisories | Vendors stripped → insufficient signal overlap |
| "Patch Tuesday" (42src) | 80 CVEs sweep in unrelated articles | >3 CVE cap → CVE matching skipped for roundup articles |
| Scattered Spider arrest (2src, correct) | `scattered-spider` is actor type | ✓ Kept — actor type is in _SIGNAL_TYPES |
| Specific FortiGate CVE (correct merges) | Single CVE, product entity | ✓ Both unchanged |
| LockBit coverage (correct merges) | `lockbit` is malware type | ✓ Kept — malware type is in _SIGNAL_TYPES |

## What this does not fix

- Same-story articles without specific entities (policy news, breach disclosures with no named CVE/actor) will remain as separate single-source clusters. This is acceptable — the right fix is AI semantic similarity, not more rules.
- Existing incorrectly-merged clusters in OpenSearch are not backfilled. The fix applies to new articles ingested going forward.
