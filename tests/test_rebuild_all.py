"""Tests for scripts/rebuild_all.py step orchestration."""
from argparse import Namespace
from unittest.mock import patch

import pytest


def _args(**overrides) -> Namespace:
    defaults = {
        "skip_ner": False,
        "skip_embed": False,
        "skip_epss": False,
        "skip_cluster": False,
        "force": False,
        "dry_run": True,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


@pytest.mark.asyncio
async def test_epss_step_runs_between_embeddings_and_clustering():
    import scripts.rebuild_all as rebuild_all

    calls: list[str] = []

    async def fake_ner(**kw):
        calls.append("ner")

    async def fake_embed(**kw):
        calls.append("embed")

    async def fake_epss(**kw):
        calls.append("epss")

    async def fake_cluster(**kw):
        calls.append("cluster")

    with patch.object(rebuild_all, "run_ner", fake_ner), \
         patch.object(rebuild_all, "run_embeddings", fake_embed), \
         patch.object(rebuild_all, "run_epss_sync", fake_epss), \
         patch.object(rebuild_all, "run_clustering", fake_cluster):
        await rebuild_all.main(_args())

    assert calls == ["ner", "embed", "epss", "cluster"]


@pytest.mark.asyncio
async def test_skip_epss_excludes_the_step():
    import scripts.rebuild_all as rebuild_all

    calls: list[str] = []

    async def fake_ner(**kw):
        calls.append("ner")

    async def fake_embed(**kw):
        calls.append("embed")

    async def fake_epss(**kw):
        calls.append("epss")

    async def fake_cluster(**kw):
        calls.append("cluster")

    with patch.object(rebuild_all, "run_ner", fake_ner), \
         patch.object(rebuild_all, "run_embeddings", fake_embed), \
         patch.object(rebuild_all, "run_epss_sync", fake_epss), \
         patch.object(rebuild_all, "run_clustering", fake_cluster):
        await rebuild_all.main(_args(skip_epss=True))

    assert "epss" not in calls
    assert calls == ["ner", "embed", "cluster"]
