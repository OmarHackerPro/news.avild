"""Tests for app.ingestion.unified_scorer."""
import pytest
from unittest.mock import AsyncMock, patch
import numpy as np


def _make_cluster(
    cluster_id: str,
    cve_ids: list[str] = None,
    vuln_aliases: list[str] = None,
    campaign_names: list[str] = None,
    primary_actors: list[str] = None,
    entity_keys: list[str] = None,
    centroid: list[float] = None,
    article_count: int = 1,
    state: str = "new",
) -> dict:
    return {
        "_id": cluster_id,
        "_source": {
            "article_count": article_count,
            "state": state,
            "entity_keys": entity_keys or [],
            "event_signature": {
                "cve_ids": cve_ids or [],
                "vuln_aliases": vuln_aliases or [],
                "campaign_names": campaign_names or [],
                "affected_products": [],
                "primary_actors": primary_actors or [],
                "confidence": "medium",
            },
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
        cve_ids=["CVE-2024-1234"],
        vuln_aliases=["log4shell"],
        primary_actors=["apt29"],
        entity_keys=["lockbit"],
        centroid=emb,
    )
    score = _compute_score(article_entities, cluster["_source"], emb)
    # 0.10 + 0.15 + 0.25 + 0.20*(1/1) + 0.30*1.0 = 1.0
    assert abs(score - 1.0) < 0.01


def test_score_embedding_only_cannot_exceed_threshold():
    """Pure embedding match (no structured signals) must score below 0.30 threshold."""
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    emb = [1.0] + [0.0] * 1023
    article_entities = []
    cluster = _make_cluster("c1", centroid=emb)
    score = _compute_score(article_entities, cluster["_source"], emb)
    assert score < ASSIGN_THRESHOLD


def test_score_cve_overlap_only():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("cve", "CVE-2024-9999")])
    cluster = _make_cluster("c1", cve_ids=["CVE-2024-9999"])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.10) < 0.01


def test_score_alias_overlap_only():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("vuln_alias", "heartbleed")])
    cluster = _make_cluster("c1", vuln_aliases=["heartbleed"])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.15) < 0.01


def test_score_actor_overlap_only():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("actor", "volt-typhoon")])
    cluster = _make_cluster("c1", primary_actors=["volt-typhoon"])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.25) < 0.01


def test_score_campaign_overlap_uses_actor_weight():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("campaign", "moveit-campaign")])
    cluster = _make_cluster("c1", campaign_names=["moveit-campaign"])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.25) < 0.01


def test_score_actor_plus_embed_exceeds_threshold():
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    emb = [1.0] + [0.0] * 1023
    article_entities = _make_article_entities([("actor", "lazarus-group")])
    cluster = _make_cluster("c1", primary_actors=["lazarus-group"], centroid=emb)
    score = _compute_score(article_entities, cluster["_source"], emb)
    # 0.25 + 0.30 = 0.55 > 0.30 threshold
    assert score >= ASSIGN_THRESHOLD


def test_score_cve_plus_alias_below_threshold():
    """CVE + alias alone no longer clears threshold — CVE articles belong in cve_topics."""
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    article_entities = _make_article_entities([
        ("cve", "CVE-2024-1234"),
        ("vuln_alias", "citrixbleed"),
    ])
    cluster = _make_cluster("c1", cve_ids=["CVE-2024-1234"], vuln_aliases=["citrixbleed"])
    score = _compute_score(article_entities, cluster["_source"], None)
    # 0.10 + 0.15 = 0.25 < 0.30
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

    low_cluster = _make_cluster("c-low", primary_actors=["apt29"])
    high_cluster = _make_cluster("c-high", primary_actors=["apt29"], vuln_aliases=["citrixbleed"])

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
    cluster = _make_cluster("c1", entity_keys=["ivanti"])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert score > 0.0  # vendor overlap is no longer ignored


def test_idf_weighted_overlap_favors_rare_entity():
    from app.ingestion import unified_scorer, entity_idf

    entity_idf._IDF_MAP.clear()
    entity_idf._IDF_MAP.update({"common-tool": 0.01, "rare-tool": 6.0, "noise": 0.01})

    # article shares the RARE entity with the cluster, plus an unshared noise entity
    article = _make_article_entities([("tool", "rare-tool"), ("tool", "noise")])
    cluster = _make_cluster("c1", entity_keys=["rare-tool", "common-tool"])
    rare_score = unified_scorer._compute_score(article, cluster["_source"], None)

    # article shares only the COMMON entity instead
    article2 = _make_article_entities([("tool", "common-tool"), ("tool", "noise")])
    cluster2 = _make_cluster("c2", entity_keys=["common-tool", "rare-tool"])
    common_score = unified_scorer._compute_score(article2, cluster2["_source"], None)

    assert rare_score > common_score
    entity_idf._IDF_MAP.clear()
