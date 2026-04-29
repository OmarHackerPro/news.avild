import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_reparse_snapshot_uses_generic_registry_dispatch_in_dry_run():
    from scripts.reparse_snapshots import reparse_snapshot

    snap_hit = {
        "_id": "snap-1",
        "_source": {
            "source_name": "The Hacker News",
            "raw_content": """
                <rss><channel><item>
                <title>Patch for CVE-2026-1111</title>
                <link>https://example.com/a</link>
                <description>Fixes CVE-2026-1111</description>
                </item></channel></rss>
            """,
        },
    }
    source_by_name = {
        "The Hacker News": {
            "name": "The Hacker News",
            "url": "https://example.com/feed.xml",
            "default_type": "news",
            "default_category": "breaking",
            "default_severity": None,
            "normalizer": "thn",
        }
    }

    stats = await reparse_snapshot(
        snap_hit, source_by_name, dry_run=True, update=False,
    )
    assert stats["entries"] == 1
    assert stats["articles_with_cves"] == 1
    assert stats["errors"] == 0


@pytest.mark.asyncio
async def test_reparse_snapshot_uses_special_handler_in_dry_run():
    from scripts.reparse_snapshots import reparse_snapshot

    snap_hit = {
        "_id": "snap-2",
        "_source": {
            "source_name": "CISA News",
            "raw_content": """
                <rss><channel><item>
                <title>CISA update</title>
                <link>https://example.com/cisa</link>
                </item></channel></rss>
            """,
        },
    }
    source_by_name = {
        "CISA News": {
            "name": "CISA News",
            "url": "https://example.com/feed.xml",
            "default_type": "news",
            "default_category": "breaking",
            "default_severity": None,
            "normalizer": "cisa_news",
        }
    }

    stats = await reparse_snapshot(
        snap_hit, source_by_name, dry_run=True, update=False,
    )
    assert stats["entries"] == 1
    assert stats["errors"] == 0


@pytest.mark.asyncio
async def test_reparse_snapshot_counts_teasers_in_dry_run():
    from scripts.reparse_snapshots import reparse_snapshot

    snap_hit = {
        "_id": "snap-3",
        "_source": {
            "source_name": "BleepingComputer",
            "raw_content": """
                <rss><channel><item>
                <title>Teaser item</title>
                <link>https://example.com/bc</link>
                <description>Only a short teaser [...]</description>
                </item></channel></rss>
            """,
        },
    }
    source_by_name = {
        "BleepingComputer": {
            "name": "BleepingComputer",
            "url": "https://example.com/feed.xml",
            "default_type": "news",
            "default_category": "breaking",
            "default_severity": None,
            "normalizer": "bleepingcomputer",
        }
    }

    stats = await reparse_snapshot(
        snap_hit, source_by_name, dry_run=True, update=False,
    )
    assert stats["teasers"] == 1
    assert stats["thin_bodies"] == 1


@pytest.mark.asyncio
async def test_reparse_snapshot_writes_when_not_dry_run():
    from scripts.reparse_snapshots import reparse_snapshot

    snap_hit = {
        "_id": "snap-4",
        "_source": {
            "source_name": "The Hacker News",
            "raw_content": """
                <rss><channel><item>
                <title>Patch for CVE-2026-1111</title>
                <link>https://example.com/a</link>
                <description>Fixes CVE-2026-1111</description>
                </item></channel></rss>
            """,
        },
    }
    source_by_name = {
        "The Hacker News": {
            "name": "The Hacker News",
            "url": "https://example.com/feed.xml",
            "default_type": "news",
            "default_category": "breaking",
            "default_severity": None,
            "normalizer": "thn",
        }
    }

    with patch("scripts.reparse_snapshots.upsert_article", new=AsyncMock(return_value=True)):
        stats = await reparse_snapshot(
            snap_hit, source_by_name, dry_run=False, update=False,
        )

    assert stats["upserted"] == 1
