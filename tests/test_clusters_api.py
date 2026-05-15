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
