"""Cluster scoring and explainability.

Computes a 0-100 importance score for a cluster from five factors:
  1. CVSS severity   — max CVSS of member articles          (0-30 pts)
  2. Coverage        — number of unique articles            (0-25 pts)
  3. Recency         — time since the cluster last updated  (0-20 pts)
  4. CVE / Entities  — number of known CVEs or entities     (0-15 pts)
  5. State bonus     — cluster maturity                     (0-10 pts)

Also populates `confidence` ("low" / "medium" / "high") and a
`top_factors` list of up to 5 contributing factors with their point
contributions, sorted by descending impact.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from app.db.opensearch import INDEX_CLUSTERS, get_os_client

logger = logging.getLogger(__name__)


def compute_cluster_score(
    *,
    article_count: int,
    max_cvss: Optional[float],
    cve_count: int,
    entity_keys: list[str],
    state: str,
    latest_at: str,
) -> dict:
    """Return {score, confidence, top_factors} — pure, no I/O."""
    factors: list[dict] = []
    total = 0.0

    # ------------------------------------------------------------------
    # 1. CVSS severity component (0-30 pts)
    # ------------------------------------------------------------------
    if max_cvss is not None:
        cvss_pts = round(min(max_cvss, 10.0) / 10.0 * 30.0, 1)
        factors.append({
            "factor": "cvss_score",
            "label": f"CVSS {max_cvss:.1f}",
            "points": cvss_pts,
        })
        total += cvss_pts

    # ------------------------------------------------------------------
    # 2. Coverage component (0-25 pts)
    # ------------------------------------------------------------------
    coverage_pts = round(min(article_count, 10) / 10.0 * 25.0, 1)
    factors.append({
        "factor": "coverage",
        "label": f"{article_count} article{'s' if article_count != 1 else ''}",
        "points": coverage_pts,
    })
    total += coverage_pts

    # ------------------------------------------------------------------
    # 3. Recency component (0-20 pts)
    # ------------------------------------------------------------------
    recency_pts = 0.0
    if latest_at:
        try:
            dt = datetime.fromisoformat(latest_at.replace("Z", "+00:00"))
            hours_ago = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
            if hours_ago < 6:
                recency_pts = 20.0
            elif hours_ago < 12:
                recency_pts = 16.0
            elif hours_ago < 24:
                recency_pts = 12.0
            elif hours_ago < 48:
                recency_pts = 8.0
            elif hours_ago < 168:  # 7 days
                recency_pts = 4.0
            label = f"Updated {int(hours_ago)}h ago" if hours_ago >= 1 else "Just updated"
            factors.append({"factor": "recency", "label": label, "points": recency_pts})
            total += recency_pts
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 4. CVE / Entity component (0-15 pts)
    # ------------------------------------------------------------------
    if cve_count > 0:
        cve_pts = round(min(cve_count, 5) / 5.0 * 15.0, 1)
        factors.append({
            "factor": "cve_count",
            "label": f"{cve_count} CVE{'s' if cve_count != 1 else ''}",
            "points": cve_pts,
        })
        total += cve_pts
    elif entity_keys:
        entity_pts = round(min(len(entity_keys), 5) / 5.0 * 5.0, 1)
        factors.append({
            "factor": "entities",
            "label": f"{len(entity_keys)} known entit{'ies' if len(entity_keys) != 1 else 'y'}",
            "points": entity_pts,
        })
        total += entity_pts

    # ------------------------------------------------------------------
    # 5. State bonus (0-10 pts)
    # ------------------------------------------------------------------
    state_pts_map = {"confirmed": 10.0, "developing": 6.0, "new": 2.0, "resolved": 0.0}
    state_pts = state_pts_map.get(state, 2.0)
    factors.append({
        "factor": "state",
        "label": state.capitalize(),
        "points": state_pts,
    })
    total += state_pts

    # ------------------------------------------------------------------
    # Finalise
    # ------------------------------------------------------------------
    factors.sort(key=lambda f: f["points"], reverse=True)
    top_factors = factors[:5]

    score = round(min(total, 100.0), 1)
    if score >= 75:
        confidence = "high"
    elif score >= 45:
        confidence = "medium"
    else:
        confidence = "low"

    return {"score": score, "confidence": confidence, "top_factors": top_factors}


async def rescore_cluster(cluster_id: str) -> None:
    """Fetch a cluster from OpenSearch, recompute its score, and write it back."""
    client = get_os_client()
    try:
        resp = await client.get(index=INDEX_CLUSTERS, id=cluster_id)
    except Exception as exc:
        logger.warning("rescore_cluster: could not fetch %s — %s", cluster_id, exc)
        return

    src = resp["_source"]
    score_data = compute_cluster_score(
        article_count=src.get("article_count", 1),
        max_cvss=src.get("max_cvss"),
        cve_count=len(src.get("cve_ids") or []),
        entity_keys=src.get("entity_keys") or [],
        state=src.get("state", "new"),
        latest_at=src.get("latest_at") or src.get("created_at", ""),
    )

    await client.update(
        index=INDEX_CLUSTERS,
        id=cluster_id,
        body={"doc": {
            "score": score_data["score"],
            "confidence": score_data["confidence"],
            "top_factors": score_data["top_factors"],
        }},
    )
    logger.debug(
        "Scored cluster %s → %.1f (%s)",
        cluster_id, score_data["score"], score_data["confidence"],
    )
