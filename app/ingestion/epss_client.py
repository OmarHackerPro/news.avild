import logging
import httpx

logger = logging.getLogger(__name__)

_EPSS_URL = "https://api.first.org/data/1.0/epss"
_TIMEOUT = 30.0
_BATCH_SIZE = 100


async def fetch_epss(cve_ids: list[str]) -> dict[str, dict]:
    """Fetch EPSS scores for a list of CVE IDs from FIRST.org.

    Returns a dict keyed by CVE ID:
        {"CVE-2024-1234": {"epss_score": 0.123, "epss_percentile": 0.876, "epss_updated_at": "2026-05-01"}}
    CVEs not found in EPSS are absent from the result. On network error, returns empty dict.
    """
    if not cve_ids:
        return {}
    results: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for i in range(0, len(cve_ids), _BATCH_SIZE):
            batch = cve_ids[i : i + _BATCH_SIZE]
            try:
                resp = await client.get(_EPSS_URL, params={"cve": ",".join(batch)})
                resp.raise_for_status()
                for entry in resp.json().get("data", []):
                    results[entry["cve"]] = {
                        "epss_score": float(entry["epss"]),
                        "epss_percentile": float(entry["percentile"]),
                        "epss_updated_at": entry["date"],
                    }
            except Exception as exc:
                logger.warning("EPSS fetch failed for batch starting at %d: %s", i, exc)
    return results
