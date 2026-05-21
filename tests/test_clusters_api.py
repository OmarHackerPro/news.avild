import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_list_clusters_excludes_advisory_clusters():
    """list_clusters() query must contain must_not: is_advisory: True."""
    from app.api.routes.clusters import list_clusters

    os_mock = AsyncMock()
    os_mock.search.return_value = {
        "hits": {"hits": [], "total": {"value": 0}}
    }

    with patch("app.api.routes.clusters.get_os_client", return_value=os_mock):
        await list_clusters()

    call_body = os_mock.search.call_args.kwargs["body"]
    must_not = call_body["query"]["bool"]["must_not"]
    assert {"term": {"is_advisory": True}} in must_not


@pytest.mark.asyncio
async def test_get_cluster_exposes_max_epss():
    from app.api.routes.clusters import get_cluster

    os_mock = AsyncMock()
    os_mock.get.return_value = {
        "_id": "cluster-epss-api",
        "_source": {
            "label": "Test cluster",
            "state": "new",
            "article_ids": [],
            "article_count": 0,
            "score": 12.0,
            "max_epss": 0.47,
            "timeline": [],
        },
    }

    with patch("app.api.routes.clusters.get_os_client", return_value=os_mock):
        result = await get_cluster("cluster-epss-api")

    assert result.max_epss == 0.47
