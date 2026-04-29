# Clustering Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken 3-tier MLT-based clustering system with LLM NER + GPU embeddings + unified composite scoring, fixing the 94% entity-miss rate that causes MLT to handle nearly all clustering and produce false merges.

**Architecture:** Articles pass through a Claude Haiku NER step (cached in Postgres) and a local GPU embedding step (BAAI/bge-large-en-v1.5 via Docker sidecar) at ingest time. The 3-tier CVE/entity/MLT waterfall in `clusterer.py` is replaced by a unified scorer that retrieves cluster candidates via OpenSearch k-NN + structured terms queries and ranks them with a weighted formula. A background merge-detection job dissolves duplicate clusters.

**Tech Stack:** Python 3.12, FastAPI async, anthropic SDK (Claude Haiku), sentence-transformers (bge-large-en-v1.5), CUDA/RTX 3050, OpenSearch k-NN (nmslib/hnsw), SQLAlchemy async, pytest-asyncio, httpx.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `services/embedder/main.py` | FastAPI embedding service (GPU) |
| Create | `services/embedder/Dockerfile` | CUDA image for embedder |
| Create | `services/embedder/requirements.txt` | Embedder deps |
| Create | `app/ingestion/ner_llm.py` | Claude Haiku NER + Postgres cache |
| Create | `app/ingestion/embedding_client.py` | Async HTTP client for embedder |
| Create | `app/ingestion/unified_scorer.py` | Score formula + candidate retrieval |
| Create | `scripts/backfill_ner.py` | Backfill NER for existing articles |
| Create | `scripts/backfill_embeddings.py` | Backfill cluster centroids |
| Create | `scripts/detect_cluster_merges.py` | Merge detection background job |
| Create | `alembic/versions/b1c2d3e4f5a6_add_ner_cache.py` | ner_cache Postgres table |
| Create | `tests/test_ner_llm.py` | NER unit tests |
| Create | `tests/test_embedding_client.py` | Embedding client unit tests |
| Create | `tests/test_unified_scorer.py` | Scorer unit tests |
| Modify | `app/db/opensearch.py` | Add k-NN fields, event_signature, merged_into |
| Modify | `app/ingestion/clusterer.py` | Full rewrite: call unified scorer, update merge/create |
| Modify | `app/ingestion/entity_extractor.py` | Call LLM NER first, merge with regex |
| Modify | `docker-compose.override.yml` | Add kiber-embedder service |
| Modify | `scripts/cluster_articles.py` | Pass embedding to cluster_article() |
| Modify | `tests/test_clusterer.py` | Update tests for new API |

---

## Task 1: Create branch and spec file

**Files:**
- (git operation)
- Create: `docs/superpowers/specs/2026-04-27-clustering-redesign.md`

- [ ] **Step 1: Create feature branch**

```bash
git checkout -b feat/clustering-redesign
```

- [ ] **Step 2: Create spec directory and write spec file**

```bash
mkdir -p docs/superpowers/specs
```

Write `docs/superpowers/specs/2026-04-27-clustering-redesign.md` with the full design from the plan file at `C:\Users\xb_admin\.claude\plans\c-users-xb-admin-desktop-omar-projects-wiggly-puppy.md`. Copy the content verbatim.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-04-27-clustering-redesign.md docs/superpowers/plans/2026-04-27-clustering-redesign.md
git commit -m "docs: add clustering redesign spec and implementation plan"
```

---

## Task 2: Alembic migration — ner_cache table

**Files:**
- Create: `alembic/versions/b1c2d3e4f5a6_add_ner_cache.py`

- [ ] **Step 1: Create migration file**

Create `alembic/versions/b1c2d3e4f5a6_add_ner_cache.py`:

```python
"""add ner_cache table

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-04-27 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b1c2d3e4f5a6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ner_cache",
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("entities_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "extracted_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("slug"),
    )


def downgrade() -> None:
    op.drop_table("ner_cache")
```

- [ ] **Step 2: Check the actual latest revision ID**

```bash
docker compose exec backend alembic history | head -5
```

Update `down_revision` in the file to match the actual latest revision (currently `a0b1c2d3e4f5` based on git status — verify this matches).

- [ ] **Step 3: Run migration**

```bash
docker compose exec backend alembic upgrade head
```

Expected output: `Running upgrade a0b1c2d3e4f5 -> b1c2d3e4f5a6, add ner_cache table`

- [ ] **Step 4: Verify table exists**

```bash
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c "\d ner_cache"
```

Expected: table with columns `slug`, `entities_json`, `extracted_at`.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/b1c2d3e4f5a6_add_ner_cache.py
git commit -m "feat(clustering): add ner_cache migration"
```

---

## Task 3: OpenSearch mapping — k-NN fields + event_signature

**Files:**
- Modify: `app/db/opensearch.py`

- [ ] **Step 1: Update `_CLUSTERS_MAPPING` settings to enable k-NN**

In `app/db/opensearch.py`, change the `_CLUSTERS_MAPPING` `"settings"` block (currently at line 97) to add `"index.knn": True`:

```python
_CLUSTERS_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "refresh_interval": "10s",
        "index.knn": True,          # ← ADD THIS
    },
    "mappings": { ...
```

- [ ] **Step 2: Add new fields to `_CLUSTERS_MAPPING` properties**

Inside `_CLUSTERS_MAPPING["mappings"]["properties"]`, add after the `"updated_at"` field:

```python
            "centroid_embedding": {
                "type": "knn_vector",
                "dimension": 1024,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosinesimil",
                    "engine": "nmslib",
                },
            },
            "event_signature": {
                "type": "object",
                "properties": {
                    "cve_ids":          {"type": "keyword"},
                    "vuln_aliases":     {"type": "keyword"},
                    "campaign_names":   {"type": "keyword"},
                    "affected_products":{"type": "keyword"},
                    "primary_actors":   {"type": "keyword"},
                    "confidence":       {"type": "keyword"},
                },
            },
            "merged_into": {"type": "keyword"},
```

- [ ] **Step 3: Add `article_embedding` to `NEWS_MAPPING`**

Add `"index.knn": True` to `NEWS_MAPPING["settings"]` and add to its `properties`:

```python
            "article_embedding": {
                "type": "knn_vector",
                "dimension": 1024,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosinesimil",
                    "engine": "nmslib",
                },
            },
```

- [ ] **Step 4: Update `ensure_indexes()` to handle k-NN index recreation**

Replace the `ensure_indexes()` function body:

```python
async def ensure_indexes() -> None:
    """Create or update OpenSearch indexes. Recreates clusters index if k-NN not enabled."""
    import logging
    log = logging.getLogger(__name__)
    client = get_os_client()

    for index, mapping in [
        (INDEX_NEWS, NEWS_MAPPING),
        (INDEX_SNAPSHOTS, _SNAPSHOTS_MAPPING),
        (INDEX_CLUSTERS, _CLUSTERS_MAPPING),
        (INDEX_ENTITIES, _ENTITIES_MAPPING),
        (INDEX_NVD_CACHE, _NVD_CACHE_MAPPING),
    ]:
        try:
            exists = await client.indices.exists(index=index)
            needs_knn = mapping["settings"].get("index.knn", False)

            if exists and needs_knn:
                # k-NN setting is immutable — check if already enabled
                settings_resp = await client.indices.get_settings(index=index)
                knn_on = settings_resp[index]["settings"].get("index", {}).get("knn") == "true"
                if not knn_on:
                    if index == INDEX_CLUSTERS:
                        # Safe: clusters are always rebuilt from scratch via --reset
                        log.warning("Recreating %s index to enable k-NN", index)
                        await client.indices.delete(index=index)
                        await client.indices.create(index=index, body=mapping)
                    else:
                        log.warning(
                            "Index '%s' exists without k-NN. article_embedding will not be "
                            "k-NN searchable until the index is manually reindexed.",
                            index,
                        )
                        # Still try to add the field via put_mapping (stores but not k-NN indexed)
                        try:
                            await client.indices.put_mapping(
                                index=index,
                                body={"properties": mapping["mappings"]["properties"]},
                            )
                        except Exception:
                            pass
                else:
                    await client.indices.put_mapping(
                        index=index,
                        body={"properties": mapping["mappings"]["properties"]},
                    )
                    log.info("Updated mapping for index: %s", index)
            elif not exists:
                await client.indices.create(index=index, body=mapping)
                log.info("Created OpenSearch index: %s", index)
            else:
                await client.indices.put_mapping(
                    index=index,
                    body={"properties": mapping["mappings"]["properties"]},
                )
                log.info("Updated mapping for index: %s", index)
        except Exception as exc:
            log.warning("Could not ensure index '%s': %s", index, exc)
```

- [ ] **Step 5: Restart backend to run ensure_indexes()**

```bash
docker compose restart backend
docker compose logs backend | grep -E "(index|knn|mapping)" | tail -20
```

Expected: `Recreating clusters index to enable k-NN` (if clusters existed without k-NN), then `Created OpenSearch index: clusters` or `Updated mapping for index: clusters`.

- [ ] **Step 6: Verify k-NN enabled on clusters index**

```bash
docker compose exec ingestion python -c "
import asyncio
from app.db.opensearch import get_os_client, INDEX_CLUSTERS
async def check():
    c = get_os_client()
    s = await c.indices.get_settings(index=INDEX_CLUSTERS)
    print('knn:', s[INDEX_CLUSTERS]['settings'].get('index', {}).get('knn'))
asyncio.run(check())
"
```

Expected: `knn: true`

- [ ] **Step 7: Commit**

```bash
git add app/db/opensearch.py
git commit -m "feat(clustering): add k-NN fields and event_signature to OpenSearch mappings"
```

---

## Task 4: Embedding service Docker container

**Files:**
- Create: `services/embedder/requirements.txt`
- Create: `services/embedder/main.py`
- Create: `services/embedder/Dockerfile`

- [ ] **Step 1: Create `services/embedder/requirements.txt`**

```
fastapi==0.115.0
uvicorn[standard]==0.30.0
sentence-transformers==3.1.1
torch==2.3.0
numpy==1.26.4
```

- [ ] **Step 2: Create `services/embedder/main.py`**

```python
import torch
from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

_INSTRUCTION = "Represent this cybersecurity article for finding related articles: "
_MODEL_NAME = "BAAI/bge-large-en-v1.5"
_device = "cuda" if torch.cuda.is_available() else "cpu"
_model = SentenceTransformer(_MODEL_NAME, device=_device)

app = FastAPI()


class EmbedRequest(BaseModel):
    text: str


class BatchEmbedRequest(BaseModel):
    texts: list[str]


@app.get("/health")
def health():
    return {"status": "ok", "device": _device, "model": _MODEL_NAME}


@app.post("/embed")
def embed(req: EmbedRequest):
    vec = _model.encode(_INSTRUCTION + req.text, normalize_embeddings=True)
    return {"embedding": vec.tolist()}


@app.post("/embed/batch")
def embed_batch(req: BatchEmbedRequest):
    texts = [_INSTRUCTION + t for t in req.texts]
    vecs = _model.encode(texts, normalize_embeddings=True, batch_size=32)
    return {"embeddings": [v.tolist() for v in vecs]}
```

- [ ] **Step 3: Create `services/embedder/Dockerfile`**

```dockerfile
FROM pytorch/pytorch:2.3.0-cuda12.1-cudnn8-runtime

WORKDIR /app
ENV TRANSFORMERS_CACHE=/model-cache

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
```

- [ ] **Step 4: Add `kiber-embedder` to `docker-compose.override.yml`**

Append to the existing `docker-compose.override.yml`:

```yaml
  kiber-embedder:
    build: ./services/embedder
    ports:
      - "8001:8001"
    volumes:
      - embedder-model-cache:/model-cache
    environment:
      TRANSFORMERS_CACHE: /model-cache
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    restart: unless-stopped

volumes:
  embedder-model-cache:
```

- [ ] **Step 5: Build and start the embedder**

```bash
docker compose build kiber-embedder
docker compose up -d kiber-embedder
```

First start downloads ~1.3GB model weights (cached in volume for subsequent starts).

- [ ] **Step 6: Verify health**

```bash
curl http://localhost:8001/health
```

Expected: `{"status":"ok","device":"cuda","model":"BAAI/bge-large-en-v1.5"}`

- [ ] **Step 7: Smoke test embedding**

```bash
curl -s -X POST http://localhost:8001/embed \
  -H "Content-Type: application/json" \
  -d '{"text": "Critical FortiGate vulnerability CVE-2024-1234"}' | python -c "
import sys, json
d = json.load(sys.stdin)
emb = d['embedding']
print(f'dim={len(emb)}, first3={emb[:3]}')
"
```

Expected: `dim=1024, first3=[<float>, <float>, <float>]`

- [ ] **Step 8: Commit**

```bash
git add services/embedder/ docker-compose.override.yml
git commit -m "feat(clustering): add GPU embedding service (bge-large-en-v1.5)"
```

---

## Task 5: Embedding client

**Files:**
- Create: `app/ingestion/embedding_client.py`
- Create: `tests/test_embedding_client.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_embedding_client.py`:

```python
"""Tests for app.ingestion.embedding_client."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx


@pytest.mark.asyncio
async def test_embed_text_returns_list_of_floats():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"embedding": [0.1] * 1024}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        from app.ingestion.embedding_client import embed_text
        result = await embed_text("Critical vulnerability in Apache Log4j")

    assert isinstance(result, list)
    assert len(result) == 1024
    assert all(isinstance(v, float) for v in result)


@pytest.mark.asyncio
async def test_embed_text_returns_none_on_timeout():
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client_cls.return_value = mock_client

        from app.ingestion.embedding_client import embed_text
        result = await embed_text("some article text")

    assert result is None


@pytest.mark.asyncio
async def test_embed_batch_returns_list_of_embeddings():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"embeddings": [[0.1] * 1024, [0.2] * 1024]}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        from app.ingestion.embedding_client import embed_batch
        result = await embed_batch(["article one", "article two"])

    assert len(result) == 2
    assert len(result[0]) == 1024


@pytest.mark.asyncio
async def test_embed_batch_returns_nones_on_error():
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
        mock_client_cls.return_value = mock_client

        from app.ingestion.embedding_client import embed_batch
        result = await embed_batch(["a", "b", "c"])

    assert result == [None, None, None]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec ingestion pytest tests/test_embedding_client.py -v 2>&1 | tail -20
```

Expected: `ImportError` or `ModuleNotFoundError` for `embedding_client`.

- [ ] **Step 3: Create `app/ingestion/embedding_client.py`**

```python
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_EMBEDDER_URL = os.getenv("EMBEDDER_URL", "http://kiber-embedder:8001")
_TIMEOUT = 2.0
_BATCH_TIMEOUT = 60.0


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
    async with httpx.AsyncClient(timeout=_BATCH_TIMEOUT) as client:
        try:
            resp = await client.post(f"{_EMBEDDER_URL}/embed/batch", json={"texts": texts})
            resp.raise_for_status()
            return resp.json()["embeddings"]
        except Exception as exc:
            logger.warning("embed_batch failed: %s", exc)
            return [None] * len(texts)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec ingestion pytest tests/test_embedding_client.py -v
```

Expected: all 4 tests `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/embedding_client.py tests/test_embedding_client.py
git commit -m "feat(clustering): add async embedding client"
```

---

## Task 6: LLM NER module

**Files:**
- Create: `app/ingestion/ner_llm.py`
- Create: `tests/test_ner_llm.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_ner_llm.py`:

```python
"""Tests for app.ingestion.ner_llm."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_anthropic_response(entities: list[dict]):
    """Build a mock Anthropic tool_use response."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = {"entities": entities}
    resp = MagicMock()
    resp.content = [tool_block]
    return resp


@pytest.mark.asyncio
async def test_extract_entities_returns_cve_and_vuln_alias():
    mock_entities = [
        {"type": "cve", "name": "CVE-2021-44228", "normalized_key": "CVE-2021-44228"},
        {"type": "vuln_alias", "name": "Log4Shell", "normalized_key": "log4shell"},
        {"type": "actor", "name": "Lazarus Group", "normalized_key": "lazarus-group"},
    ]

    with patch("app.ingestion.ner_llm._client") as mock_client:
        mock_client.messages.create.return_value = _make_anthropic_response(mock_entities)

        from app.ingestion.ner_llm import extract_entities_llm
        result = await extract_entities_llm(
            slug="test-article",
            title="Log4Shell exploited by Lazarus Group",
            summary="North Korean threat actor actively exploiting CVE-2021-44228.",
            db_session=None,  # skip cache
        )

    assert any(e["type"] == "vuln_alias" and e["normalized_key"] == "log4shell" for e in result)
    assert any(e["type"] == "cve" and e["normalized_key"] == "CVE-2021-44228" for e in result)
    assert any(e["type"] == "actor" and e["normalized_key"] == "lazarus-group" for e in result)


@pytest.mark.asyncio
async def test_extract_entities_returns_empty_on_llm_failure():
    with patch("app.ingestion.ner_llm._client") as mock_client:
        mock_client.messages.create.side_effect = Exception("API error")

        from app.ingestion.ner_llm import extract_entities_llm
        result = await extract_entities_llm(
            slug="fail-article",
            title="Some article",
            summary="Some content.",
            db_session=None,
        )

    assert result == []


@pytest.mark.asyncio
async def test_extract_entities_uses_cache_on_hit():
    cached_entities = [
        {"type": "malware", "name": "LockBit", "normalized_key": "lockbit"}
    ]

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (cached_entities,)
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch("app.ingestion.ner_llm._client") as mock_client:
        from app.ingestion.ner_llm import extract_entities_llm
        result = await extract_entities_llm(
            slug="cached-article",
            title="LockBit ransomware",
            summary="LockBit 3.0 targets healthcare.",
            db_session=mock_session,
        )

    mock_client.messages.create.assert_not_called()
    assert result == cached_entities


@pytest.mark.asyncio
async def test_extract_entities_caches_result():
    mock_entities = [
        {"type": "campaign", "name": "MOVEit campaign", "normalized_key": "moveit-campaign"}
    ]

    mock_session = AsyncMock()
    # Cache miss
    miss_result = MagicMock()
    miss_result.fetchone.return_value = None
    mock_session.execute = AsyncMock(return_value=miss_result)

    with patch("app.ingestion.ner_llm._client") as mock_client:
        mock_client.messages.create.return_value = _make_anthropic_response(mock_entities)

        from app.ingestion.ner_llm import extract_entities_llm
        result = await extract_entities_llm(
            slug="new-article",
            title="MOVEit Transfer breach",
            summary="Mass exploitation of MOVEit Transfer.",
            db_session=mock_session,
        )

    assert mock_session.execute.call_count == 2  # one SELECT, one INSERT
    assert result == mock_entities
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec ingestion pytest tests/test_ner_llm.py -v 2>&1 | tail -10
```

Expected: `ImportError` for `ner_llm`.

- [ ] **Step 3: Create `app/ingestion/ner_llm.py`**

```python
"""Claude Haiku NER — extracts security entities from article text.

Caches results in Postgres ner_cache table. Pass db_session=None to skip cache
(useful for unit tests and one-off calls).
"""
import json
import logging
import os
from typing import Optional

import anthropic
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024

_SYSTEM_PROMPT = """You are a cybersecurity named entity extractor. Extract security-relevant entities from the article title and summary.

Entity types:
- cve: CVE identifiers. Keep format (e.g. "CVE-2021-44228"). normalized_key = same as name.
- product: software/hardware, include version if present. "FortiGate 7.4.2" → "fortigate-7.4.2". Skip bare names without version (skip "Windows", include "Windows 11 23H2" → "windows-11-23h2").
- malware: malware families. "LockBit 3.0" → "lockbit-3.0". "BlackCat" → "blackcat".
- actor: threat actor groups. "Lazarus Group" → "lazarus-group". "APT29" → "apt29".
- tool: attack tools/frameworks. "Cobalt Strike" → "cobalt-strike". "Mimikatz" → "mimikatz".
- vuln_alias: vulnerability nicknames. "Log4Shell" → "log4shell". "Heartbleed" → "heartbleed". "CitrixBleed" → "citrixbleed". "PrintNightmare" → "printnightmare".
- campaign: named incidents or campaigns. "MOVEit Transfer campaign" → "moveit-transfer-campaign". "SolarWinds breach" → "solarwinds-breach".

Normalization: lowercase everything, spaces and special chars → hyphens. Only extract entities you are confident about."""

_TOOL = {
    "name": "extract_entities",
    "description": "Return extracted security entities from the article.",
    "input_schema": {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["cve", "product", "malware", "actor", "tool", "vuln_alias", "campaign"],
                        },
                        "name": {"type": "string"},
                        "normalized_key": {"type": "string"},
                    },
                    "required": ["type", "name", "normalized_key"],
                },
            }
        },
        "required": ["entities"],
    },
}


async def _get_cached(slug: str, session: AsyncSession) -> Optional[list[dict]]:
    result = await session.execute(
        text("SELECT entities_json FROM ner_cache WHERE slug = :slug"),
        {"slug": slug},
    )
    row = result.fetchone()
    return row[0] if row else None


async def _write_cache(slug: str, entities: list[dict], session: AsyncSession) -> None:
    await session.execute(
        text(
            "INSERT INTO ner_cache (slug, entities_json, extracted_at) "
            "VALUES (:slug, :entities, NOW()) "
            "ON CONFLICT (slug) DO NOTHING"
        ),
        {"slug": slug, "entities": json.dumps(entities)},
    )
    await session.commit()


async def extract_entities_llm(
    slug: str,
    title: str,
    summary: str,
    db_session: Optional[AsyncSession],
) -> list[dict]:
    """Extract entities via Claude Haiku. Returns list of entity dicts.

    Falls back to [] if the LLM call fails. Results are cached in Postgres by slug.
    Pass db_session=None to skip cache (testing / one-off use).
    """
    if db_session is not None:
        cached = await _get_cached(slug, db_session)
        if cached is not None:
            return cached

    text_input = f"Title: {title}\nSummary: {(summary or '')[:500]}"
    try:
        response = _client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "extract_entities"},
            messages=[{"role": "user", "content": text_input}],
        )
        tool_block = next((b for b in response.content if b.type == "tool_use"), None)
        entities = tool_block.input.get("entities", []) if tool_block else []
    except Exception as exc:
        logger.warning("LLM NER failed for slug=%s: %s", slug, exc)
        entities = []

    if db_session is not None:
        await _write_cache(slug, entities, db_session)

    return entities
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec ingestion pytest tests/test_ner_llm.py -v
```

Expected: all 4 tests `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/ner_llm.py tests/test_ner_llm.py
git commit -m "feat(clustering): add Claude Haiku NER module with Postgres cache"
```

---

## Task 7: Update entity extractor to call LLM NER first

**Files:**
- Modify: `app/ingestion/entity_extractor.py`

- [ ] **Step 1: Locate the main entry point function**

Read `app/ingestion/entity_extractor.py` and find the `extract_entities()` function signature (the one called from ingest/normalizer). It currently accepts article text fields and returns a list of entity dicts.

- [ ] **Step 2: Add LLM NER call at the top of `extract_entities()`**

The goal: LLM NER runs first and produces entities. The existing regex/gazetteer runs second and adds any it finds that the LLM missed. Results are merged by `normalized_key`.

Find the `extract_entities()` function and add the following pattern at the start. The function signature gains two optional parameters: `slug` and `db_session` (both default to `None` for backwards compat):

```python
async def extract_entities(
    title: str,
    desc: str,
    summary: str,
    cve_ids: list[str],
    *,
    slug: str | None = None,
    db_session=None,
) -> list[dict]:
    """Extract entities from article text. LLM NER runs first; regex fills gaps."""
    llm_entities: list[dict] = []
    if slug:
        from app.ingestion.ner_llm import extract_entities_llm
        llm_entities = await extract_entities_llm(
            slug=slug,
            title=title,
            summary=summary or desc,
            db_session=db_session,
        )

    # Existing regex/gazetteer extraction (unchanged logic below)
    regex_entities = _extract_regex(title, desc, summary, cve_ids)

    # Merge: LLM is authoritative; regex adds anything new by normalized_key
    seen_keys = {e["normalized_key"] for e in llm_entities}
    merged = list(llm_entities)
    for e in regex_entities:
        if e["normalized_key"] not in seen_keys:
            merged.append(e)
            seen_keys.add(e["normalized_key"])
    return merged
```

Note: Rename the existing synchronous extraction logic into a private helper `_extract_regex(title, desc, summary, cve_ids)` that contains all the current regex/gazetteer code. This is a refactor-in-place — all existing logic stays intact, just moved into the helper.

- [ ] **Step 3: Run existing entity extractor tests**

```bash
docker compose exec ingestion pytest tests/test_entity_extractor.py -v
```

Expected: all existing tests still `PASSED` (the new params have defaults so call sites don't break).

- [ ] **Step 4: Commit**

```bash
git add app/ingestion/entity_extractor.py
git commit -m "feat(clustering): integrate LLM NER into entity extractor pipeline"
```

---

## Task 8: Unified scorer

**Files:**
- Create: `app/ingestion/unified_scorer.py`
- Create: `tests/test_unified_scorer.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_unified_scorer.py`:

```python
"""Tests for app.ingestion.unified_scorer."""
import pytest
from unittest.mock import AsyncMock, patch
import numpy as np


def _make_cluster(
    cluster_id: str,
    cve_ids: list[str] = None,
    vuln_aliases: list[str] = None,
    campaign_names: list[str] = None,
    entity_keys: list[str] = None,
    centroid: list[float] = None,
    article_count: int = 1,
    state: str = "new",
) -> dict:
    return {
        "_id": cluster_id,
        "_source": {
            "article_count": article_count,
            "state": state,
            "entity_keys": entity_keys or [],
            "event_signature": {
                "cve_ids": cve_ids or [],
                "vuln_aliases": vuln_aliases or [],
                "campaign_names": campaign_names or [],
                "affected_products": [],
                "primary_actors": [],
                "confidence": "medium",
            },
            "centroid_embedding": centroid,
        },
    }


def _make_article_entities(types_keys: list[tuple]) -> list[dict]:
    return [{"type": t, "normalized_key": k} for t, k in types_keys]


# ---------------------------------------------------------------------------
# Score formula
# ---------------------------------------------------------------------------

def test_score_perfect_match_is_one():
    from app.ingestion.unified_scorer import _compute_score

    emb = [1.0] + [0.0] * 1023
    article_entities = _make_article_entities([
        ("cve", "CVE-2024-1234"),
        ("vuln_alias", "log4shell"),
        ("malware", "lockbit"),
    ])
    cluster = _make_cluster(
        "c1",
        cve_ids=["CVE-2024-1234"],
        vuln_aliases=["log4shell"],
        entity_keys=["lockbit"],
        centroid=emb,
    )
    score = _compute_score(article_entities, cluster["_source"], emb)
    # 0.45 + 0.25 + 0.15 * (1/1) + 0.15 * 1.0 = 1.0
    assert abs(score - 1.0) < 0.01


def test_score_embedding_only_cannot_exceed_threshold():
    """Pure embedding match (no structured signals) must score below 0.30 threshold."""
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    emb = [1.0] + [0.0] * 1023
    article_entities = []  # no entities
    cluster = _make_cluster("c1", centroid=emb)
    score = _compute_score(article_entities, cluster["_source"], emb)
    assert score < ASSIGN_THRESHOLD


def test_score_cve_overlap_only():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("cve", "CVE-2024-9999")])
    cluster = _make_cluster("c1", cve_ids=["CVE-2024-9999"])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.45) < 0.01


def test_score_alias_overlap_only():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("vuln_alias", "heartbleed")])
    cluster = _make_cluster("c1", vuln_aliases=["heartbleed"])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.25) < 0.01


def test_score_cve_plus_alias_exceeds_threshold():
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    article_entities = _make_article_entities([
        ("cve", "CVE-2024-1234"),
        ("vuln_alias", "citrixbleed"),
    ])
    cluster = _make_cluster("c1", cve_ids=["CVE-2024-1234"], vuln_aliases=["citrixbleed"])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert score >= ASSIGN_THRESHOLD


# ---------------------------------------------------------------------------
# find_best_cluster
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_best_cluster_returns_none_below_threshold():
    from app.ingestion.unified_scorer import find_best_cluster

    article_entities = []
    article_embedding = None

    with patch("app.ingestion.unified_scorer._get_candidates", new_callable=AsyncMock) as mock_cands:
        mock_cands.return_value = []
        result = await find_best_cluster(article_entities, article_embedding)

    assert result is None


@pytest.mark.asyncio
async def test_find_best_cluster_returns_highest_scoring():
    from app.ingestion.unified_scorer import find_best_cluster

    low_cluster = _make_cluster("c-low", vuln_aliases=["log4shell"])
    high_cluster = _make_cluster("c-high", cve_ids=["CVE-2024-1234"], vuln_aliases=["log4shell"])

    article_entities = _make_article_entities([
        ("cve", "CVE-2024-1234"),
        ("vuln_alias", "log4shell"),
    ])

    with patch("app.ingestion.unified_scorer._get_candidates", new_callable=AsyncMock) as mock_cands:
        mock_cands.return_value = [low_cluster, high_cluster]
        result = await find_best_cluster(article_entities, None)

    assert result == "c-high"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec ingestion pytest tests/test_unified_scorer.py -v 2>&1 | tail -10
```

Expected: `ImportError` for `unified_scorer`.

- [ ] **Step 3: Create `app/ingestion/unified_scorer.py`**

```python
"""Unified cluster scoring: replaces the 3-tier CVE/entity/MLT waterfall.

Candidate retrieval: OpenSearch structured terms + k-NN, then score each candidate.
Best cluster above ASSIGN_THRESHOLD wins; None means create a new cluster.
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from app.db.opensearch import INDEX_CLUSTERS, get_os_client

logger = logging.getLogger(__name__)

ASSIGN_THRESHOLD = float(os.getenv("CLUSTER_SCORE_THRESHOLD", "0.30"))
MERGE_THRESHOLD = float(os.getenv("CLUSTER_MERGE_THRESHOLD", "0.55"))

_W_CVE = float(os.getenv("CLUSTER_WEIGHT_CVE", "0.45"))
_W_ALIAS = float(os.getenv("CLUSTER_WEIGHT_ALIAS", "0.25"))
_W_ENTITY = float(os.getenv("CLUSTER_WEIGHT_ENTITY", "0.15"))
_W_EMBED = float(os.getenv("CLUSTER_WEIGHT_EMBED", "0.15"))

_KNN_K = 10
_STRUCTURED_WINDOW_DAYS = 14
_EMBED_WINDOW_HOURS = 72

_SOURCE_FIELDS = [
    "article_count", "state", "entity_keys",
    "event_signature", "centroid_embedding",
]


def _compute_score(
    article_entities: list[dict],
    cluster_source: dict,
    article_embedding: Optional[list[float]],
) -> float:
    sig = cluster_source.get("event_signature") or {}

    art_cves = {e["normalized_key"] for e in article_entities if e["type"] == "cve"}
    art_aliases = {
        e["normalized_key"]
        for e in article_entities
        if e["type"] in ("vuln_alias", "campaign")
    }
    art_others = {
        e["normalized_key"]
        for e in article_entities
        if e["type"] not in ("cve", "vuln_alias", "campaign", "vendor")
    }

    cl_cves = set(sig.get("cve_ids") or [])
    cl_aliases = set((sig.get("vuln_aliases") or []) + (sig.get("campaign_names") or []))
    cl_others = set(cluster_source.get("entity_keys") or []) - cl_cves - cl_aliases

    cve_overlap = 1.0 if art_cves & cl_cves else 0.0
    alias_overlap = 1.0 if art_aliases & cl_aliases else 0.0

    union_others = art_others | cl_others
    entity_jaccard = (
        len(art_others & cl_others) / len(union_others) if union_others else 0.0
    )

    cosine = 0.0
    centroid = cluster_source.get("centroid_embedding")
    if article_embedding and centroid:
        a = np.array(article_embedding, dtype=np.float32)
        c = np.array(centroid, dtype=np.float32)
        denom = np.linalg.norm(a) * np.linalg.norm(c)
        if denom > 0:
            cosine = max(0.0, float(np.dot(a, c) / denom))

    return (
        _W_CVE * cve_overlap
        + _W_ALIAS * alias_overlap
        + _W_ENTITY * entity_jaccard
        + _W_EMBED * cosine
    )


async def _get_candidates(
    article_entities: list[dict],
    article_embedding: Optional[list[float]],
) -> list[dict]:
    os_client = get_os_client()
    now = datetime.now(timezone.utc)
    cutoff_14d = (now - timedelta(days=_STRUCTURED_WINDOW_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    cutoff_72h = (now - timedelta(hours=_EMBED_WINDOW_HOURS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    cve_ids = [e["normalized_key"] for e in article_entities if e["type"] == "cve"]
    vuln_aliases = [e["normalized_key"] for e in article_entities if e["type"] == "vuln_alias"]
    campaign_names = [e["normalized_key"] for e in article_entities if e["type"] == "campaign"]

    candidates: dict[str, dict] = {}

    # Structured lookup (terms query on event_signature)
    should_clauses = []
    for cve in cve_ids:
        should_clauses.append({"term": {"event_signature.cve_ids": cve}})
    for alias in vuln_aliases:
        should_clauses.append({"term": {"event_signature.vuln_aliases": alias}})
    for campaign in campaign_names:
        should_clauses.append({"term": {"event_signature.campaign_names": campaign}})

    if should_clauses:
        structured_query = {
            "query": {
                "bool": {
                    "should": should_clauses,
                    "minimum_should_match": 1,
                    "filter": [
                        {"range": {"latest_at": {"gte": cutoff_14d}}},
                        {"bool": {"must_not": [{"term": {"state": "resolved"}}]}},
                    ],
                }
            },
            "_source": _SOURCE_FIELDS,
            "size": 20,
        }
        try:
            resp = await os_client.search(index=INDEX_CLUSTERS, body=structured_query)
            for hit in resp["hits"]["hits"]:
                candidates[hit["_id"]] = hit
        except Exception as exc:
            logger.warning("Structured candidate lookup failed: %s", exc)

    # k-NN lookup (embedding similarity)
    if article_embedding:
        knn_query = {
            "size": _KNN_K,
            "query": {
                "knn": {
                    "centroid_embedding": {
                        "vector": article_embedding,
                        "k": _KNN_K,
                        "filter": {
                            "bool": {
                                "must": [{"range": {"latest_at": {"gte": cutoff_72h}}}],
                                "must_not": [{"term": {"state": "resolved"}}],
                            }
                        },
                    }
                }
            },
            "_source": _SOURCE_FIELDS,
        }
        try:
            resp = await os_client.search(index=INDEX_CLUSTERS, body=knn_query)
            for hit in resp["hits"]["hits"]:
                if hit["_id"] not in candidates:
                    candidates[hit["_id"]] = hit
        except Exception as exc:
            logger.warning("k-NN candidate lookup failed: %s", exc)

    return list(candidates.values())


async def find_best_cluster(
    article_entities: list[dict],
    article_embedding: Optional[list[float]],
) -> Optional[str]:
    """Return the cluster_id of the best matching cluster, or None to create new."""
    candidates = await _get_candidates(article_entities, article_embedding)
    if not candidates:
        return None

    best_id: Optional[str] = None
    best_score = -1.0

    for hit in candidates:
        score = _compute_score(article_entities, hit["_source"], article_embedding)
        if score > best_score:
            best_score = score
            best_id = hit["_id"]

    if best_score >= ASSIGN_THRESHOLD:
        logger.debug("Best cluster %s score=%.3f", best_id, best_score)
        return best_id

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec ingestion pytest tests/test_unified_scorer.py -v
```

Expected: all 7 tests `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/unified_scorer.py tests/test_unified_scorer.py
git commit -m "feat(clustering): add unified scorer with k-NN + structured candidate retrieval"
```

---

## Task 9: Rewrite clusterer.py

**Files:**
- Modify: `app/ingestion/clusterer.py`
- Modify: `tests/test_clusterer.py`

- [ ] **Step 1: Read the full current `app/ingestion/clusterer.py`**

Read the file carefully. Note the signatures of `cluster_article()`, `merge_into_cluster()`, and `create_cluster()` — you will keep them API-compatible so other call sites don't break.

- [ ] **Step 2: Rewrite `app/ingestion/clusterer.py`**

Replace the file content with:

```python
"""Cluster assignment: unified scorer replaces the 3-tier CVE/entity/MLT waterfall.

Public API (unchanged from previous version):
  cluster_article(article, slug, entities) → None
  merge_into_cluster(cluster_id, slug, entity_keys, cve_ids, *, ...) → None
  create_cluster(article, entity_keys, *, embedding) → str
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.db.opensearch import INDEX_CLUSTERS, INDEX_NEWS, get_os_client
from app.ingestion.embedding_client import embed_text
from app.ingestion.scorer import score_cluster
from app.ingestion.unified_scorer import find_best_cluster

logger = logging.getLogger(__name__)

_EMBED_INPUT_MAX = 400  # chars of summary/desc to include in embedding input


def _build_embed_input(article: dict) -> str:
    text = article.get("title", "")
    snippet = article.get("summary") or article.get("desc") or ""
    if snippet:
        text += ". " + snippet[:_EMBED_INPUT_MAX]
    return text


def _build_event_signature(entities: list[dict], cve_ids: list[str]) -> dict:
    sig: dict = {
        "cve_ids": list(dict.fromkeys(cve_ids)),
        "vuln_aliases": [],
        "campaign_names": [],
        "affected_products": [],
        "primary_actors": [],
        "confidence": "low",
    }
    for e in entities:
        t = e["type"]
        k = e["normalized_key"]
        if t == "vuln_alias":
            sig["vuln_aliases"].append(k)
        elif t == "campaign":
            sig["campaign_names"].append(k)
        elif t == "product":
            sig["affected_products"].append(k)
        elif t == "actor":
            sig["primary_actors"].append(k)

    if len(sig["cve_ids"]) >= 2 or (sig["cve_ids"] and sig["vuln_aliases"]):
        sig["confidence"] = "high"
    elif sig["cve_ids"] or sig["vuln_aliases"] or sig["campaign_names"]:
        sig["confidence"] = "medium"
    return sig


def _updated_centroid(old_centroid: Optional[list[float]], new_vec: list[float], n: int) -> list[float]:
    """Running average: new_centroid = (old * (n-1) + new) / n."""
    if old_centroid is None or n <= 1:
        return new_vec
    import numpy as np
    c = (np.array(old_centroid) * (n - 1) + np.array(new_vec)) / n
    return c.tolist()


async def cluster_article(
    article: dict,
    slug: str,
    entities: list[dict],
) -> None:
    """Assign article to an existing cluster or create a new one."""
    cve_ids: list[str] = article.get("cve_ids") or []
    embedding = await embed_text(_build_embed_input(article))

    cluster_id = await find_best_cluster(entities, embedding)

    if cluster_id:
        await merge_into_cluster(
            cluster_id,
            slug,
            [e["normalized_key"] for e in entities],
            cve_ids,
            source_name=article.get("source_name", ""),
            title=article.get("title", ""),
            published_at=article.get("published_at", ""),
            cvss_score=article.get("cvss_score"),
            credibility_weight=float(article.get("credibility_weight") or 1.0),
            new_entities=entities,
            new_embedding=embedding,
        )
    else:
        await create_cluster(article, entities, embedding=embedding)


async def merge_into_cluster(
    cluster_id: str,
    article_slug: str,
    entity_keys: list[str],
    cve_ids: list[str],
    *,
    source_name: str = "",
    title: str = "",
    published_at: str = "",
    cvss_score: Optional[float] = None,
    credibility_weight: float = 1.0,
    new_entities: Optional[list[dict]] = None,
    new_embedding: Optional[list[float]] = None,
) -> None:
    os_client = get_os_client()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Fetch current cluster to compute centroid update
    try:
        existing = await os_client.get(index=INDEX_CLUSTERS, id=cluster_id, _source=True)
        src = existing["_source"]
        old_centroid = src.get("centroid_embedding")
        old_count = src.get("article_count", 1)
        old_sig = src.get("event_signature") or {}
    except Exception:
        old_centroid = None
        old_count = 1
        old_sig = {}

    new_count = old_count + 1
    new_centroid = (
        _updated_centroid(old_centroid, new_embedding, new_count)
        if new_embedding
        else old_centroid
    )

    # Merge event_signature fields
    new_sig_entities = new_entities or []
    sig_update = {
        "cve_ids": list(dict.fromkeys((old_sig.get("cve_ids") or []) + cve_ids)),
        "vuln_aliases": list(dict.fromkeys(
            (old_sig.get("vuln_aliases") or []) +
            [e["normalized_key"] for e in new_sig_entities if e["type"] == "vuln_alias"]
        )),
        "campaign_names": list(dict.fromkeys(
            (old_sig.get("campaign_names") or []) +
            [e["normalized_key"] for e in new_sig_entities if e["type"] == "campaign"]
        )),
        "affected_products": list(dict.fromkeys(
            (old_sig.get("affected_products") or []) +
            [e["normalized_key"] for e in new_sig_entities if e["type"] == "product"]
        )),
        "primary_actors": list(dict.fromkeys(
            (old_sig.get("primary_actors") or []) +
            [e["normalized_key"] for e in new_sig_entities if e["type"] == "actor"]
        )),
    }
    if len(sig_update["cve_ids"]) >= 2 or (sig_update["cve_ids"] and sig_update["vuln_aliases"]):
        sig_update["confidence"] = "high"
    elif sig_update["cve_ids"] or sig_update["vuln_aliases"] or sig_update["campaign_names"]:
        sig_update["confidence"] = "medium"
    else:
        sig_update["confidence"] = old_sig.get("confidence", "low")

    script_source = """
        // Dedup and add article
        if (!ctx._source.article_ids.contains(params.slug)) {
            ctx._source.article_ids.add(params.slug);
            ctx._source.article_count += 1;
        }

        // Lifecycle state
        if (ctx._source.article_count >= 3) {
            ctx._source.state = 'confirmed';
        } else if (ctx._source.article_count >= 2) {
            if (ctx._source.state == 'new') ctx._source.state = 'developing';
        }

        // Entity keys (dedup)
        for (key in params.entity_keys) {
            if (!ctx._source.entity_keys.contains(key)) {
                ctx._source.entity_keys.add(key);
            }
        }

        // CVE ids (dedup, grow)
        for (cve in params.cve_ids) {
            if (!ctx._source.cve_ids.contains(cve)) {
                ctx._source.cve_ids.add(cve);
            }
        }

        // CVSS max
        if (params.cvss_score != null && params.cvss_score > ctx._source.max_cvss) {
            ctx._source.max_cvss = params.cvss_score;
        }

        // Credibility max
        if (params.credibility_weight > ctx._source.max_credibility_weight) {
            ctx._source.max_credibility_weight = params.credibility_weight;
        }

        // Timeline (dedup by slug)
        boolean found = false;
        for (entry in ctx._source.timeline) {
            if (entry.article_slug == params.slug) { found = true; break; }
        }
        if (!found) {
            ctx._source.timeline.add(params.timeline_entry);
        }

        // Timestamps
        if (params.published_at > ctx._source.latest_at) {
            ctx._source.latest_at = params.published_at;
        }
        ctx._source.updated_at = params.now;

        // Event signature
        ctx._source.event_signature = params.event_signature;

        // Centroid embedding
        if (params.centroid != null) {
            ctx._source.centroid_embedding = params.centroid;
        }
    """

    await os_client.update(
        index=INDEX_CLUSTERS,
        id=cluster_id,
        body={
            "script": {
                "source": script_source,
                "lang": "painless",
                "params": {
                    "slug": article_slug,
                    "entity_keys": entity_keys,
                    "cve_ids": cve_ids,
                    "cvss_score": cvss_score or 0.0,
                    "credibility_weight": credibility_weight,
                    "published_at": published_at or now,
                    "now": now,
                    "timeline_entry": {
                        "article_slug": article_slug,
                        "source_name": source_name,
                        "title": title,
                        "published_at": published_at or now,
                        "added_at": now,
                    },
                    "event_signature": sig_update,
                    "centroid": new_centroid,
                },
            },
            "upsert": {},
        },
        retry_on_conflict=3,
    )

    # Update article with cluster_id back-reference
    await os_client.update(
        index=INDEX_NEWS,
        id=article_slug,
        body={"doc": {"cluster_id": cluster_id}},
        retry_on_conflict=3,
    )

    await _rescore(cluster_id)


async def create_cluster(
    article: dict,
    entities: list[dict],
    *,
    embedding: Optional[list[float]] = None,
) -> str:
    os_client = get_os_client()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    slug = article.get("slug", "")
    cve_ids: list[str] = article.get("cve_ids") or []
    entity_keys = [e["normalized_key"] for e in entities]
    published_at = article.get("published_at") or now

    doc = {
        "label": article.get("title", ""),
        "state": "new",
        "summary": "",
        "why_it_matters": "",
        "score": 0.0,
        "confidence": "low",
        "max_cvss": article.get("cvss_score") or 0.0,
        "cisa_kev": False,
        "max_credibility_weight": float(article.get("credibility_weight") or 1.0),
        "top_factors": [],
        "article_ids": [slug],
        "categories": [article.get("category")] if article.get("category") else [],
        "tags": [],
        "article_count": 1,
        "cve_ids": cve_ids,
        "seed_cve_ids": cve_ids,
        "entity_keys": entity_keys,
        "event_signature": _build_event_signature(entities, cve_ids),
        "centroid_embedding": embedding,
        "merged_into": None,
        "timeline": [{
            "article_slug": slug,
            "source_name": article.get("source_name", ""),
            "title": article.get("title", ""),
            "published_at": published_at,
            "added_at": now,
        }],
        "latest_at": published_at,
        "created_at": now,
        "updated_at": now,
    }

    resp = await os_client.index(index=INDEX_CLUSTERS, body=doc)
    cluster_id = resp["_id"]

    await os_client.update(
        index=INDEX_NEWS,
        id=slug,
        body={"doc": {"cluster_id": cluster_id}},
        retry_on_conflict=3,
    )
    await _rescore(cluster_id)
    return cluster_id


async def _rescore(cluster_id: str) -> None:
    os_client = get_os_client()
    try:
        doc = await os_client.get(index=INDEX_CLUSTERS, id=cluster_id)
        src = doc["_source"]
        factors, new_score = score_cluster(src)
        confidence = "high" if new_score >= 75 else "medium" if new_score >= 45 else "low"
        await os_client.update(
            index=INDEX_CLUSTERS,
            id=cluster_id,
            body={"doc": {"score": new_score, "confidence": confidence, "top_factors": factors}},
        )
    except Exception as exc:
        logger.warning("Rescore failed for %s: %s", cluster_id, exc)
```

- [ ] **Step 3: Update `tests/test_clusterer.py`**

The old tests reference `find_cluster_by_cve`, `find_cluster_by_entities`, `find_cluster_by_mlt`, and `_signal_keys` — these no longer exist. Replace the entire file:

```python
"""Tests for the rewritten app.ingestion.clusterer (unified scorer)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


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

    with patch("app.ingestion.clusterer.embed_text", new_callable=AsyncMock, return_value=[0.1] * 1024), \
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

    with patch("app.ingestion.clusterer.embed_text", new_callable=AsyncMock, return_value=None), \
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
```

- [ ] **Step 4: Run all clusterer tests**

```bash
docker compose exec ingestion pytest tests/test_clusterer.py -v
```

Expected: all new tests `PASSED`.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
docker compose exec ingestion pytest tests/ -v 2>&1 | tail -30
```

Expected: all tests pass. Fix any failures before continuing.

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/clusterer.py tests/test_clusterer.py
git commit -m "feat(clustering): rewrite clusterer with unified scorer, event_signature, centroid embedding"
```

---

## Task 10: Update cluster_articles.py

**Files:**
- Modify: `scripts/cluster_articles.py`

- [ ] **Step 1: Read the current `scripts/cluster_articles.py`**

Find where `cluster_article()` is called and where articles are fetched from OpenSearch scroll.

- [ ] **Step 2: Update the scroll source fields**

Ensure the scroll query's `_source` field list includes `credibility_weight`, `source_name`, `cvss_score`, `summary`, `desc` (needed by embedding input builder and merge logic). These should already be present from the prior backfill fix — verify and add any missing ones.

- [ ] **Step 3: Ensure EMBEDDER_URL is in env for the ingestion container**

Add to `docker-compose.override.yml` under the `ingestion` service (or wherever the script runs):

```yaml
  ingestion:
    environment:
      EMBEDDER_URL: "http://kiber-embedder:8001"
      ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY}"
```

- [ ] **Step 4: Verify the script runs without error on a dry-run**

```bash
docker compose exec ingestion python scripts/cluster_articles.py --dry-run --limit 5
```

Expected: processes 5 articles, logs cluster assignments or new cluster creations, no exceptions.

- [ ] **Step 5: Commit**

```bash
git add scripts/cluster_articles.py docker-compose.override.yml
git commit -m "feat(clustering): wire embedding env and source fields into cluster_articles script"
```

---

## Task 11: Backfill NER script

**Files:**
- Create: `scripts/backfill_ner.py`

- [ ] **Step 1: Create `scripts/backfill_ner.py`**

```python
"""Backfill Claude Haiku NER for all articles not yet in ner_cache.

Usage:
  python scripts/backfill_ner.py
  python scripts/backfill_ner.py --dry-run --limit 10
"""
import argparse
import asyncio
import json
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.opensearch import INDEX_NEWS, get_os_client
from app.ingestion.ner_llm import extract_entities_llm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_SCROLL_SIZE = 100
_SCROLL_TTL = "5m"


async def _get_cached_slugs(session: AsyncSession) -> set[str]:
    result = await session.execute(text("SELECT slug FROM ner_cache"))
    return {row[0] for row in result.fetchall()}


async def main(dry_run: bool, limit: int | None) -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with AsyncSessionLocal() as session:
        cached_slugs = await _get_cached_slugs(session)
        logger.info("Already cached: %d articles", len(cached_slugs))

        os_client = get_os_client()
        resp = await os_client.search(
            index=INDEX_NEWS,
            body={
                "query": {"match_all": {}},
                "_source": ["slug", "title", "summary", "desc"],
                "size": _SCROLL_SIZE,
                "sort": [{"published_at": "asc"}],
            },
            scroll=_SCROLL_TTL,
        )
        scroll_id = resp["_scroll_id"]
        hits = resp["hits"]["hits"]

        processed = 0
        skipped = 0

        while hits:
            for hit in hits:
                src = hit["_source"]
                slug = src["slug"]

                if slug in cached_slugs:
                    skipped += 1
                    continue

                if limit and processed >= limit:
                    logger.info("Reached limit of %d", limit)
                    await os_client.clear_scroll(scroll_id=scroll_id)
                    return

                if dry_run:
                    logger.info("[DRY RUN] Would extract NER for: %s — %s", slug, src.get("title", ""))
                    processed += 1
                    continue

                entities = await extract_entities_llm(
                    slug=slug,
                    title=src.get("title", ""),
                    summary=src.get("summary") or src.get("desc") or "",
                    db_session=session,
                )
                logger.info("slug=%s entities=%d types=%s", slug, len(entities),
                            [e["type"] for e in entities])
                processed += 1

            resp = await os_client.scroll(scroll_id=scroll_id, scroll=_SCROLL_TTL)
            scroll_id = resp["_scroll_id"]
            hits = resp["hits"]["hits"]

        await os_client.clear_scroll(scroll_id=scroll_id)
        logger.info("Done. processed=%d skipped(cached)=%d", processed, skipped)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(main(args.dry_run, args.limit))
```

- [ ] **Step 2: Dry-run on 10 articles to verify NER quality**

```bash
docker compose exec ingestion python scripts/backfill_ner.py --dry-run --limit 10
```

Expected: logs 10 article slugs and titles, no exceptions.

- [ ] **Step 3: Run on a small real batch to check entity quality**

```bash
docker compose exec ingestion python scripts/backfill_ner.py --limit 20
```

Inspect the output. Confirm:
- `vuln_alias` fires for articles mentioning Log4Shell, Heartbleed, etc.
- `campaign` fires for MOVEit, SolarWinds, etc.
- `cve` fires for CVE identifiers in text that regex may have missed
- No obviously wrong extractions

- [ ] **Step 4: Run full backfill**

```bash
docker compose exec ingestion python scripts/backfill_ner.py
```

Expected: processes all articles, logs progress. Takes ~3–5 minutes for ~1000 articles.

- [ ] **Step 5: Verify cache coverage**

```bash
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT COUNT(*) FROM ner_cache;"
```

Expected: count close to total article count in OpenSearch.

- [ ] **Step 6: Commit**

```bash
git add scripts/backfill_ner.py
git commit -m "feat(clustering): add NER backfill script"
```

---

## Task 12: Backfill embeddings for cluster rebuild

**Files:**
- Create: `scripts/backfill_embeddings.py`

This script pre-computes article embeddings and stores them on the articles index. Used during cluster rebuild to avoid recomputing embeddings per-article in real-time (optional optimization — cluster_articles.py calls embed_text() anyway, so this script can be skipped if rebuild speed is acceptable).

- [ ] **Step 1: Create `scripts/backfill_embeddings.py`**

```python
"""Pre-compute and store article_embedding for all articles.

The cluster rebuild (cluster_articles.py --reset) calls embed_text() per article
anyway, so this script is an optional optimization to pre-warm the embedding field.

Usage:
  python scripts/backfill_embeddings.py
  python scripts/backfill_embeddings.py --limit 100
"""
import argparse
import asyncio
import logging

from app.db.opensearch import INDEX_NEWS, get_os_client
from app.ingestion.embedding_client import embed_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_SCROLL_SIZE = 256
_SCROLL_TTL = "5m"
_INSTRUCTION = "Represent this cybersecurity article for finding related articles: "


def _build_text(src: dict) -> str:
    title = src.get("title", "")
    snippet = src.get("summary") or src.get("desc") or ""
    return title + (". " + snippet[:400] if snippet else "")


async def main(limit: int | None) -> None:
    os_client = get_os_client()
    resp = await os_client.search(
        index=INDEX_NEWS,
        body={
            "query": {"bool": {"must_not": [{"exists": {"field": "article_embedding"}}]}},
            "_source": ["slug", "title", "summary", "desc"],
            "size": _SCROLL_SIZE,
        },
        scroll=_SCROLL_TTL,
    )
    scroll_id = resp["_scroll_id"]
    hits = resp["hits"]["hits"]
    total = 0

    while hits:
        texts = [_build_text(h["_source"]) for h in hits]
        slugs = [h["_source"]["slug"] for h in hits]

        embeddings = await embed_batch(texts)

        bulk_body = []
        for slug, emb in zip(slugs, embeddings):
            if emb is None:
                continue
            bulk_body.append({"update": {"_index": INDEX_NEWS, "_id": slug}})
            bulk_body.append({"doc": {"article_embedding": emb}})

        if bulk_body:
            await os_client.bulk(body=bulk_body, refresh=False)

        total += len([e for e in embeddings if e is not None])
        logger.info("Embedded %d articles (total so far: %d)", len(hits), total)

        if limit and total >= limit:
            break

        resp = await os_client.scroll(scroll_id=scroll_id, scroll=_SCROLL_TTL)
        scroll_id = resp["_scroll_id"]
        hits = resp["hits"]["hits"]

    await os_client.clear_scroll(scroll_id=scroll_id)
    logger.info("Done. Total embedded: %d", total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(main(args.limit))
```

- [ ] **Step 2: Run backfill**

```bash
docker compose exec ingestion python scripts/backfill_embeddings.py
```

Expected: processes articles in batches of 256, GPU-accelerated, completes in ~1 minute.

- [ ] **Step 3: Spot-check an article has the embedding field**

```bash
docker compose exec ingestion python -c "
import asyncio
from app.db.opensearch import INDEX_NEWS, get_os_client
async def check():
    c = get_os_client()
    r = await c.search(index=INDEX_NEWS, body={'query': {'exists': {'field': 'article_embedding'}}, 'size': 1})
    hits = r['hits']['hits']
    if hits:
        emb = hits[0]['_source'].get('article_embedding', [])
        print(f'dim={len(emb)} sample={emb[:3]}')
asyncio.run(check())
"
```

Expected: `dim=1024 sample=[<float>, <float>, <float>]`

- [ ] **Step 4: Commit**

```bash
git add scripts/backfill_embeddings.py
git commit -m "feat(clustering): add article embedding backfill script"
```

---

## Task 13: Cluster merge detection job

**Files:**
- Create: `scripts/detect_cluster_merges.py`

- [ ] **Step 1: Create `scripts/detect_cluster_merges.py`**

```python
"""Detect and merge duplicate clusters that formed before they could be linked.

Runs every 4 hours. Finds cluster pairs with overlapping event_signature fields,
scores them with the unified formula, and dissolves the smaller into the larger.

Usage:
  python scripts/detect_cluster_merges.py
  python scripts/detect_cluster_merges.py --dry-run
"""
import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from app.db.opensearch import INDEX_CLUSTERS, get_os_client
from app.ingestion.unified_scorer import MERGE_THRESHOLD, _compute_score

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_WINDOW_HOURS = 24
_CANDIDATE_SIZE = 10
_SOURCE_FIELDS = ["article_count", "article_ids", "entity_keys", "event_signature",
                  "centroid_embedding", "cve_ids", "seed_cve_ids", "timeline",
                  "max_cvss", "max_credibility_weight", "latest_at", "state", "label"]


async def _fetch_recently_updated(os_client, cutoff: str) -> list[dict]:
    resp = await os_client.search(
        index=INDEX_CLUSTERS,
        body={
            "query": {
                "bool": {
                    "must": [{"range": {"updated_at": {"gte": cutoff}}}],
                    "must_not": [{"term": {"state": "resolved"}}],
                }
            },
            "_source": _SOURCE_FIELDS,
            "size": 200,
        },
    )
    return resp["hits"]["hits"]


async def _find_overlapping_clusters(os_client, cluster: dict) -> list[dict]:
    sig = cluster["_source"].get("event_signature") or {}
    should = []
    for cve in sig.get("cve_ids") or []:
        should.append({"term": {"event_signature.cve_ids": cve}})
    for alias in sig.get("vuln_aliases") or []:
        should.append({"term": {"event_signature.vuln_aliases": alias}})
    for campaign in sig.get("campaign_names") or []:
        should.append({"term": {"event_signature.campaign_names": campaign}})

    if not should:
        return []

    resp = await os_client.search(
        index=INDEX_CLUSTERS,
        body={
            "query": {
                "bool": {
                    "should": should,
                    "minimum_should_match": 1,
                    "must_not": [
                        {"ids": {"values": [cluster["_id"]]}},
                        {"term": {"state": "resolved"}},
                    ],
                }
            },
            "_source": _SOURCE_FIELDS,
            "size": _CANDIDATE_SIZE,
        },
    )
    return resp["hits"]["hits"]


def _weighted_centroid(c1: dict, c2: dict) -> Optional[list[float]]:
    emb1 = c1["_source"].get("centroid_embedding")
    emb2 = c2["_source"].get("centroid_embedding")
    n1 = c1["_source"].get("article_count", 1)
    n2 = c2["_source"].get("article_count", 1)
    if not emb1 or not emb2:
        return emb1 or emb2
    total = n1 + n2
    centroid = (np.array(emb1) * n1 + np.array(emb2) * n2) / total
    return centroid.tolist()


async def _merge_clusters(os_client, survivor: dict, dissolved: dict, dry_run: bool) -> None:
    s_id = survivor["_id"]
    d_id = dissolved["_id"]
    s_src = survivor["_source"]
    d_src = dissolved["_source"]

    merged_article_ids = list(dict.fromkeys(
        (s_src.get("article_ids") or []) + (d_src.get("article_ids") or [])
    ))
    merged_entity_keys = list(dict.fromkeys(
        (s_src.get("entity_keys") or []) + (d_src.get("entity_keys") or [])
    ))
    merged_cve_ids = list(dict.fromkeys(
        (s_src.get("cve_ids") or []) + (d_src.get("cve_ids") or [])
    ))
    merged_timeline = (s_src.get("timeline") or []) + (d_src.get("timeline") or [])
    new_centroid = _weighted_centroid(survivor, dissolved)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    logger.info(
        "%sMerging cluster %s (%d articles) into %s (%d articles)",
        "[DRY RUN] " if dry_run else "",
        d_id, d_src.get("article_count", 0),
        s_id, s_src.get("article_count", 0),
    )

    if dry_run:
        return

    await os_client.update(
        index=INDEX_CLUSTERS,
        id=s_id,
        body={"doc": {
            "article_ids": merged_article_ids,
            "article_count": len(merged_article_ids),
            "entity_keys": merged_entity_keys,
            "cve_ids": merged_cve_ids,
            "timeline": merged_timeline,
            "centroid_embedding": new_centroid,
            "max_cvss": max(s_src.get("max_cvss", 0), d_src.get("max_cvss", 0)),
            "max_credibility_weight": max(
                s_src.get("max_credibility_weight", 1.0),
                d_src.get("max_credibility_weight", 1.0),
            ),
            "updated_at": now,
        }},
    )

    await os_client.update(
        index=INDEX_CLUSTERS,
        id=d_id,
        body={"doc": {"state": "resolved", "merged_into": s_id, "updated_at": now}},
    )

    # Re-point dissolved cluster's articles to survivor
    for article_id in d_src.get("article_ids") or []:
        from app.db.opensearch import INDEX_NEWS
        try:
            await os_client.update(
                index=INDEX_NEWS,
                id=article_id,
                body={"doc": {"cluster_id": s_id}},
                retry_on_conflict=3,
            )
        except Exception as exc:
            logger.warning("Could not re-point article %s: %s", article_id, exc)


async def main(dry_run: bool) -> None:
    os_client = get_os_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=_WINDOW_HOURS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    recent = await _fetch_recently_updated(os_client, cutoff)
    logger.info("Checking %d recently updated clusters for merge candidates", len(recent))

    seen_pairs: set[frozenset] = set()
    merge_count = 0

    for cluster in recent:
        candidates = await _find_overlapping_clusters(os_client, cluster)
        for candidate in candidates:
            pair = frozenset([cluster["_id"], candidate["_id"]])
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            # Score both directions, use max
            score_a = _compute_score(
                [{"type": "cve", "normalized_key": k} for k in
                 (cluster["_source"].get("event_signature") or {}).get("cve_ids") or []],
                candidate["_source"],
                cluster["_source"].get("centroid_embedding"),
            )
            score_b = _compute_score(
                [{"type": "cve", "normalized_key": k} for k in
                 (candidate["_source"].get("event_signature") or {}).get("cve_ids") or []],
                cluster["_source"],
                candidate["_source"].get("centroid_embedding"),
            )
            score = max(score_a, score_b)

            if score >= MERGE_THRESHOLD:
                # Survivor = larger cluster
                if (cluster["_source"].get("article_count", 0) >=
                        candidate["_source"].get("article_count", 0)):
                    survivor, dissolved = cluster, candidate
                else:
                    survivor, dissolved = candidate, cluster

                await _merge_clusters(os_client, survivor, dissolved, dry_run)
                merge_count += 1

    logger.info("Done. Merges performed: %d", merge_count)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
```

- [ ] **Step 2: Commit**

```bash
git add scripts/detect_cluster_merges.py
git commit -m "feat(clustering): add cluster merge detection job"
```

---

## Task 14: End-to-end backfill and rebuild

- [ ] **Step 1: Rebuild clusters with the new unified scorer**

```bash
docker compose exec ingestion python scripts/cluster_articles.py --reset
```

This deletes all existing cluster documents and re-clusters all articles using the new unified scorer. Monitor progress in logs. Expected faster than the previous ~13 min baseline.

- [ ] **Step 2: Check cluster count and state distribution**

```bash
docker compose exec ingestion python -c "
import asyncio
from app.db.opensearch import INDEX_CLUSTERS, get_os_client
async def stats():
    c = get_os_client()
    r = await c.search(index=INDEX_CLUSTERS, body={
        'aggs': {
            'states': {'terms': {'field': 'state'}},
            'confidence': {'terms': {'field': 'event_signature.confidence'}},
        },
        'size': 0
    })
    print('States:', r['aggregations']['states']['buckets'])
    print('Confidence:', r['aggregations']['confidence']['buckets'])
    total = r['hits']['total']['value']
    print('Total clusters:', total)
asyncio.run(stats())
"
```

Sanity checks:
- Total cluster count should be LOWER than before (fewer false-split duplicates)
- Should see `high` and `medium` confidence clusters (not all `low`)
- `confirmed` state clusters should exist (articles successfully grouped)

- [ ] **Step 3: Spot-check known problem cases**

Run this query for each known problem case:

```bash
docker compose exec ingestion python -c "
import asyncio
from app.db.opensearch import INDEX_CLUSTERS, get_os_client
async def check(label_term):
    c = get_os_client()
    r = await c.search(index=INDEX_CLUSTERS, body={
        'query': {'match': {'label': label_term}},
        '_source': ['label', 'article_count', 'state', 'event_signature'],
        'size': 5
    })
    for h in r['hits']['hits']:
        print(h['_id'], h['_source'])
asyncio.run(check('Log4Shell'))  # Should see 1 cluster with vuln_alias=log4shell
"
```

Verify:
- Log4Shell articles are in ONE cluster with `event_signature.vuln_aliases: ['log4shell']`
- CISA advisories are in SEPARATE clusters (not one giant blob)
- Newsletter roundups are NOT merged with single-CVE incident articles

- [ ] **Step 4: Run merge detection dry-run**

```bash
docker compose exec ingestion python scripts/detect_cluster_merges.py --dry-run
```

Expected: lists any candidate pairs above the 0.55 threshold. Review them to confirm they look like genuine duplicates (same event, different cluster).

- [ ] **Step 5: Run merge detection for real if dry-run looks good**

```bash
docker compose exec ingestion python scripts/detect_cluster_merges.py
```

- [ ] **Step 6: Final commit**

```bash
git add .
git commit -m "feat(clustering): complete clustering redesign — NER + embeddings + unified scorer"
```

---

## Self-Review

**Spec coverage check:**
- ✅ LLM NER with Claude Haiku, cached in Postgres → Task 6
- ✅ New entity types `vuln_alias`, `campaign` → Task 6 (`ner_llm.py` system prompt + tool schema)
- ✅ `BAAI/bge-large-en-v1.5` GPU embedding service → Task 4
- ✅ OpenSearch k-NN on clusters index → Task 3
- ✅ `event_signature` on cluster documents → Task 3 (mapping) + Task 9 (clusterer)
- ✅ `centroid_embedding` with running-average update → Task 9 (`_updated_centroid`)
- ✅ Unified scorer: `0.45·CVE + 0.25·alias + 0.15·entity_jaccard + 0.15·cosine` → Task 8
- ✅ Assignment threshold 0.30, merge threshold 0.55 → Task 8 (`unified_scorer.py`)
- ✅ Candidate retrieval: structured terms (14d) + k-NN (72h) → Task 8 (`_get_candidates`)
- ✅ Merge detection job → Task 13
- ✅ Merge: dissolve smaller cluster, tombstone `merged_into` field → Task 13
- ✅ Entity extractor integrates LLM NER as primary → Task 7
- ✅ Feature branch `feat/clustering-redesign` → Task 1
- ✅ Backfill NER for existing articles → Task 11
- ✅ Backfill embeddings → Task 12
- ✅ End-to-end rebuild + verification → Task 14
- ✅ MLT functions retired → Task 9 (clusterer rewrite replaces them)
- ✅ `seed_cve_ids` preserved — merge does NOT update it → Task 9 (Painless script)

**Type consistency check:**
- `embed_text()` → `list[float] | None` — used consistently in clusterer.py and backfill_embeddings.py ✅
- `embed_batch()` → `list[list[float] | None]` — used in backfill_embeddings.py ✅
- `extract_entities_llm()` → `list[dict]` — consistent in entity_extractor.py and backfill_ner.py ✅
- `find_best_cluster()` → `str | None` — used in cluster_article() ✅
- `_compute_score()` signature: `(list[dict], dict, Optional[list[float]]) → float` — used in unified_scorer.py and detect_cluster_merges.py ✅
- `MERGE_THRESHOLD` imported from `unified_scorer` in detect_cluster_merges.py ✅
