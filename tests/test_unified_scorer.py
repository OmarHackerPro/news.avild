"""Tests for app.ingestion.unified_scorer."""
import pytest
from unittest.mock import AsyncMock, patch
import numpy as np


def _make_cluster(
    cluster_id: str,
    founding_types: list[dict] = None,
    entity_keys: list[str] = None,
    centroid: list[float] = None,
    article_count: int = 1,
    state: str = "new",
) -> dict:
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


def _make_article_entities(types_keys: list[tuple]) -> list[dict]:
    return [{"type": t, "normalized_key": k} for t, k in types_keys]


# ---------------------------------------------------------------------------
# Score formula
# ---------------------------------------------------------------------------

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


def _emb_with_cosine(target: float) -> tuple[list[float], list[float]]:
    """Two unit vectors whose cosine similarity equals `target`."""
    import math
    a = [1.0] + [0.0] * 1023
    c = [target, math.sqrt(max(0.0, 1.0 - target * target))] + [0.0] * 1022
    return a, c


def test_score_high_embedding_alone_merges():
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    a, c = _emb_with_cosine(0.95)
    cluster = _make_cluster("c1", centroid=c)
    score = _compute_score([], cluster["_source"], a)
    assert score >= ASSIGN_THRESHOLD


def test_score_moderate_embedding_alone_does_not_merge():
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    a, c = _emb_with_cosine(0.80)
    cluster = _make_cluster("c1", centroid=c)
    score = _compute_score([], cluster["_source"], a)
    assert score < ASSIGN_THRESHOLD


def test_calibration_curve_zero_below_floor():
    from app.ingestion.unified_scorer import _embed_signal

    assert _embed_signal(0.50) == 0.0   # well below floor
    assert _embed_signal(0.70) == 0.0   # still below new floor (0.75)
    assert _embed_signal(0.75) == 0.0   # at floor — formula gives (0.75-0.75)/(0.90-0.75)=0
    assert _embed_signal(0.90) == 1.0   # at ceiling
    assert _embed_signal(0.95) == 1.0   # above ceiling
    assert abs(_embed_signal(0.825) - 0.5) < 0.001  # midpoint between 0.75 and 0.90


def test_score_cve_overlap_only():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("cve", "CVE-2024-9999")])
    cluster = _make_cluster("c1", founding_types=[{"key": "CVE-2024-9999", "type": "cve"}])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.10) < 0.01


def test_score_alias_overlap_only():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("vuln_alias", "heartbleed")])
    cluster = _make_cluster("c1", founding_types=[{"key": "heartbleed", "type": "vuln_alias"}])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.15) < 0.01


def test_score_actor_overlap_only():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("actor", "volt-typhoon")])
    cluster = _make_cluster("c1", founding_types=[{"key": "volt-typhoon", "type": "actor"}])
    score = _compute_score(article_entities, cluster["_source"], None)
    # Single actor: IDF Jaccard = idf("volt-typhoon") / idf("volt-typhoon") = 1.0
    assert abs(score - 0.22) < 0.01


def test_score_campaign_overlap_uses_actor_weight():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("campaign", "moveit-campaign")])
    cluster = _make_cluster("c1", founding_types=[{"key": "moveit-campaign", "type": "campaign"}])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.22) < 0.01


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
    # 0.22 + 0.35 = 0.57 > 0.31 threshold
    assert score >= ASSIGN_THRESHOLD


def test_score_cve_plus_alias_below_threshold():
    """CVE + alias alone no longer clears threshold — CVE articles belong in cve_topics."""
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


# ---------------------------------------------------------------------------
# find_best_cluster
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_best_cluster_returns_none_below_threshold():
    from app.ingestion.unified_scorer import find_best_cluster

    with patch("app.ingestion.unified_scorer._get_candidates", new_callable=AsyncMock) as mock_cands:
        mock_cands.return_value = []
        result = await find_best_cluster([], None)

    assert result is None


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


@pytest.mark.asyncio
async def test_find_best_cluster_returns_none_when_candidates_below_threshold():
    from app.ingestion.unified_scorer import find_best_cluster

    no_match_cluster = _make_cluster("c-nomatch")
    article_entities = _make_article_entities([("cve", "CVE-2024-9999")])

    with patch("app.ingestion.unified_scorer._get_candidates", new_callable=AsyncMock) as mock_cands:
        mock_cands.return_value = [no_match_cluster]
        result = await find_best_cluster(article_entities, None)

    assert result is None


def test_window_constants_default_to_30_days():
    import importlib
    from app.ingestion import unified_scorer
    importlib.reload(unified_scorer)
    assert unified_scorer._EMBED_WINDOW_DAYS == 30
    assert unified_scorer._STRUCTURED_WINDOW_DAYS == 30


def test_retrieval_key_uppercases_cve_only():
    from app.ingestion.unified_scorer import _retrieval_key

    assert _retrieval_key({"type": "cve", "normalized_key": "cve-2025-1234"}) == "CVE-2025-1234"
    assert _retrieval_key({"type": "actor", "normalized_key": "apt28"}) == "apt28"
    assert _retrieval_key({"type": "malware", "normalized_key": "lockbit"}) == "lockbit"


def test_vendor_now_counts_in_entity_score():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("vendor", "ivanti")])
    cluster = _make_cluster("c1", founding_types=[{"key": "ivanti", "type": "vendor"}])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert score > 0.0  # vendor overlap is no longer ignored


def test_idf_weighted_overlap_favors_rare_entity():
    from app.ingestion import unified_scorer, entity_idf

    entity_idf._IDF_MAP.clear()
    entity_idf._IDF_MAP.update({"common-tool": 0.01, "rare-tool": 6.0, "noise": 0.01})

    # article shares the RARE entity with the cluster, plus an unshared noise entity
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
