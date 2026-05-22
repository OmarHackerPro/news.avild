import logging
import os

import httpx

logger = logging.getLogger(__name__)

_EMBEDDER_URL = os.getenv("EMBEDDER_URL", "http://kiber-embedder:8001")
# The embedder (services/embedder) runs a single-threaded CPU encoder
# (ThreadPoolExecutor max_workers=1). Concurrent callers queue server-side, so a
# low client timeout abandons requests the embedder is still completing — it
# returns 200 after the client already gave up. Timeouts must cover real CPU
# encode latency plus server-side queueing.
_TIMEOUT = 60.0
_BATCH_TIMEOUT = 300.0


async def embed_text(text: str) -> list[float] | None:
    """Embed a single article text. Returns None on any error."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.post(f"{_EMBEDDER_URL}/embed", json={"text": text})
            resp.raise_for_status()
            return resp.json()["embedding"]
        except Exception as exc:
            logger.warning("embed_text failed: %s", exc)
            return None


async def embed_batch(texts: list[str]) -> list[list[float] | None]:
    """Embed a list of texts. Returns list of same length; None entries on failure."""
    if not texts:
        return []
    async with httpx.AsyncClient(timeout=_BATCH_TIMEOUT) as client:
        try:
            resp = await client.post(f"{_EMBEDDER_URL}/embed/batch", json={"texts": texts})
            resp.raise_for_status()
            result = resp.json()["embeddings"]
            if len(result) != len(texts):
                logger.warning("embed_batch length mismatch: sent %d, got %d", len(texts), len(result))
                return [None] * len(texts)
            return result
        except Exception as exc:
            logger.warning("embed_batch failed: %s", exc)
            return [None] * len(texts)
