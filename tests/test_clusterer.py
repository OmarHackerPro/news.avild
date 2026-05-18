"""Tests for the rewritten app.ingestion.clusterer (unified scorer)."""
import pytest
from unittest.mock import AsyncMock, patch

from app.ingestion.clusterer import _build_event_signature, _updated_centroid, _is_roundup


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------

def test_build_event_signature_high_confidence_cve_and_alias():
    entities = [
        {"type": "cve", "normalized_key": "CVE-2021-44228"},
        {"type": "vuln_alias", "normalized_key": "log4shell"},
    ]
    sig = _build_event_signature(entities, ["CVE-2021-44228"])
    assert sig["confidence"] == "high"
    assert "log4shell" in sig["vuln_aliases"]
    assert "CVE-2021-44228" in sig["cve_ids"]


def test_build_event_signature_low_confidence_no_signals():
    sig = _build_event_signature([], [])
    assert sig["confidence"] == "low"


def test_updated_centroid_initializes_from_first_vec():
    vec = [1.0, 0.0, 0.0]
    result = _updated_centroid(None, vec, 1)
    assert result == vec


def test_updated_centroid_running_average():
    old = [1.0, 0.0]
    new_vec = [0.0, 1.0]
    result = _updated_centroid(old, new_vec, 2)
    assert abs(result[0] - 0.5) < 0.001
    assert abs(result[1] - 0.5) < 0.001


# ---------------------------------------------------------------------------
# cluster_article — delegates to find_best_cluster
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cluster_article_merges_when_cluster_found():
    article = {
        "slug": "fortios-rce-001",
        "title": "FortiOS RCE",
        "cve_ids": ["CVE-2026-1234"],
        "source_name": "BleepingComputer",
        "published_at": "2026-04-27T10:00:00Z",
        "credibility_weight": 1.2,
    }
    entities = [{"type": "cve", "normalized_key": "CVE-2026-1234"}]

    with patch("app.ingestion.clusterer.embed_article", new_callable=AsyncMock, return_value=[0.1] * 1024), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock, return_value="cluster-abc") as mock_best, \
         patch("app.ingestion.clusterer.merge_into_cluster", new_callable=AsyncMock) as mock_merge, \
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock) as mock_create:

        from app.ingestion.clusterer import cluster_article
        await cluster_article(article, "fortios-rce-001", entities)

    mock_best.assert_awaited_once()
    mock_merge.assert_awaited_once()
    assert mock_merge.call_args[0][0] == "cluster-abc"
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_cluster_article_creates_new_when_no_match():
    article = {
        "slug": "novel-article-001",
        "title": "New Threat",
        "cve_ids": [],
        "source_name": "Threatpost",
        "published_at": "2026-04-27T10:00:00Z",
    }

    with patch("app.ingestion.clusterer.embed_article", new_callable=AsyncMock, return_value=None), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock, return_value=None), \
         patch("app.ingestion.clusterer.merge_into_cluster", new_callable=AsyncMock) as mock_merge, \
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock) as mock_create:

        from app.ingestion.clusterer import cluster_article
        await cluster_article(article, "novel-article-001", [])

    mock_merge.assert_not_awaited()
    mock_create.assert_awaited_once()


# ---------------------------------------------------------------------------
# create_cluster — sets seed_cve_ids, event_signature, centroid_embedding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_cluster_sets_seed_cve_ids():
    os_mock = AsyncMock()
    os_mock.index.return_value = {"_id": "new-cluster-001"}
    os_mock.update.return_value = {}

    article = {
        "slug": "cve-article-001",
        "title": "Critical Bug",
        "cve_ids": ["CVE-2026-9999"],
        "published_at": "2026-04-27T10:00:00Z",
        "source_name": "CISA",
    }
    entities = [{"type": "cve", "normalized_key": "CVE-2026-9999"}]

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        from app.ingestion.clusterer import create_cluster
        await create_cluster(article, entities, embedding=[0.1] * 1024)

    indexed = os_mock.index.call_args.kwargs["body"]
    assert indexed["seed_cve_ids"] == ["CVE-2026-9999"]
    assert indexed["cve_ids"] == ["CVE-2026-9999"]
    assert indexed["centroid_embedding"] == [0.1] * 1024


@pytest.mark.asyncio
async def test_create_cluster_event_signature_confidence_high_when_cve_and_alias():
    os_mock = AsyncMock()
    os_mock.index.return_value = {"_id": "cluster-hi-conf"}
    os_mock.update.return_value = {}

    article = {
        "slug": "log4shell-001",
        "title": "Log4Shell exploited",
        "cve_ids": ["CVE-2021-44228"],
        "published_at": "2026-04-27T10:00:00Z",
        "source_name": "BleepingComputer",
    }
    entities = [
        {"type": "cve", "normalized_key": "CVE-2021-44228"},
        {"type": "vuln_alias", "normalized_key": "log4shell"},
    ]

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        from app.ingestion.clusterer import create_cluster
        await create_cluster(article, entities)

    indexed = os_mock.index.call_args.kwargs["body"]
    assert indexed["event_signature"]["confidence"] == "high"
    assert "log4shell" in indexed["event_signature"]["vuln_aliases"]


# ---------------------------------------------------------------------------
# merge_into_cluster — does NOT update seed_cve_ids
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_merge_does_not_touch_seed_cve_ids():
    os_mock = AsyncMock()
    os_mock.get.return_value = {
        "_source": {
            "article_count": 1,
            "centroid_embedding": [0.5] * 1024,
            "event_signature": {"cve_ids": ["CVE-2026-1111"], "vuln_aliases": [],
                                 "campaign_names": [], "affected_products": [],
                                 "primary_actors": [], "confidence": "medium"},
        }
    }
    os_mock.update.return_value = {}

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        from app.ingestion.clusterer import merge_into_cluster
        await merge_into_cluster(
            "cluster-existing", "article-new", ["fortios"], ["CVE-2026-1111"],
            source_name="CISA", title="Follow-up", published_at="2026-04-27T12:00:00Z",
        )

    for call in os_mock.update.call_args_list:
        script = call.kwargs.get("body", {}).get("script", {})
        if "source" in script:
            assert "seed_cve_ids" not in script["source"]


# ---------------------------------------------------------------------------
# Two-flow routing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cluster_article_calls_upsert_for_dedicated_cve_article():
    """Articles with ≤5 CVEs trigger upsert_cve_topics."""
    article = {
        "slug": "fortios-rce-001",
        "title": "FortiOS RCE",
        "cve_ids": ["CVE-2026-1234"],
        "source_name": "BleepingComputer",
        "published_at": "2026-04-27T10:00:00Z",
        "credibility_weight": 1.2,
    }
    entities = [{"type": "cve", "normalized_key": "CVE-2026-1234"}]

    with patch("app.ingestion.clusterer.embed_article", new_callable=AsyncMock, return_value=[0.1] * 1024), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock, return_value=None), \
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock), \
         patch("app.ingestion.clusterer.upsert_cve_topics", new_callable=AsyncMock) as mock_upsert, \
         patch("app.ingestion.clusterer.create_cve_topic_stubs", new_callable=AsyncMock) as mock_stubs:
        from app.ingestion.clusterer import cluster_article
        await cluster_article(article, article["slug"], entities)

    mock_upsert.assert_awaited_once()
    mock_stubs.assert_not_awaited()
    call_kwargs = mock_upsert.call_args
    assert call_kwargs.args[0] == ["CVE-2026-1234"]
    assert call_kwargs.args[1] == "fortios-rce-001"


@pytest.mark.asyncio
async def test_cluster_article_calls_stubs_for_roundup():
    """Articles with >5 CVEs trigger create_cve_topic_stubs, not upsert."""
    article = {
        "slug": "patch-tuesday-may-2026",
        "title": "Patch Tuesday May 2026",
        "cve_ids": [f"CVE-2026-{i:04d}" for i in range(80)],
        "source_name": "Microsoft",
        "published_at": "2026-05-01T10:00:00Z",
        "credibility_weight": 1.0,
    }
    entities = []

    with patch("app.ingestion.clusterer.embed_article", new_callable=AsyncMock, return_value=[0.1] * 1024), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock, return_value=None), \
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock), \
         patch("app.ingestion.clusterer.upsert_cve_topics", new_callable=AsyncMock) as mock_upsert, \
         patch("app.ingestion.clusterer.create_cve_topic_stubs", new_callable=AsyncMock) as mock_stubs:
        from app.ingestion.clusterer import cluster_article
        await cluster_article(article, article["slug"], entities)

    mock_stubs.assert_awaited_once()
    mock_upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_cluster_article_incident_flow_runs_even_for_cve_article():
    """Incident flow (find_best_cluster) always runs regardless of CVE routing."""
    article = {
        "slug": "fortios-rce-001",
        "title": "FortiOS RCE",
        "cve_ids": ["CVE-2026-1234"],
        "source_name": "BleepingComputer",
        "published_at": "2026-04-27T10:00:00Z",
        "credibility_weight": 1.2,
    }
    entities = [{"type": "cve", "normalized_key": "CVE-2026-1234"}]

    with patch("app.ingestion.clusterer.embed_article", new_callable=AsyncMock, return_value=[0.1] * 1024), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock, return_value="cluster-abc") as mock_best, \
         patch("app.ingestion.clusterer.merge_into_cluster", new_callable=AsyncMock) as mock_merge, \
         patch("app.ingestion.clusterer.upsert_cve_topics", new_callable=AsyncMock), \
         patch("app.ingestion.clusterer.create_cve_topic_stubs", new_callable=AsyncMock):
        from app.ingestion.clusterer import cluster_article
        await cluster_article(article, article["slug"], entities)

    mock_best.assert_awaited_once()
    mock_merge.assert_awaited_once()


@pytest.mark.asyncio
async def test_cluster_article_no_cve_skips_cve_flow():
    """Articles with no CVEs skip the CVE flow entirely."""
    article = {
        "slug": "threat-actor-post",
        "title": "Lazarus Group targets banks",
        "cve_ids": [],
        "source_name": "Krebs",
        "published_at": "2026-04-27T10:00:00Z",
        "credibility_weight": 1.2,
    }
    entities = [{"type": "actor", "normalized_key": "lazarus-group"}]

    with patch("app.ingestion.clusterer.embed_article", new_callable=AsyncMock, return_value=[0.1] * 1024), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock, return_value=None), \
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock), \
         patch("app.ingestion.clusterer.upsert_cve_topics", new_callable=AsyncMock) as mock_upsert, \
         patch("app.ingestion.clusterer.create_cve_topic_stubs", new_callable=AsyncMock) as mock_stubs:
        from app.ingestion.clusterer import cluster_article
        await cluster_article(article, article["slug"], entities)

    mock_upsert.assert_not_awaited()
    mock_stubs.assert_not_awaited()


# ---------------------------------------------------------------------------
# _is_roundup — pure heuristic
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,cve_ids,expected", [
    # keyword matches
    ("Patch Tuesday May 2026: 80 fixes", [], True),
    ("March 2026 CVE Landscape: 31 High-Impact Vulnerabilities", [], True),
    ("Weekly Digest: Top Security Stories", [], True),
    ("Monthly Roundup: April Threats", [], True),
    ("Weekly Digest Cybersecurity News", [], True),
    # CVE count threshold
    ("FortiOS RCE", [f"CVE-2026-{i:04d}" for i in range(11)], True),
    # normal articles — not a roundup
    ("FortiOS RCE CVE-2026-1234 actively exploited", ["CVE-2026-1234"], False),
    ("Lazarus Group targets financial institutions", [], False),
    ("Threat landscape shifts after CVSS overhaul", [], False),
    ("Google monthly security updates for May 2026", [], False),
    # exactly 10 CVEs — not a roundup (threshold is >10)
    ("Multiple CVEs fixed", [f"CVE-2026-{i:04d}" for i in range(10)], False),
])
def test_is_roundup(label, cve_ids, expected):
    assert _is_roundup(label, cve_ids) is expected


# ---------------------------------------------------------------------------
# create_cluster — sets is_roundup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_cluster_sets_is_roundup_true_for_roundup_label():
    os_mock = AsyncMock()
    os_mock.index.return_value = {"_id": "cluster-roundup-001"}
    os_mock.update.return_value = {}

    article = {
        "slug": "patch-tuesday-may-2026",
        "title": "Patch Tuesday May 2026: 80 fixes",
        "cve_ids": [f"CVE-2026-{i:04d}" for i in range(80)],
        "published_at": "2026-05-01T10:00:00Z",
        "source_name": "Microsoft",
    }

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        from app.ingestion.clusterer import create_cluster
        await create_cluster(article, [], embedding=[0.1] * 1024)

    indexed = os_mock.index.call_args.kwargs["body"]
    assert indexed["is_roundup"] is True


@pytest.mark.asyncio
async def test_create_cluster_sets_is_roundup_false_for_normal_article():
    os_mock = AsyncMock()
    os_mock.index.return_value = {"_id": "cluster-normal-001"}
    os_mock.update.return_value = {}

    article = {
        "slug": "fortios-rce-001",
        "title": "FortiOS RCE CVE-2026-1234 actively exploited",
        "cve_ids": ["CVE-2026-1234"],
        "published_at": "2026-04-27T10:00:00Z",
        "source_name": "BleepingComputer",
    }

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        from app.ingestion.clusterer import create_cluster
        await create_cluster(article, [], embedding=[0.1] * 1024)

    indexed = os_mock.index.call_args.kwargs["body"]
    assert indexed["is_roundup"] is False


# ---------------------------------------------------------------------------
# content_type routing in cluster_article()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kev_catalog_article_does_not_create_cluster():
    """kev_catalog articles annotate clusters but never create one."""
    from app.ingestion.clusterer import cluster_article

    article = {
        "slug": "cisa-adds-3-cve-2026-abc12345",
        "title": "CISA Adds 3 Known Exploited Vulnerabilities to Catalog",
        "content_type": "kev_catalog",
        "cve_ids": ["CVE-2026-1111", "CVE-2026-2222", "CVE-2026-3333"],
        "published_at": "2026-05-15T10:00:00Z",
        "source_name": "CISA News",
    }

    os_mock = AsyncMock()
    os_mock.update_by_query = AsyncMock(return_value={})

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer.embed_article", new_callable=AsyncMock, return_value=[0.1] * 1024), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock) as mock_find, \
         patch("app.ingestion.clusterer.upsert_cve_topics", new_callable=AsyncMock), \
         patch("app.ingestion.clusterer.create_cve_topic_stubs", new_callable=AsyncMock):
        mock_find.return_value = None  # no existing cluster match
        await cluster_article(article, "cisa-adds-3-cve-2026-abc12345", [])

    # create_cluster / index was NOT called
    os_mock.index.assert_not_called()
    # kev annotation WAS attempted
    os_mock.update_by_query.assert_awaited_once()
    call_body = os_mock.update_by_query.call_args.kwargs["body"]
    assert call_body["query"]["terms"]["cve_ids"] == ["CVE-2026-1111", "CVE-2026-2222", "CVE-2026-3333"]


@pytest.mark.asyncio
async def test_product_advisory_does_not_create_cluster_when_no_match():
    """product_advisory articles merge if a cluster matches, but never seed a new one."""
    from app.ingestion.clusterer import cluster_article

    article = {
        "slug": "cisco-ios-xe-rce-abc12345",
        "title": "Cisco IOS XE RCE Vulnerability",
        "content_type": "product_advisory",
        "cve_ids": ["CVE-2026-9999"],
        "published_at": "2026-05-15T10:00:00Z",
        "source_name": "Cisco Security Advisories",
    }

    os_mock = AsyncMock()

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer.embed_article", new_callable=AsyncMock, return_value=[0.1] * 1024), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock) as mock_find, \
         patch("app.ingestion.clusterer.upsert_cve_topics", new_callable=AsyncMock), \
         patch("app.ingestion.clusterer.create_cve_topic_stubs", new_callable=AsyncMock):
        mock_find.return_value = None  # no match
        await cluster_article(article, "cisco-ios-xe-rce-abc12345", [])

    # cluster index (create_cluster) was NOT called
    os_mock.index.assert_not_called()


@pytest.mark.asyncio
async def test_ics_advisory_creates_cluster_with_is_advisory_true():
    """ics_advisory articles create a cluster with is_advisory=True."""
    from app.ingestion.clusterer import create_cluster

    article = {
        "slug": "icsa-26-099-01-siemens-abc12345",
        "title": "Siemens SCALANCE Vulnerabilities (ICSA-26-099-01)",
        "content_type": "ics_advisory",
        "cve_ids": ["CVE-2026-5555"],
        "published_at": "2026-05-15T10:00:00Z",
        "source_name": "CISA Advisories",
    }

    os_mock = AsyncMock()
    os_mock.index.return_value = {"_id": "cluster-ics-001"}
    os_mock.update.return_value = {}

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        await create_cluster(article, [], embedding=[0.1] * 1024)

    indexed = os_mock.index.call_args.kwargs["body"]
    assert indexed["is_advisory"] is True


@pytest.mark.asyncio
async def test_news_article_creates_cluster_with_is_advisory_false():
    """Regular news articles create a cluster with is_advisory=False."""
    from app.ingestion.clusterer import create_cluster

    article = {
        "slug": "fortios-rce-abc12345",
        "title": "FortiOS RCE CVE-2026-1234 exploited in the wild",
        "content_type": "news",
        "cve_ids": ["CVE-2026-1234"],
        "published_at": "2026-05-15T09:00:00Z",
        "source_name": "BleepingComputer",
    }

    os_mock = AsyncMock()
    os_mock.index.return_value = {"_id": "cluster-news-001"}
    os_mock.update.return_value = {}

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        await create_cluster(article, [], embedding=[0.1] * 1024)

    indexed = os_mock.index.call_args.kwargs["body"]
    assert indexed["is_advisory"] is False


@pytest.mark.asyncio
async def test_product_advisory_merges_when_cluster_found():
    """product_advisory articles merge into an existing cluster if one matches."""
    from app.ingestion.clusterer import cluster_article

    article = {
        "slug": "msrc-cve-2026-9999-abc12345",
        "title": "CVE-2026-9999 | Windows Kernel Elevation of Privilege",
        "content_type": "product_advisory",
        "cve_ids": ["CVE-2026-9999"],
        "published_at": "2026-05-15T10:00:00Z",
        "source_name": "Microsoft MSRC",
        "credibility_weight": 1.5,
    }

    os_mock = AsyncMock()
    os_mock.get.return_value = {"_source": {"article_ids": [], "article_count": 1, "event_signature": {}, "latest_at": "", "centroid_embedding": None}}
    os_mock.update.return_value = {}

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer.embed_article", new_callable=AsyncMock, return_value=[0.1] * 1024), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock) as mock_find, \
         patch("app.ingestion.clusterer.upsert_cve_topics", new_callable=AsyncMock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        mock_find.return_value = "existing-cluster-001"  # a match was found
        await cluster_article(article, "msrc-cve-2026-9999-abc12345", [])

    # index (create_cluster) was NOT called — merged into existing
    os_mock.index.assert_not_called()
    # update WAS called — merge_into_cluster ran
    os_mock.update.assert_called()


@pytest.mark.asyncio
async def test_kev_catalog_with_no_cves_skips_annotation():
    """kev_catalog with empty cve_ids skips update_by_query entirely."""
    from app.ingestion.clusterer import cluster_article

    article = {
        "slug": "cisa-kev-empty-abc12345",
        "title": "CISA Adds One Known Exploited Vulnerability to Catalog",
        "content_type": "kev_catalog",
        "cve_ids": [],
        "published_at": "2026-05-15T10:00:00Z",
        "source_name": "CISA News",
    }

    os_mock = AsyncMock()
    os_mock.update_by_query = AsyncMock(return_value={})

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer.embed_article", new_callable=AsyncMock, return_value=[0.1] * 1024):
        await cluster_article(article, "cisa-kev-empty-abc12345", [])

    os_mock.update_by_query.assert_not_called()
    os_mock.index.assert_not_called()
