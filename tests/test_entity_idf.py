"""Tests for app.ingestion.entity_idf."""
import math
import pytest


def test_idf_returns_default_when_map_empty():
    from app.ingestion import entity_idf

    entity_idf._IDF_MAP.clear()
    assert entity_idf.idf("anything") == entity_idf._DEFAULT_IDF


def test_idf_returns_mapped_value():
    from app.ingestion import entity_idf

    entity_idf._IDF_MAP.clear()
    entity_idf._IDF_MAP["ivanti"] = 5.0
    assert entity_idf.idf("ivanti") == 5.0
    assert entity_idf.idf("unseen-key") == entity_idf._DEFAULT_IDF
    entity_idf._IDF_MAP.clear()


def test_compute_idf_common_entity_is_low_rare_is_high():
    from app.ingestion.entity_idf import _compute_idf

    # 1000 articles total
    common = _compute_idf(n_articles=1000, df=900)   # in 90% of articles
    rare = _compute_idf(n_articles=1000, df=5)        # in 0.5% of articles
    assert common < 0.5
    assert rare > 4.0


def test_compute_idf_clamps_to_floor():
    from app.ingestion.entity_idf import _compute_idf, _MIN_IDF

    # entity present in every article -> log(1) = 0 -> clamped to floor
    assert _compute_idf(n_articles=1000, df=1000) == _MIN_IDF
    # df larger than N (stale count) -> still clamped, never negative
    assert _compute_idf(n_articles=1000, df=5000) == _MIN_IDF
