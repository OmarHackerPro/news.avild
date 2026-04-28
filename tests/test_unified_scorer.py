"""Tests for app.ingestion.unified_scorer."""
import pytest
from unittest.mock import AsyncMock, patch
import numpy as np


def _make_cluster(
    cluster_id: str,
    cve_ids: list[str] = None,
    vuln_aliases: list[str] = None,
    campaign_names: list[str] = None,
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
                "primary_actors": [],
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
        ("malware", "lockbit"),
    ])
    cluster = _make_cluster(
        "c1",
        cve_ids=["CVE-2024-1234"],
        vuln_aliases=["log4shell"],
        entity_keys=["lockbit"],
        centroid=emb,
    )
    score = _compute_score(article_entities, cluster["_source"], emb)
    # 0.45 + 0.25 + 0.15 * (1/1) + 0.15 * 1.0 = 1.0
    assert abs(score - 1.0) < 0.01


def test_score_embedding_only_cannot_exceed_threshold():
    """Pure embedding match (no structured signals) must score below 0.30 threshold."""
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    emb = [1.0] + [0.0] * 1023
    article_entities = []  # no entities
    cluster = _make_cluster("c1", centroid=emb)
    score = _compute_score(article_entities, cluster["_source"], emb)
    assert score < ASSIGN_THRESHOLD


def test_score_cve_overlap_only():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("cve", "CVE-2024-9999")])
    cluster = _make_cluster("c1", cve_ids=["CVE-2024-9999"])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.45) < 0.01


def test_score_alias_overlap_only():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("vuln_alias", "heartbleed")])
    cluster = _make_cluster("c1", vuln_aliases=["heartbleed"])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.25) < 0.01


def test_score_cve_plus_alias_exceeds_threshold():
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    article_entities = _make_article_entities([
        ("cve", "CVE-2024-1234"),
        ("vuln_alias", "citrixbleed"),
    ])
    cluster = _make_cluster("c1", cve_ids=["CVE-2024-1234"], vuln_aliases=["citrixbleed"])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert score >= ASSIGN_THRESHOLD


# ---------------------------------------------------------------------------
# find_best_cluster
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_best_cluster_returns_none_below_threshold():
    from app.ingestion.unified_scorer import find_best_cluster

    article_entities = []
    article_embedding = None

    with patch("app.ingestion.unified_scorer._get_candidates", new_callable=AsyncMock) as mock_cands:
        mock_cands.return_value = []
        result = await find_best_cluster(article_entities, article_embedding)

    assert result is None


@pytest.mark.asyncio
async def test_find_best_cluster_returns_highest_scoring():
    from app.ingestion.unified_scorer import find_best_cluster

    low_cluster = _make_cluster("c-low", vuln_aliases=["log4shell"])
    high_cluster = _make_cluster("c-high", cve_ids=["CVE-2024-1234"], vuln_aliases=["log4shell"])

    article_entities = _make_article_entities([
        ("cve", "CVE-2024-1234"),
        ("vuln_alias", "log4shell"),
    ])

    with patch("app.ingestion.unified_scorer._get_candidates", new_callable=AsyncMock) as mock_cands:
        mock_cands.return_value = [low_cluster, high_cluster]
        result = await find_best_cluster(article_entities, None)

    assert result == "c-high"


@pytest.mark.asyncio
async def test_find_best_cluster_returns_none_when_candidates_below_threshold():
    """Returns None even when candidates exist, if best score is below ASSIGN_THRESHOLD."""
    from app.ingestion.unified_scorer import find_best_cluster

    # Cluster with no shared CVEs/aliases/entities and no embedding — score = 0.0
    no_match_cluster = _make_cluster("c-nomatch")
    article_entities = _make_article_entities([("cve", "CVE-2024-9999")])

    with patch("app.ingestion.unified_scorer._get_candidates", new_callable=AsyncMock) as mock_cands:
        mock_cands.return_value = [no_match_cluster]
        result = await find_best_cluster(article_entities, None)

    assert result is None
