# Clustering Redesign — Founding Identity + Cluster Type

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix cluster inflation by freezing each cluster's identity signal at birth, using IDF-weighted Jaccard for actor overlap, ring-fencing roundup articles, and adding a `cluster_type` field for brief routing.

**Architecture:** Two new cluster fields (`founding_entity_types`, `cluster_type`) set once at `create_cluster()` and never mutated. `_compute_score()` in `unified_scorer.py` switches from reading accumulated `event_signature` to reading frozen `founding_entity_types`. Roundup articles bypass `find_best_cluster()` and always create their own cluster.

**Tech Stack:** Python 3.12, opensearch-py async, numpy, pytest/pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-05-19-clustering-redesign-design.md`

---

## Codebase context (read before starting)

- `app/db/opensearch.py` — index mappings; `CLUSTER_MAPPING` ends at line 212; new fields go inside the `"properties"` block at line 210, before the closing brace.
- `app/ingestion/clusterer.py` — `_is_roundup()` at line 81; `cluster_article()` at line 104; `create_cluster()` at line 332.
- `app/ingestion/unified_scorer.py` — `_EMBED_LO` at line 28; `_SOURCE_FIELDS` at line 35; `_compute_score()` at line 48; `_embed_signal()` at line 41.
- `tests/test_clusterer.py` — tests for `cluster_article`, `create_cluster`, `_is_roundup`.
- `tests/test_unified_scorer.py` — all `_compute_score` tests use a `_make_cluster()` helper (line 7) that builds clusters with `event_signature`. This helper must be replaced in Task 3.

**Key invariant:** `entity_keys` on the cluster still grows with every merge (used for OpenSearch retrieval in `_structured_lookup`). Only `founding_entity_types` is frozen, and only it drives overlap scoring.

**Weight constants** (do not change): `_W_CVE=0.10, _W_ALIAS=0.15, _W_ACTOR=0.22, _W_ENTITY=0.18, _W_EMBED=0.35` — they sum to 1.0.

---

## Task 1: Add 3 new fields to OpenSearch cluster mapping

**Files:**
- Modify: `app/db/opensearch.py:207-210`

No test needed — `ensure_indexes()` calls `put_mapping` on startup, which is integration-tested by the running service. The strict dynamic mapping will reject writes if the field isn't declared, so correctness is verified when Task 7 runs.

- [ ] **Step 1: Add fields before the closing brace of CLUSTER_MAPPING properties**

In `app/db/opensearch.py`, find the block ending at line 210:
```python
            "is_roundup":     {"type": "boolean"},
            "is_advisory":    {"type": "boolean"},
        },
    },
}
```

Replace with:
```python
            "is_roundup":     {"type": "boolean"},
            "is_advisory":    {"type": "boolean"},
            "founding_entity_keys": {"type": "keyword"},
            "founding_entity_types": {
                "type": "object",
                "properties": {
                    "key":  {"type": "keyword"},
                    "type": {"type": "keyword"},
                },
            },
            "cluster_type": {"type": "keyword"},
        },
    },
}
```

- [ ] **Step 2: Commit**

```bash
git add app/db/opensearch.py
git commit -m "feat(clusters): add founding_entity_keys, founding_entity_types, cluster_type to mapping"
```

---

## Task 2: Add `_classify_cluster_type` to clusterer.py

**Files:**
- Modify: `app/ingestion/clusterer.py` (after `_is_roundup` at line 83)
- Test: `tests/test_clusterer.py`

This is a pure function (no I/O). Write failing tests first.

- [ ] **Step 1: Write failing tests in `tests/test_clusterer.py`**

Append to `tests/test_clusterer.py`:

```python
# ---------------------------------------------------------------------------
# _classify_cluster_type
# ---------------------------------------------------------------------------

def test_classify_cluster_type_roundup():
    from app.ingestion.clusterer import _classify_cluster_type
    article = {"title": "May 2026 Patch Tuesday", "content_type": "news"}
    assert _classify_cluster_type(article, [], ["CVE-2026-1"]) == "roundup"


def test_classify_cluster_type_advisory_ics():
    from app.ingestion.clusterer import _classify_cluster_type
    article = {"title": "ICS Advisory ICSA-26-001", "content_type": "ics_advisory"}
    assert _classify_cluster_type(article, [], []) == "advisory"


def test_classify_cluster_type_advisory_product():
    from app.ingestion.clusterer import _classify_cluster_type
    article = {"title": "Vendor Security Bulletin", "content_type": "product_advisory"}
    assert _classify_cluster_type(article, [], ["CVE-2026-9999"]) == "advisory"


def test_classify_cluster_type_cve_incident():
    from app.ingestion.clusterer import _classify_cluster_type
    article = {"title": "FortiOS RCE exploited", "content_type": "news"}
    entities = [{"type": "product", "normalized_key": "fortios"}]
    assert _classify_cluster_type(article, entities, ["CVE-2026-9999"]) == "cve_incident"


def test_classify_cluster_type_campaign():
    from app.ingestion.clusterer import _classify_cluster_type
    article = {"title": "APT29 Deploys New Backdoor", "content_type": "news"}
    entities = [{"type": "actor", "normalized_key": "apt29"}]
    assert _classify_cluster_type(article, entities, []) == "campaign"


def test_classify_cluster_type_campaign_via_campaign_entity():
    from app.ingestion.clusterer import _classify_cluster_type
    article = {"title": "Operation DreamJob targets engineers", "content_type": "news"}
    entities = [{"type": "campaign", "normalized_key": "operation-dreamjob"}]
    assert _classify_cluster_type(article, entities, []) == "campaign"


def test_classify_cluster_type_research():
    from app.ingestion.clusterer import _classify_cluster_type
    article = {"title": "How AI Is Changing Threat Detection", "content_type": "news"}
    assert _classify_cluster_type(article, [], []) == "research"


def test_classify_cluster_type_cve_wins_over_actor():
    from app.ingestion.clusterer import _classify_cluster_type
    article = {"title": "APT29 exploits CVE-2026-1234", "content_type": "news"}
    entities = [{"type": "actor", "normalized_key": "apt29"}]
    assert _classify_cluster_type(article, entities, ["CVE-2026-1234"]) == "cve_incident"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_clusterer.py::test_classify_cluster_type_roundup -v
```

Expected: `FAILED` with `ImportError: cannot import name '_classify_cluster_type'`

- [ ] **Step 3: Add `_classify_cluster_type` to `app/ingestion/clusterer.py`**

Insert after `_is_roundup` (after line 83):

```python
def _classify_cluster_type(
    article: dict, entities: list[dict], cve_ids: list[str]
) -> str:
    """Deterministic cluster type — set once at create time, never updated."""
    if _is_roundup(article.get("title", ""), cve_ids):
        return "roundup"
    content_type = article.get("content_type", "news")
    if content_type in ("ics_advisory", "product_advisory"):
        return "advisory"
    if cve_ids:
        return "cve_incident"
    if any(e["type"] in ("actor", "campaign") for e in entities):
        return "campaign"
    return "research"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_clusterer.py -k "classify" -v
```

Expected: 8 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/clusterer.py tests/test_clusterer.py
git commit -m "feat(clusters): add _classify_cluster_type pure function"
```

---

## Task 3: Update `_make_cluster` helper and all existing `_compute_score` tests

**Files:**
- Modify: `tests/test_unified_scorer.py`

The existing `_make_cluster` builds clusters with `event_signature.primary_actors`, `event_signature.cve_ids`, etc. After Task 5 rewrites `_compute_score`, it will read `founding_entity_types` instead. This task updates the helper and all calls to it so tests pass both before and after the rewrite.

**Strategy:** Replace the `_make_cluster` signature. The new version accepts `founding_types: list[dict]` (list of `{"key": ..., "type": ...}`) and derives everything from it. Also keep `entity_keys` as an optional override for retrieval-only fields (used in `test_vendor_now_counts_in_entity_score` and `test_idf_weighted_overlap_favors_rare_entity`).

- [ ] **Step 1: Replace `_make_cluster` in `tests/test_unified_scorer.py`**

Replace lines 7–34 (the entire `_make_cluster` function):

```python
def _make_cluster(
    cluster_id: str,
    founding_types: list[dict] = None,
    entity_keys: list[str] = None,
    centroid: list[float] = None,
    article_count: int = 1,
    state: str = "new",
) -> dict:
    """Build a mock cluster hit for scorer tests.

    founding_types: list of {"key": str, "type": str} — the frozen founding signal.
    entity_keys: override for the growing retrieval list (defaults to founding keys).
    """
    ft = founding_types or []
    fkeys = [f["key"] for f in ft]
    return {
        "_id": cluster_id,
        "_source": {
            "article_count": article_count,
            "state": state,
            "entity_keys": entity_keys if entity_keys is not None else fkeys,
            "founding_entity_keys": fkeys,
            "founding_entity_types": ft,
            "centroid_embedding": centroid,
        },
    }
```

- [ ] **Step 2: Update all `_make_cluster` call sites in `tests/test_unified_scorer.py`**

Replace each call below. These are exact replacements — copy them verbatim.

**`test_score_perfect_match_is_one` (line 54–65):**
```python
def test_score_perfect_match_is_one():
    from app.ingestion.unified_scorer import _compute_score

    emb = [1.0] + [0.0] * 1023
    article_entities = _make_article_entities([
        ("cve", "CVE-2024-1234"),
        ("vuln_alias", "log4shell"),
        ("actor", "apt29"),
        ("malware", "lockbit"),
    ])
    cluster = _make_cluster(
        "c1",
        founding_types=[
            {"key": "CVE-2024-1234", "type": "cve"},
            {"key": "log4shell", "type": "vuln_alias"},
            {"key": "apt29", "type": "actor"},
            {"key": "lockbit", "type": "malware"},
        ],
        centroid=emb,
    )
    score = _compute_score(article_entities, cluster["_source"], emb)
    # 0.10 + 0.15 + 0.22 + 0.18 + 0.35 = 1.0 (weights sum to 1.0)
    assert abs(score - 1.0) < 0.01
```

**`test_score_cve_overlap_only` (line 104–110):**
```python
def test_score_cve_overlap_only():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("cve", "CVE-2024-9999")])
    cluster = _make_cluster("c1", founding_types=[{"key": "CVE-2024-9999", "type": "cve"}])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.10) < 0.01
```

**`test_score_alias_overlap_only` (line 113–119):**
```python
def test_score_alias_overlap_only():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("vuln_alias", "heartbleed")])
    cluster = _make_cluster("c1", founding_types=[{"key": "heartbleed", "type": "vuln_alias"}])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.15) < 0.01
```

**`test_score_actor_overlap_only` (line 122–128):**
```python
def test_score_actor_overlap_only():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("actor", "volt-typhoon")])
    cluster = _make_cluster("c1", founding_types=[{"key": "volt-typhoon", "type": "actor"}])
    score = _compute_score(article_entities, cluster["_source"], None)
    # Single actor: IDF Jaccard = idf("volt-typhoon") / idf("volt-typhoon") = 1.0
    assert abs(score - 0.22) < 0.01
```

**`test_score_campaign_overlap_uses_actor_weight` (line 131–137):**
```python
def test_score_campaign_overlap_uses_actor_weight():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("campaign", "moveit-campaign")])
    cluster = _make_cluster("c1", founding_types=[{"key": "moveit-campaign", "type": "campaign"}])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.22) < 0.01
```

**`test_score_actor_plus_embed_exceeds_threshold` (line 140–148):**
```python
def test_score_actor_plus_embed_exceeds_threshold():
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    emb = [1.0] + [0.0] * 1023
    article_entities = _make_article_entities([("actor", "lazarus-group")])
    cluster = _make_cluster(
        "c1",
        founding_types=[{"key": "lazarus-group", "type": "actor"}],
        centroid=emb,
    )
    score = _compute_score(article_entities, cluster["_source"], emb)
    # 0.22 (actor) + 0.35 (embed) = 0.57 > 0.31 threshold
    assert score >= ASSIGN_THRESHOLD
```

**`test_score_cve_plus_alias_below_threshold` (line 151–162):**
```python
def test_score_cve_plus_alias_below_threshold():
    """CVE + alias alone no longer clears threshold — embed or entity needed."""
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    article_entities = _make_article_entities([
        ("cve", "CVE-2024-1234"),
        ("vuln_alias", "citrixbleed"),
    ])
    cluster = _make_cluster(
        "c1",
        founding_types=[
            {"key": "CVE-2024-1234", "type": "cve"},
            {"key": "citrixbleed", "type": "vuln_alias"},
        ],
    )
    score = _compute_score(article_entities, cluster["_source"], None)
    # 0.10 + 0.15 = 0.25 < 0.31
    assert score < ASSIGN_THRESHOLD
```

**`test_vendor_now_counts_in_entity_score` (line 229–235):**
```python
def test_vendor_now_counts_in_entity_score():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("vendor", "ivanti")])
    cluster = _make_cluster("c1", founding_types=[{"key": "ivanti", "type": "vendor"}])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert score > 0.0
```

**`test_idf_weighted_overlap_favors_rare_entity` (line 238–255):**
```python
def test_idf_weighted_overlap_favors_rare_entity():
    from app.ingestion import unified_scorer, entity_idf

    entity_idf._IDF_MAP.clear()
    entity_idf._IDF_MAP.update({"common-tool": 0.01, "rare-tool": 6.0, "noise": 0.01})

    article = _make_article_entities([("tool", "rare-tool"), ("tool", "noise")])
    cluster = _make_cluster("c1", founding_types=[
        {"key": "rare-tool", "type": "tool"},
        {"key": "common-tool", "type": "tool"},
    ])
    rare_score = unified_scorer._compute_score(article, cluster["_source"], None)

    article2 = _make_article_entities([("tool", "common-tool"), ("tool", "noise")])
    cluster2 = _make_cluster("c2", founding_types=[
        {"key": "common-tool", "type": "tool"},
        {"key": "rare-tool", "type": "tool"},
    ])
    common_score = unified_scorer._compute_score(article2, cluster2["_source"], None)

    assert rare_score > common_score
    entity_idf._IDF_MAP.clear()
```

**`test_find_best_cluster_returns_highest_scoring` (line 181–196):**
```python
@pytest.mark.asyncio
async def test_find_best_cluster_returns_highest_scoring():
    from app.ingestion.unified_scorer import find_best_cluster

    low_cluster = _make_cluster("c-low", founding_types=[{"key": "apt29", "type": "actor"}])
    high_cluster = _make_cluster("c-high", founding_types=[
        {"key": "apt29", "type": "actor"},
        {"key": "citrixbleed", "type": "vuln_alias"},
    ])

    article_entities = _make_article_entities([
        ("actor", "apt29"),
        ("vuln_alias", "citrixbleed"),
    ])

    with patch("app.ingestion.unified_scorer._get_candidates", new_callable=AsyncMock) as mock_cands:
        mock_cands.return_value = [low_cluster, high_cluster]
        result = await find_best_cluster(article_entities, None)

    assert result == "c-high"
```

- [ ] **Step 3: Update `test_calibration_curve_zero_below_floor` for new `_EMBED_LO=0.75`**

Replace `test_calibration_curve_zero_below_floor` (line 94–101):

```python
def test_calibration_curve_zero_below_floor():
    from app.ingestion.unified_scorer import _embed_signal

    assert _embed_signal(0.50) == 0.0   # well below floor
    assert _embed_signal(0.70) == 0.0   # still below new floor (0.75)
    assert _embed_signal(0.75) == 0.0   # at floor — formula gives (0.75-0.75)/(0.90-0.75)=0
    assert _embed_signal(0.90) == 1.0   # at ceiling
    assert _embed_signal(0.95) == 1.0   # above ceiling
    assert abs(_embed_signal(0.825) - 0.5) < 0.001  # midpoint between 0.75 and 0.90
```

- [ ] **Step 4: Run all unified_scorer tests — they will FAIL (expected)**

```bash
python -m pytest tests/test_unified_scorer.py -v 2>&1 | head -60
```

Expected: multiple failures because `_compute_score` still reads `event_signature` but clusters no longer have it. Specifically `_make_cluster` no longer passes `event_signature` to the cluster source.

This confirms the tests are correctly wired to the new data model. Proceed to Task 4.

---

## Task 4: Write new `_compute_score` tests (all will fail until Task 5)

**Files:**
- Modify: `tests/test_unified_scorer.py`

- [ ] **Step 1: Append 4 new tests to `tests/test_unified_scorer.py`**

Add after the last existing test:

```python
# ---------------------------------------------------------------------------
# New founding-identity behaviour
# ---------------------------------------------------------------------------

def test_score_uses_founding_not_accumulated():
    """Accumulated entity_keys do not drive scoring — only founding_entity_types does."""
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    # Cluster has 400 accumulated actors in entity_keys but only 1 founding actor
    big_entity_keys = [f"actor-{i}" for i in range(400)]
    cluster = _make_cluster(
        "c-mega",
        founding_types=[{"key": "deceptive-development", "type": "actor"}],
        entity_keys=big_entity_keys,
    )
    # Article mentions one of the 400 accumulated actors but NOT the founding actor
    article_entities = _make_article_entities([("actor", "actor-7")])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert score < ASSIGN_THRESHOLD


def test_score_idf_actor_jaccard_mega_cluster():
    """IDF-weighted actor Jaccard: 1 match vs 400 founding actors → near-zero score."""
    from app.ingestion import unified_scorer, entity_idf

    entity_idf._IDF_MAP.clear()  # all IDF defaults to 1.0

    big_founding = [{"key": f"actor-{i}", "type": "actor"} for i in range(400)]
    cluster = _make_cluster("c-mega", founding_types=big_founding)
    article_entities = _make_article_entities([("actor", "actor-0")])

    score = unified_scorer._compute_score(article_entities, cluster["_source"], None)
    # actor_jaccard = idf("actor-0") / sum(idf(all 400)) = 1.0 / 400 = 0.0025
    # contribution = 0.22 * 0.0025 = 0.00055
    assert score < 0.01

    entity_idf._IDF_MAP.clear()


def test_score_entity_free_cluster_rejects_moderate_cosine():
    """Entity-free cluster (no founding_entity_types): cosine 0.82 contributes nothing."""
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    a, c = _emb_with_cosine(0.82)
    cluster = _make_cluster("c-editorial", centroid=c)  # founding_types=[] → entity-free
    score = _compute_score([], cluster["_source"], a)
    assert score < ASSIGN_THRESHOLD


def test_score_entity_free_cluster_accepts_near_identical():
    """Entity-free cluster: cosine 0.92 ≥ EMBED_HI=0.90 → binary 1.0 → contributes."""
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD, _W_EMBED

    a, c = _emb_with_cosine(0.92)
    cluster = _make_cluster("c-editorial", centroid=c)  # founding_types=[] → entity-free
    score = _compute_score([], cluster["_source"], a)
    # embed_signal_val = 1.0, score = _W_EMBED * 1.0 = 0.35 > 0.31
    assert score >= ASSIGN_THRESHOLD
    assert abs(score - _W_EMBED) < 0.01
```

- [ ] **Step 2: Run new tests to confirm they fail**

```bash
python -m pytest tests/test_unified_scorer.py -k "founding or mega or entity_free" -v
```

Expected: `FAILED` — all 4 new tests fail. The old tests also fail from Task 3. This is correct.

---

## Task 5: Rewrite `_compute_score`, update `_SOURCE_FIELDS`, raise `_EMBED_LO`

**Files:**
- Modify: `app/ingestion/unified_scorer.py`

- [ ] **Step 1: Raise `_EMBED_LO` default from `"0.70"` to `"0.75"` (line 28)**

Replace:
```python
_EMBED_LO = float(os.getenv("CLUSTER_EMBED_LO", "0.70"))
```

With:
```python
_EMBED_LO = float(os.getenv("CLUSTER_EMBED_LO", "0.75"))
```

- [ ] **Step 2: Update `_SOURCE_FIELDS` (line 35–39)**

Replace:
```python
_SOURCE_FIELDS = [
    "article_count", "state", "entity_keys",
    "event_signature", "centroid_embedding", "latest_at",
]
```

With:
```python
_SOURCE_FIELDS = [
    "article_count", "state", "entity_keys",
    "founding_entity_keys", "founding_entity_types",
    "centroid_embedding", "latest_at",
]
```

- [ ] **Step 3: Rewrite `_compute_score` (lines 48–110)**

Replace the entire `_compute_score` function:

```python
def _compute_score(
    article_entities: list[dict],
    cluster_source: dict,
    article_embedding: Optional[list[float]],
) -> float:
    # --- Article signal sets ---
    art_cves = {e["normalized_key"] for e in article_entities if e["type"] == "cve"}
    art_vuln_aliases = {
        e["normalized_key"] for e in article_entities if e["type"] == "vuln_alias"
    }
    art_actors_campaigns = {
        e["normalized_key"]
        for e in article_entities
        if e["type"] in ("actor", "campaign")
    }
    art_others = {
        e["normalized_key"]
        for e in article_entities
        if e["type"] not in ("cve", "vuln_alias", "actor", "campaign")
    }

    # --- Founding cluster signal sets (frozen at create time, never accumulated) ---
    founding_types = cluster_source.get("founding_entity_types") or []
    founding_cves = {ft["key"] for ft in founding_types if ft["type"] == "cve"}
    founding_vuln_aliases = {
        ft["key"] for ft in founding_types if ft["type"] == "vuln_alias"
    }
    founding_actors_campaigns = {
        ft["key"] for ft in founding_types if ft["type"] in ("actor", "campaign")
    }
    founding_others = {
        ft["key"]
        for ft in founding_types
        if ft["type"] not in ("cve", "vuln_alias", "actor", "campaign")
    }
    has_entity_anchor = bool(founding_types)

    # --- CVE and alias overlap (binary: a specific CVE match is always strong signal) ---
    cve_overlap = 1.0 if art_cves & founding_cves else 0.0
    alias_overlap = 1.0 if art_vuln_aliases & founding_vuln_aliases else 0.0

    # --- Actor/campaign overlap (IDF-weighted Jaccard — penalises mega-clusters) ---
    union_actors = art_actors_campaigns | founding_actors_campaigns
    shared_actors = art_actors_campaigns & founding_actors_campaigns
    if union_actors:
        num = sum(idf(k) for k in shared_actors)
        den = sum(idf(k) for k in union_actors)
        actor_campaign_overlap = num / den if den else 0.0
    else:
        actor_campaign_overlap = 0.0

    # --- Other entity overlap (IDF-weighted Jaccard — product/tool/malware/vendor) ---
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

    # Entity-free clusters (no founding signal) require near-identical embedding
    # to merge — prevents editorial/topic drift clusters from absorbing loosely
    # related articles.
    if has_entity_anchor:
        embed_signal_val = _embed_signal(cosine)
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

- [ ] **Step 4: Run all unified_scorer tests**

```bash
python -m pytest tests/test_unified_scorer.py -v
```

Expected: all tests PASS. If any fail, debug before proceeding.

Common failure: `test_score_high_embedding_alone_merges` uses `_make_cluster("c1", centroid=c)` with no `founding_types`. With `founding_types=[]`, the entity-free path fires. Test uses `cosine=0.95 ≥ _EMBED_HI=0.90` → `embed_signal_val=1.0` → `score=0.35 ≥ 0.31`. Should pass.

Common failure: `test_score_moderate_embedding_alone_does_not_merge` uses `cosine=0.80`. Entity-free → binary check: `0.80 < 0.90` → `embed_signal_val=0.0` → `score=0.0 < 0.31`. Should pass.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/unified_scorer.py tests/test_unified_scorer.py
git commit -m "feat(clusters): rewrite _compute_score with founding signals and IDF actor Jaccard"
```

---

## Task 6: Update `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add `CLUSTER_EMBED_LO` to the clustering section**

In `.env.example`, find the clustering section (lines 76–81):
```
# ─────────────────────────────────────────────────────────────────────────────
# Clustering — retrieval windows
# ─────────────────────────────────────────────────────────────────────────────

CLUSTER_EMBED_WINDOW_DAYS=30
CLUSTER_STRUCTURED_WINDOW_DAYS=30
```

Replace with:
```
# ─────────────────────────────────────────────────────────────────────────────
# Clustering — retrieval windows and scoring thresholds
# ─────────────────────────────────────────────────────────────────────────────

CLUSTER_EMBED_WINDOW_DAYS=30
CLUSTER_STRUCTURED_WINDOW_DAYS=30

# Embedding calibration: cosine below LO contributes nothing; at HI contributes 1.0.
# Entity-free clusters (no founding_entity_types) use a binary check at HI.
CLUSTER_EMBED_LO=0.75
CLUSTER_EMBED_HI=0.90
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: document CLUSTER_EMBED_LO and CLUSTER_EMBED_HI in .env.example"
```

---

## Task 7: Update `create_cluster` to set founding fields and `cluster_type`

**Files:**
- Modify: `app/ingestion/clusterer.py:332-391`
- Test: `tests/test_clusterer.py`

- [ ] **Step 1: Write failing tests in `tests/test_clusterer.py`**

Append to `tests/test_clusterer.py`:

```python
# ---------------------------------------------------------------------------
# create_cluster — founding_entity_types, founding_entity_keys, cluster_type
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_cluster_sets_founding_entity_types():
    os_mock = AsyncMock()
    os_mock.index.return_value = {"_id": "cluster-001"}
    os_mock.update.return_value = {}

    article = {
        "slug": "apt29-breach-001",
        "title": "APT29 Targets Finance Sector",
        "cve_ids": [],
        "published_at": "2026-05-01T10:00:00Z",
        "content_type": "news",
    }
    entities = [
        {"type": "actor", "normalized_key": "apt29"},
        {"type": "malware", "normalized_key": "cozycar"},
    ]

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        from app.ingestion.clusterer import create_cluster
        await create_cluster(article, entities)

    indexed = os_mock.index.call_args.kwargs["body"]
    assert indexed["founding_entity_keys"] == ["apt29", "cozycar"]
    assert indexed["founding_entity_types"] == [
        {"key": "apt29", "type": "actor"},
        {"key": "cozycar", "type": "malware"},
    ]
    assert indexed["cluster_type"] == "campaign"


@pytest.mark.asyncio
async def test_create_cluster_sets_cluster_type_cve_incident():
    os_mock = AsyncMock()
    os_mock.index.return_value = {"_id": "cluster-002"}
    os_mock.update.return_value = {}

    article = {
        "slug": "fortios-rce-001",
        "title": "FortiOS RCE CVE-2026-9999",
        "cve_ids": ["CVE-2026-9999"],
        "published_at": "2026-05-01T10:00:00Z",
        "content_type": "news",
    }
    entities = [{"type": "product", "normalized_key": "fortios"}]

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        from app.ingestion.clusterer import create_cluster
        await create_cluster(article, entities)

    indexed = os_mock.index.call_args.kwargs["body"]
    assert indexed["cluster_type"] == "cve_incident"


@pytest.mark.asyncio
async def test_create_cluster_founding_keys_match_entity_keys_at_creation():
    """At creation time, founding_entity_keys == entity_keys (they diverge later)."""
    os_mock = AsyncMock()
    os_mock.index.return_value = {"_id": "cluster-003"}
    os_mock.update.return_value = {}

    article = {
        "slug": "test-001",
        "title": "Test article",
        "cve_ids": ["CVE-2026-1234"],
        "published_at": "2026-05-01T10:00:00Z",
        "content_type": "news",
    }
    entities = [{"type": "cve", "normalized_key": "CVE-2026-1234"}]

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        from app.ingestion.clusterer import create_cluster
        await create_cluster(article, entities)

    indexed = os_mock.index.call_args.kwargs["body"]
    assert indexed["founding_entity_keys"] == indexed["entity_keys"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_clusterer.py -k "founding or cluster_type" -v
```

Expected: `FAILED` with `KeyError: 'founding_entity_keys'`

- [ ] **Step 3: Update `create_cluster` in `app/ingestion/clusterer.py`**

In `create_cluster` (line 332), update the `doc` dict. Find the existing lines (approx. 340–377):

```python
    slug = article.get("slug", "")
    cve_ids: list[str] = article.get("cve_ids") or []
    entity_keys = [e["normalized_key"] for e in entities]
    published_at = article.get("published_at") or now

    doc = {
```

Replace with:

```python
    slug = article.get("slug", "")
    cve_ids: list[str] = article.get("cve_ids") or []
    entity_keys = [e["normalized_key"] for e in entities]
    founding_entity_types = [{"key": e["normalized_key"], "type": e["type"]} for e in entities]
    published_at = article.get("published_at") or now

    doc = {
```

Then inside `doc`, after `"entity_keys": entity_keys,` add:

```python
        "founding_entity_keys": entity_keys,
        "founding_entity_types": founding_entity_types,
        "cluster_type": _classify_cluster_type(article, entities, cve_ids),
```

The full updated `doc` block (copy exactly):

```python
    doc = {
        "label": article.get("title", ""),
        "state": "new",
        "is_roundup": _is_roundup(article.get("title", ""), cve_ids),
        "is_advisory": article.get("content_type") == "ics_advisory",
        "summary": "",
        "why_it_matters": "",
        "score": 0.0,
        "confidence": "low",
        "max_cvss": article.get("cvss_score") or 0.0,
        "cisa_kev": False,
        "max_credibility_weight": float(article.get("credibility_weight") or 1.0),
        "top_factors": [],
        "article_ids": [slug],
        "categories": [article.get("category")] if article.get("category") else [],
        "tags": [],
        "article_count": 1,
        "cve_ids": cve_ids,
        "seed_cve_ids": cve_ids,
        "entity_keys": entity_keys,
        "founding_entity_keys": entity_keys,
        "founding_entity_types": founding_entity_types,
        "cluster_type": _classify_cluster_type(article, entities, cve_ids),
        "event_signature": _build_event_signature(entities, cve_ids),
        "merged_into": None,
        "timeline": [{
            "article_slug": slug,
            "source_name": article.get("source_name", ""),
            "title": article.get("title", ""),
            "published_at": published_at,
            "added_at": now,
        }],
        "latest_at": published_at,
        "created_at": now,
        "updated_at": now,
    }
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_clusterer.py -v
```

Expected: all tests PASS (including the 3 new ones).

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/clusterer.py tests/test_clusterer.py
git commit -m "feat(clusters): create_cluster sets founding_entity_types and cluster_type"
```

---

## Task 8: Ring-fence roundup articles in `cluster_article`

**Files:**
- Modify: `app/ingestion/clusterer.py:104-153`
- Test: `tests/test_clusterer.py`

- [ ] **Step 1: Write failing test in `tests/test_clusterer.py`**

Append to `tests/test_clusterer.py`:

```python
# ---------------------------------------------------------------------------
# cluster_article — roundup ring-fence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cluster_article_roundup_skips_find_best_and_creates_own():
    """Roundup articles bypass find_best_cluster and always create their own cluster."""
    article = {
        "slug": "patch-tuesday-2026-05",
        "title": "Microsoft May 2026 Patch Tuesday",
        "cve_ids": ["CVE-2026-1111", "CVE-2026-2222"],
        "content_type": "news",
        "source_name": "SANS ISC",
        "published_at": "2026-05-13T10:00:00Z",
    }

    with patch("app.ingestion.clusterer.embed_article", new_callable=AsyncMock, return_value=[0.1] * 1024), \
         patch("app.ingestion.clusterer.upsert_cve_topics", new_callable=AsyncMock), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock, return_value="cluster-existing") as mock_best, \
         patch("app.ingestion.clusterer.merge_into_cluster", new_callable=AsyncMock) as mock_merge, \
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock, return_value="new-roundup-cluster") as mock_create:

        from app.ingestion.clusterer import cluster_article
        await cluster_article(article, "patch-tuesday-2026-05", [])

    mock_best.assert_not_awaited()   # never asks for a best cluster
    mock_merge.assert_not_awaited()  # never merges into anything
    mock_create.assert_awaited_once()  # always creates its own


@pytest.mark.asyncio
async def test_cluster_article_stormcast_roundup_creates_own():
    """ISC Stormcast is caught by 'stormcast' keyword — creates own cluster."""
    article = {
        "slug": "stormcast-2026-05-19",
        "title": "ISC Stormcast For Tuesday, May 19th, 2026 https://isc.sans.edu/...",
        "cve_ids": [],
        "content_type": "news",
        "source_name": "SANS ISC",
        "published_at": "2026-05-19T08:00:00Z",
    }

    with patch("app.ingestion.clusterer.embed_article", new_callable=AsyncMock, return_value=[0.1] * 1024), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock, return_value="cluster-existing") as mock_best, \
         patch("app.ingestion.clusterer.merge_into_cluster", new_callable=AsyncMock) as mock_merge, \
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock, return_value="new-stormcast-cluster") as mock_create:

        from app.ingestion.clusterer import cluster_article
        await cluster_article(article, "stormcast-2026-05-19", [])

    mock_best.assert_not_awaited()
    mock_create.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_clusterer.py -k "roundup_skips or stormcast_roundup" -v
```

Expected: `FAILED` — roundup articles currently reach `find_best_cluster`.

- [ ] **Step 3: Update `cluster_article` in `app/ingestion/clusterer.py`**

Find `cluster_article` at line 104. Replace the entire function body (lines 104–153) with:

```python
async def cluster_article(
    article: dict,
    slug: str,
    entities: list[dict],
) -> None:
    """Assign article to an incident cluster and optionally to CVE topics.

    Routing by content_type:
    - kev_catalog: annotate matching clusters with cisa_kev=True, then exit.
    - product_advisory: CVE topics + merge if match found, but never seed new cluster.
    - ics_advisory: CVE topics + full cluster flow; create_cluster sets is_advisory=True.
    - news / threat_advisory (default): unchanged full flow.

    Roundup articles (patch tuesday, CVE landscape, stormcast, etc.) always create
    their own cluster and never merge into real incident clusters.
    """
    content_type = article.get("content_type", "news")
    cve_ids: list[str] = article.get("cve_ids") or []

    # KEV catalog: annotate existing clusters, then exit — skip embedding (not needed)
    if content_type == "kev_catalog":
        await _mark_kev_clusters(cve_ids)
        return

    embedding = await embed_article(article, [e["normalized_key"] for e in entities])
    ref_time = _parse_published_at(article.get("published_at"))

    # CVE topic flow (all non-kev types participate)
    if cve_ids:
        if len(cve_ids) > _MAX_ARTICLE_CVES_FOR_CVE_TOPIC:
            await create_cve_topic_stubs(cve_ids)
        else:
            await upsert_cve_topics(cve_ids, slug, entities, embedding)

    # Roundup articles (patch tuesday, weekly digest, stormcast, CVE landscape, etc.)
    # always create their own cluster. They must never merge into real incident clusters
    # because they carry dozens of CVEs/entities that would corrupt the retrieval net.
    if _is_roundup(article.get("title", ""), cve_ids):
        await create_cluster(article, entities, embedding=embedding)
        return

    # Incident cluster flow
    cluster_id = await find_best_cluster(entities, embedding, reference_time=ref_time)

    if cluster_id:
        await merge_into_cluster(
            cluster_id,
            slug,
            [e["normalized_key"] for e in entities],
            cve_ids,
            source_name=article.get("source_name", ""),
            title=article.get("title", ""),
            published_at=article.get("published_at", ""),
            cvss_score=article.get("cvss_score"),
            credibility_weight=float(article.get("credibility_weight") or 1.0),
            new_entities=entities,
            new_embedding=embedding,
        )
    elif content_type != "product_advisory":
        await create_cluster(article, entities, embedding=embedding)
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/test_clusterer.py tests/test_unified_scorer.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/clusterer.py tests/test_clusterer.py
git commit -m "feat(clusters): ring-fence roundup articles from incident clusters"
```

---

## Task 9: Full test suite + cluster rebuild

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: all tests pass. If failures appear in unrelated tests (normalizer, ingester), they are pre-existing and not caused by this change set — verify by checking `git stash && python -m pytest tests/ -v` on the previous commit.

- [ ] **Step 2: Rebuild clusters to see the effect**

```bash
docker compose build ingestion && docker compose up -d ingestion
docker compose exec ingestion python scripts/cluster_articles.py --reset
```

This takes ~13 minutes. After completion, verify:

```bash
docker compose exec ingestion python -c "
import asyncio
from dotenv import load_dotenv; load_dotenv()
from app.db.opensearch import INDEX_CLUSTERS, get_os_client

async def run():
    c = get_os_client()
    # Check max cluster size
    r = await c.search(index=INDEX_CLUSTERS, body={
        'size': 5, 'sort': [{'article_count': 'desc'}],
        '_source': ['label', 'article_count', 'cluster_type', 'founding_entity_keys']
    })
    for h in r['hits']['hits']:
        s = h['_source']
        print('[%d] %s | type=%s | founding=%s' % (
            s['article_count'], s.get('label','')[:60],
            s.get('cluster_type','?'), s.get('founding_entity_keys',[])))

asyncio.run(run())
" 2>/dev/null
```

Expected:
- No single cluster with `article_count > 30`
- Each cluster has `cluster_type` set (not null)
- Each cluster has `founding_entity_keys` set (not empty for event clusters)
- ISC Stormcast episodes appear as individual singletons with `cluster_type=roundup`

- [ ] **Step 3: Final commit (if any last-minute fixes needed from rebuild observation)**

```bash
git add -A
git commit -m "fix(clusters): post-rebuild tuning"
```

Only create this commit if the rebuild revealed actual bugs requiring code fixes. If everything looks good, skip this step.
