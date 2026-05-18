"""Entity-aware chunked embedding input.

Builds the text fed to the embedder: title + body + an Entities: line, split
into ~512-token chunks for long articles. embed_article() averages the chunk
vectors into one article vector.
"""
import numpy as np

from app.ingestion.embedding_client import embed_batch
from app.ingestion.normalizer import strip_html

_CHUNK_CHARS = 1800  # ~512 tokens for bge-large-en-v1.5


def _body_text(article: dict) -> str:
    html = article.get("content_html") or ""
    if html:
        return strip_html(html)
    return article.get("summary") or article.get("desc") or ""


def build_chunk_inputs(article: dict, entity_keys: list[str]) -> list[str]:
    """Return one embedding-input string per chunk (length 1 for short articles)."""
    title = article.get("title") or ""
    body = _body_text(article)
    entity_line = ("\nEntities: " + ", ".join(entity_keys)) if entity_keys else ""

    if len(body) <= _CHUNK_CHARS:
        return [f"{title}. {body}{entity_line}"]

    slices = [body[i:i + _CHUNK_CHARS] for i in range(0, len(body), _CHUNK_CHARS)]
    return [f"{title}. {s}{entity_line}" for s in slices]


async def embed_article(article: dict, entity_keys: list[str]) -> list[float] | None:
    """Embed an article (chunked, entity-aware) and average chunk vectors.

    Returns None if every chunk embedding failed.
    """
    inputs = build_chunk_inputs(article, entity_keys)
    vectors = await embed_batch(inputs)
    good = [v for v in vectors if v is not None]
    if not good:
        return None
    return np.mean(np.array(good, dtype=np.float32), axis=0).tolist()
