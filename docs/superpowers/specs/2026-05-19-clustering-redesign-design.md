# Clustering Redesign — Founding Identity + Cluster Type

_2026-05-19_

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three distinct cluster inflation failure modes so that every confirmed cluster represents a single, bounded security event — making clusters usable as the unit of content for daily CISO briefs.

**Architecture:** Freeze cluster identity at birth via `founding_entity_keys`. Score new articles against founding signal only, not accumulated noise. Ring-fence roundup articles from real clusters. Add `cluster_type` for brief section routing.

**Tech Stack:** Python 3.12, FastAPI async, opensearch-py, numpy, app/ingestion/clusterer.py, app/ingestion/unified_scorer.py, app/ingestion/entity_idf.py, app/db/opensearch.py

---

## Problem

Three failure modes, one root cause: the scoring system has no concept of cluster identity. A 1-article cluster and a 300-article cluster are scored identically. As clusters absorb articles they grow more general, but scoring compares incoming articles against accumulated noise rather than the founding signal.

| Cluster | Size | Failure mode | Root cause |
|---|---|---|---|
| DeceptiveDevelopment | 352 | Actor cascade | Binary actor overlap: any 1 of 491 actors = 1.0 |
| CISA Roundcube | 64 | CVE accumulation | Roundup articles merged in, adding CVEs; more CVEs → more roundups |
| Agentic AI | 17 | Embedding drift | No entity anchor; centroid drifts to general topic |
| ISC Stormcast | 30 | Pure embedding | Daily podcast has no entities; merges on title similarity |

These clusters are unusable in daily briefs. A CISO brief built on a 352-article cluster labelled "DeceptiveDevelopment" that contains "18th May Threat Intelligence Report" and "npm supply chain packages" is not a brief — it's noise.

---

## Non-goals

- Global graph clustering (Phase 2 experiment — stays noted in memory, not touched here)
- Changes to `scorer.py` (public scores unchanged)
- Changes to the API surface (additive fields only, no breaking changes)
- LLM-based cluster quality classification
- Retroactive cluster splitting (fixing existing clusters requires a `--reset` rebuild, which is standard dev workflow)

---

## Five Changes

### Change 1 — `founding_entity_keys` field

**What:** New `keyword[]` field on the cluster document, set once at `create_cluster()` from the founding article's entity keys. Never mutated after that.

**Where it lives:** `app/db/opensearch.py` (mapping), `app/ingestion/clusterer.py` (set at create), `app/ingestion/unified_scorer.py` (used in scoring).

**How scoring changes:** `_compute_score()` in `unified_scorer.py` currently reads overlap signals from `cluster_source.get("entity_keys")` and `event_signature`. Replace the entity/actor/CVE overlap sources with `founding_entity_keys` split by type:

- `founding_cves` — founding keys that match `CVE-*` pattern (uppercase)
- `founding_vuln_aliases` — founding keys whose type is `vuln_alias` (stored in `founding_entity_types` map, see below)
- `founding_actors_campaigns` — founding keys of type `actor` or `campaign`
- `founding_others` — remaining (product, tool, malware)

**`entity_keys` still grows** with every merge. It is used for OpenSearch retrieval (`_structured_lookup` terms query) so incoming articles still find the cluster as a candidate. It is no longer used for scoring.

**`event_signature` still grows** with every merge in `merge_into_cluster()`. It is used for display on the cluster detail page (primary_actors, campaign_names, etc.) and for the confidence label. It is no longer used for overlap scoring — `founding_entity_types` replaces it for that purpose.

**`founding_entity_types`:** To split `founding_entity_keys` by type at score time without re-fetching article data, store a parallel `founding_entity_types` field: a list of `{key, type}` objects set alongside `founding_entity_keys` at create time. Added to `_SOURCE_FIELDS` in `unified_scorer.py`.

**Impact:**
- DeceptiveDevelopment: founding signal = `{deceptivedevelopment, lazarus, contagious-interview}`. Incoming article with `{north-korea, kimsuky, apt38}` scores against 3 founders, not 491 accumulated actors.
- CISA Roundcube: founding signal = `{roundcube, winter-vivern}`. Patch Tuesday articles have no Roundcube overlap — near-zero score.

---

### Change 2 — IDF-weighted Jaccard for actors/campaigns

**What:** Replace the binary actor/campaign overlap signal with IDF-weighted Jaccard, identical in structure to the `entity_jaccard` calculation already used for other entity types.

**Current code** (`unified_scorer.py:83-84`):
```python
actor_campaign_overlap = 1.0 if art_actors_campaigns & cl_actors_campaigns else 0.0
```

**New code:**
```python
union_actors = art_actors_campaigns | cl_founding_actors_campaigns
shared_actors = art_actors_campaigns & cl_founding_actors_campaigns
if union_actors:
    num = sum(idf(k) for k in shared_actors)
    den = sum(idf(k) for k in union_actors)
    actor_campaign_overlap = num / den if den else 0.0
else:
    actor_campaign_overlap = 0.0
```

Where `cl_founding_actors_campaigns` comes from `founding_entity_types` (Change 1).

**Why IDF matters:** Common actors like `north-korea`, `lazarus` have low IDF (appear in hundreds of articles). Rare, specific actors like `deceptive-development` or a specific campaign name have high IDF. A mega-cluster with 491 actors has a huge denominator — any small intersection produces a near-zero score. A tight cluster with 2 specific actors that fully overlap produces score 1.0.

**Note:** CVE overlap and vuln_alias overlap remain binary (0 or 1.0). A shared specific CVE-ID is always strong signal; there's no ambiguity cost to being binary there.

---

### Change 3 — Roundup articles are ring-fenced

**What:** In `cluster_article()`, check `_is_roundup()` on the *incoming article* before calling `find_best_cluster()`. If the incoming article is a roundup, skip the merge lookup entirely and call `create_cluster()` directly with `is_roundup=True`.

**Current flow:**
```
cluster_article()
  → find_best_cluster()   # roundup articles can still score against real clusters
  → merge_into_cluster()  # adds 23 CVEs from a CVE landscape roundup to a real cluster
```

**New flow:**
```
cluster_article()
  → if _is_roundup(article): create_cluster(..., is_roundup=True); return
  → find_best_cluster()   # only non-roundup articles reach here
  → merge_into_cluster()
```

**Why this fixes CISA Roundcube:** "January 2026 CVE Landscape" is caught by `_is_roundup()` (keyword `cve landscape` is in `_ROUNDUP_KEYWORDS`). It creates its own `is_roundup=True` cluster and never touches the CISA Roundcube cluster. Its 23 CVEs are never added to CISA Roundcube's `entity_keys`.

**ISC Stormcast after this change:** Each episode is its own `is_roundup=True` singleton. The existing 30-article Stormcast cluster only exists in current data; after `--reset` rebuild each episode is independent. The `stormcast` keyword added to `_ROUNDUP_KEYWORDS` (done 2026-05-19) handles detection.

**Roundup clusters in briefs:** `is_roundup=True` clusters are already excluded from the main brief feed. They can optionally appear in a "This week in patches" sidebar section — the `cluster_type` field (Change 5) handles that routing.

---

### Change 4 — Entity-free clusters require near-identical embedding to absorb

**What:** In `_compute_score()`, if the cluster's `founding_entity_keys` is empty, apply a higher effective threshold: the embedding signal only contributes if cosine ≥ `_EMBED_HI` (0.90). In practice, set a flag `has_entity_anchor = len(founding_entity_keys) > 0` and only add embedding contribution if `cosine ≥ _EMBED_HI or has_entity_anchor`.

**Implementation:**
```python
# Entity-free clusters only merge on near-identical embedding
embed_threshold = _EMBED_LO if has_entity_anchor else _EMBED_HI
embed_signal_val = max(0.0, min(1.0, (cosine - embed_threshold) / (_EMBED_HI - embed_threshold))) if _EMBED_HI > embed_threshold else (1.0 if cosine >= _EMBED_HI else 0.0)
```

**What this fixes:** "Are We Ready for Auto Remediation With Agentic AI?" has no entity anchor. A future AI security editorial needs cosine ≥ 0.90 to absorb into it. Two different opinion pieces about AI trends sit at cosine ~0.75 — they don't merge. A near-duplicate article (same story, different outlet) sits at cosine ~0.93 — they merge correctly.

**Also raise `_EMBED_LO`** from `0.70` → `0.75` in `unified_scorer.py` (env default). This makes embedding require stronger similarity before contributing to entity-anchored clusters too. Cosine 0.70 was too permissive for general topic similarity.

---

### Change 5 — `cluster_type` field

**What:** New `keyword` field on the cluster document. Set deterministically at `create_cluster()` with no LLM call.

**Classification rules (evaluated in order):**

```python
def _classify_cluster_type(article: dict, entities: list[dict], cve_ids: list[str]) -> str:
    if _is_roundup(article.get("title", ""), cve_ids):
        return "roundup"
    content_type = article.get("content_type", "news")
    if content_type in ("ics_advisory", "product_advisory"):
        return "advisory"
    has_cves = bool(cve_ids)
    has_actors = any(e["type"] in ("actor", "campaign") for e in entities)
    has_products = any(e["type"] in ("product", "tool", "malware") for e in entities)
    if has_cves and (has_products or not has_actors):
        return "cve_incident"
    if has_actors and not has_cves:
        return "campaign"
    if has_cves and has_actors:
        return "cve_incident"  # CVE takes precedence for routing
    return "research"  # no CVEs, no actors — editorial/analysis
```

**Type definitions:**

| Type | Brief section | Description |
|---|---|---|
| `cve_incident` | Exploit Watch | Specific CVE(s) + product context |
| `advisory` | Exploit Watch | Official advisory (CISA, ICS-CERT, vendor) |
| `campaign` | Threat Actor Activity | Named campaign or actor, no specific CVE |
| `research` | Context Reading | Analysis/editorial with no incident anchor |
| `roundup` | (excluded or sidebar) | Patch Tuesday, weekly digest, CVE landscape |

**Why this matters for briefs:** Routes clusters to brief sections without an LLM call. The brief engine reads `cluster_type` and places accordingly. `cve_incident` clusters with CVSS ≥ 7 and `cisa_kev=True` are the most urgent. `campaign` clusters give actor context. `research` from high-credibility sources (Schneier, Krebs, SANS) provides context. `roundup` is excluded from the body but optionally shown as a "Patches This Week" section.

---

## OpenSearch Mapping Changes

In `app/db/opensearch.py`, add to `CLUSTER_MAPPING["mappings"]["properties"]`:

```python
"founding_entity_keys": {"type": "keyword"},
"founding_entity_types": {
    "type": "object",
    "properties": {
        "key": {"type": "keyword"},
        "type": {"type": "keyword"},
    }
},
"cluster_type": {"type": "keyword"},
```

`ensure_indexes()` calls `put_mapping` on startup so existing clusters get the field definitions without index recreation. Existing clusters will have `null` for these fields until the next `--reset` rebuild.

---

## `_SOURCE_FIELDS` in `unified_scorer.py`

Add `"founding_entity_keys"` and `"founding_entity_types"` to `_SOURCE_FIELDS` so they are fetched in candidate retrieval:

```python
_SOURCE_FIELDS = [
    "article_count", "state", "entity_keys",
    "founding_entity_keys", "founding_entity_types",
    "centroid_embedding", "latest_at",
]
```

---

## `_compute_score` rewrite (unified_scorer.py)

Complete replacement of the overlap signal block. The new version reads founding signals instead of accumulated signals, uses IDF-weighted Jaccard for actors, and applies the entity-anchor check for embedding:

```python
def _compute_score(
    article_entities: list[dict],
    cluster_source: dict,
    article_embedding: Optional[list[float]],
) -> float:
    # --- Article signal sets ---
    art_cves = {e["normalized_key"] for e in article_entities if e["type"] == "cve"}
    art_vuln_aliases = {e["normalized_key"] for e in article_entities if e["type"] == "vuln_alias"}
    art_actors_campaigns = {
        e["normalized_key"] for e in article_entities if e["type"] in ("actor", "campaign")
    }
    art_others = {
        e["normalized_key"] for e in article_entities
        if e["type"] not in ("cve", "vuln_alias", "actor", "campaign")
    }

    # --- Founding cluster signal sets (frozen at create time) ---
    founding_types = cluster_source.get("founding_entity_types") or []
    founding_cves = {ft["key"] for ft in founding_types if ft["type"] == "cve"}
    founding_vuln_aliases = {ft["key"] for ft in founding_types if ft["type"] == "vuln_alias"}
    founding_actors_campaigns = {
        ft["key"] for ft in founding_types if ft["type"] in ("actor", "campaign")
    }
    founding_others = {
        ft["key"] for ft in founding_types
        if ft["type"] not in ("cve", "vuln_alias", "actor", "campaign")
    }
    has_entity_anchor = bool(founding_types)

    # --- Overlap signals ---
    cve_overlap = 1.0 if art_cves & founding_cves else 0.0
    alias_overlap = 1.0 if art_vuln_aliases & founding_vuln_aliases else 0.0

    # IDF-weighted Jaccard for actors/campaigns
    union_actors = art_actors_campaigns | founding_actors_campaigns
    shared_actors = art_actors_campaigns & founding_actors_campaigns
    if union_actors:
        num = sum(idf(k) for k in shared_actors)
        den = sum(idf(k) for k in union_actors)
        actor_campaign_overlap = num / den if den else 0.0
    else:
        actor_campaign_overlap = 0.0

    # IDF-weighted Jaccard for other entities
    union_others = art_others | founding_others
    shared_others = art_others & founding_others
    if union_others:
        num = sum(idf(k) for k in shared_others)
        den = sum(idf(k) for k in union_others)
        entity_jaccard = num / den if den else 0.0
    else:
        entity_jaccard = 0.0

    # --- Embedding signal ---
    cosine = 0.0
    centroid = cluster_source.get("centroid_embedding")
    if article_embedding and centroid:
        a = np.array(article_embedding, dtype=np.float32)
        c = np.array(centroid, dtype=np.float32)
        denom = np.linalg.norm(a) * np.linalg.norm(c)
        if denom > 0:
            cosine = max(0.0, float(np.dot(a, c) / denom))

    # Entity-free clusters require near-identical match
    embed_lo = _EMBED_LO if has_entity_anchor else _EMBED_HI
    if _EMBED_HI > embed_lo:
        embed_signal_val = max(0.0, min(1.0, (cosine - embed_lo) / (_EMBED_HI - embed_lo)))
    else:
        embed_signal_val = 1.0 if cosine >= _EMBED_HI else 0.0

    return (
        _W_CVE * cve_overlap
        + _W_ALIAS * alias_overlap
        + _W_ACTOR * actor_campaign_overlap
        + _W_ENTITY * entity_jaccard
        + _W_EMBED * embed_signal_val
    )
```

---

## `create_cluster` changes (clusterer.py)

Add to the `doc` dict:

```python
founding_entity_types = [
    {"key": e["normalized_key"], "type": e["type"]} for e in entities
]
doc["founding_entity_keys"] = [ft["key"] for ft in founding_entity_types]
doc["founding_entity_types"] = founding_entity_types
doc["cluster_type"] = _classify_cluster_type(article, entities, cve_ids)
```

---

## `cluster_article` changes (clusterer.py)

Add roundup ring-fence before `find_best_cluster`:

```python
async def cluster_article(article, slug, entities):
    content_type = article.get("content_type", "news")
    cve_ids = article.get("cve_ids") or []

    if content_type == "kev_catalog":
        await _mark_kev_clusters(cve_ids)
        return

    embedding = await embed_article(article, [e["normalized_key"] for e in entities])
    ref_time = _parse_published_at(article.get("published_at"))

    if cve_ids:
        if len(cve_ids) > _MAX_ARTICLE_CVES_FOR_CVE_TOPIC:
            await create_cve_topic_stubs(cve_ids)
        else:
            await upsert_cve_topics(cve_ids, slug, entities, embedding)

    # Roundup articles never merge into real clusters — they create their own
    if _is_roundup(article.get("title", ""), cve_ids):
        await create_cluster(article, entities, embedding=embedding)
        return

    cluster_id = await find_best_cluster(entities, embedding, reference_time=ref_time)
    if cluster_id:
        await merge_into_cluster(...)
    elif content_type != "product_advisory":
        await create_cluster(article, entities, embedding=embedding)
```

---

## Environment variable changes

Update `.env.example` (and `docker-compose.yml` if defaults are overridden):

```
CLUSTER_EMBED_LO=0.75   # raised from 0.70
```

All other thresholds stay at current defaults.

---

## Testing approach

### Unit tests (`tests/test_unified_scorer.py`)

| Test | Assertion |
|---|---|
| Founding signal used, not accumulated | Create mock cluster with `founding_entity_types=[{key:"lazarus", type:"actor"}]` and `entity_keys` containing 400 actors. Article with `{lazarus}` → IDF Jaccard = idf("lazarus")/idf("lazarus") = 1.0 (union = just lazarus in founding). |
| IDF-weighted actor Jaccard | 3-actor article vs 400-actor cluster → Jaccard denominator dominates → score < 0.05 |
| Entity-free high threshold | Cluster with `founding_entity_types=[]`, cosine=0.82 → embed_signal_val = 0.0 (below EMBED_HI=0.90) |
| Entity-free near-identical | Cluster with `founding_entity_types=[]`, cosine=0.92 → `embed_lo = _EMBED_HI`, so binary branch fires → `embed_signal_val = 1.0` (0.92 ≥ 0.90) → embedding contributes |
| Roundup blocked | `_is_roundup` returns True → `create_cluster` called, `find_best_cluster` never called |
| `cluster_type` classification | cve_ids=["CVE-2024-1234"], entities=[product] → `cve_incident`; actors only → `campaign`; nothing → `research` |

Note on entity-free threshold: when `embed_lo = _EMBED_HI`, the division is 0/0. Guard: use `embed_signal_val = 1.0 if cosine >= _EMBED_HI else 0.0` (binary at the high threshold). This is already handled in the code snippet above.

### Integration smoke test

After `--reset` rebuild:
- Run: `docker compose exec ingestion python scripts/cluster_articles.py --reset`
- Verify: no cluster with `article_count > 30` exists (DeceptiveDevelopment should split)
- Verify: ISC Stormcast episodes are individual `is_roundup=True` singletons
- Verify: top confirmed clusters each have a coherent `cluster_type`

---

## Files touched

| File | Change |
|---|---|
| `app/db/opensearch.py` | Add `founding_entity_keys`, `founding_entity_types`, `cluster_type` to `CLUSTER_MAPPING` |
| `app/ingestion/clusterer.py` | `create_cluster`: set founding fields + `cluster_type`. `cluster_article`: roundup ring-fence. Add `_classify_cluster_type`. |
| `app/ingestion/unified_scorer.py` | `_compute_score`: use founding signals, IDF Jaccard for actors, entity-anchor check. `_SOURCE_FIELDS`: add founding fields. Raise `_EMBED_LO` default to 0.75. |
| `tests/test_unified_scorer.py` | New unit tests per table above |
| `.env.example` | Document `CLUSTER_EMBED_LO=0.75` |
