"""Run-scoped in-process cache of clusters created/updated during a batch run.

`scripts/cluster_articles.py` creates clusters faster than OpenSearch's refresh
interval. Previously `create_cluster()` used `refresh="wait_for"` (blocking
~1s per cluster) so the next article's `find_best_cluster()` search could see
the just-created cluster. This cache holds every cluster created or merged into
during the run, letting `find_best_cluster()` match against them with zero
OpenSearch latency — so the costly `wait_for` can be dropped for batch runs.

Opt-in: disabled by default. Live ingestion (one article per call, seconds to
minutes apart) relies on OpenSearch's own refresh and keeps the `wait_for`
path, so a process-lived cache that could drift stale is never used there.
`cluster_articles.py` calls `enable()` / `disable()` around its run.

Concurrency: `cluster_articles.py` processes a page of articles concurrently.
asyncio is single-threaded, so dict operations are atomic, but a read-modify-
write spanning awaits (a merge) can lose an update if two articles merge into
the same cluster at once. This mirrors the pre-existing race against OpenSearch
and self-corrects at the next per-page OpenSearch refresh — it is not a new
class of bug.
"""
from typing import Optional

_enabled = False
_clusters: dict[str, dict] = {}


def enable() -> None:
    """Turn the cache on (batch runs only) and start from empty."""
    global _enabled
    _enabled = True
    _clusters.clear()


def disable() -> None:
    """Turn the cache off and drop all entries."""
    global _enabled
    _enabled = False
    _clusters.clear()


def is_enabled() -> bool:
    return _enabled


def put(cluster_id: str, source: dict) -> None:
    """Store/replace a cluster's current `_source` dict. No-op when disabled."""
    if _enabled and cluster_id:
        _clusters[cluster_id] = dict(source)


def get(cluster_id: str) -> Optional[dict]:
    """Return a copy of the cached `_source` for a cluster, or None."""
    if not _enabled:
        return None
    src = _clusters.get(cluster_id)
    return dict(src) if src is not None else None


def hits() -> list[dict]:
    """All cached clusters as OpenSearch-hit-shaped dicts: ``{_id, _source}``."""
    if not _enabled:
        return []
    return [{"_id": cid, "_source": dict(src)} for cid, src in _clusters.items()]
