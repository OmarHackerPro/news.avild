# Local NER Replacement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Claude Haiku NER in the ingestion hot path with `attack-vector/SecureModernBERT-NER` running as a CPU-only sidecar service. Keep Haiku in tree as a backfill/eval tool. Ship eval harness with adjudication UI and an explicit cutover script.

**Architecture:** New `ner` Docker Compose service with its own `Dockerfile.ner`, FastAPI app serving `POST /extract` and `GET /health`. Ingestion calls it via httpx through a new `app/ingestion/ner_client.py`. `ner_cache` gets a `model_version` column so old Haiku rows and new local rows coexist. `vuln_alias` (the one type the local model doesn't emit) is handled via a curated regex list seeded from `ner_cache` + a hand-curated canonical list, plugged into the existing keyword-loader path in `entity_extractor.py`.

**Tech Stack:** Python 3.12, FastAPI, asyncio, httpx, transformers (HuggingFace), torch (CPU wheel), SQLAlchemy async, Alembic, Docker Compose, OpenSearch DSL (existing), opensearch-py.

**Spec:** [docs/superpowers/specs/2026-05-14-local-ner-replacement-design.md](../specs/2026-05-14-local-ner-replacement-design.md)

---

## Task 1: Alembic migration — `ner_cache.model_version`

**Files:**
- Create: `alembic/versions/a8b9c0d1e2f3_ner_cache_model_version.py`

- [ ] **Step 1: Write the migration**

```python
"""ner_cache add model_version column and composite primary key

Revision ID: a8b9c0d1e2f3
Revises: f7b8c9d0e1f2
Create Date: 2026-05-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "f7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add nullable, backfill, then enforce NOT NULL and switch PK
    op.add_column("ner_cache", sa.Column("model_version", sa.Text(), nullable=True))
    op.execute("UPDATE ner_cache SET model_version = 'haiku-4-5' WHERE model_version IS NULL")
    op.alter_column("ner_cache", "model_version", nullable=False)
    op.drop_constraint("ner_cache_pkey", "ner_cache", type_="primary")
    op.create_primary_key("ner_cache_pkey", "ner_cache", ["slug", "model_version"])


def downgrade() -> None:
    op.drop_constraint("ner_cache_pkey", "ner_cache", type_="primary")
    op.create_primary_key("ner_cache_pkey", "ner_cache", ["slug"])
    op.drop_column("ner_cache", "model_version")
```

- [ ] **Step 2: Run the migration locally to confirm syntax**

Run: `docker compose run --rm backend alembic upgrade head`
Expected: migration applies with no error. Confirm via `docker compose exec db psql -U postgres -d avild_news -c "\d ner_cache"` showing composite PK.

- [ ] **Step 3: Commit**

```bash
git add alembic/versions/a8b9c0d1e2f3_ner_cache_model_version.py
git commit -m "feat(ner): add model_version column to ner_cache"
```

---

## Task 2: Alembic migration — `ner_eval_judgments`

**Files:**
- Create: `alembic/versions/b9c0d1e2f3a4_ner_eval_judgments.py`

- [ ] **Step 1: Write the migration**

```python
"""create ner_eval_judgments table

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-05-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9c0d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ner_eval_judgments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_normalized_key", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("input_zone", sa.Text(), nullable=True),
        sa.Column("verdict", sa.Text(), nullable=True),
        sa.Column("judged_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("source IN ('haiku', 'local', 'both')", name="ck_source"),
        sa.CheckConstraint("input_zone IN ('shared', 'new-input') OR input_zone IS NULL", name="ck_input_zone"),
        sa.CheckConstraint("verdict IN ('correct', 'wrong', 'skip') OR verdict IS NULL", name="ck_verdict"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", "entity_type", "entity_normalized_key", "source", name="uq_ner_eval_judgment"),
    )
    op.create_index("ix_ner_eval_judgments_slug", "ner_eval_judgments", ["slug"])
    op.create_index("ix_ner_eval_judgments_unjudged", "ner_eval_judgments", ["verdict"], postgresql_where=sa.text("verdict IS NULL"))


def downgrade() -> None:
    op.drop_index("ix_ner_eval_judgments_unjudged", table_name="ner_eval_judgments")
    op.drop_index("ix_ner_eval_judgments_slug", table_name="ner_eval_judgments")
    op.drop_table("ner_eval_judgments")
```

- [ ] **Step 2: Apply and verify**

Run: `docker compose run --rm backend alembic upgrade head`
Expected: `ner_eval_judgments` table exists. Verify with `\d ner_eval_judgments`.

- [ ] **Step 3: Commit**

```bash
git add alembic/versions/b9c0d1e2f3a4_ner_eval_judgments.py
git commit -m "feat(ner): add ner_eval_judgments table"
```

---

## Task 3: SQLAlchemy model for `ner_eval_judgments`

**Files:**
- Create: `app/db/models/ner_eval_judgment.py`
- Modify: `app/db/models/__init__.py` (export the new model)

- [ ] **Step 1: Create the model**

`app/db/models/ner_eval_judgment.py`:

```python
"""SQLAlchemy model for ner_eval_judgments."""
from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NerEvalJudgment(Base):
    __tablename__ = "ner_eval_judgments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_normalized_key: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # haiku | local | both
    input_zone: Mapped[str | None] = mapped_column(Text, nullable=True)  # shared | new-input | NULL
    verdict: Mapped[str | None] = mapped_column(Text, nullable=True)  # correct | wrong | skip | NULL
    judged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 2: Export from package init**

Append to `app/db/models/__init__.py`:

```python
from app.db.models.ner_eval_judgment import NerEvalJudgment  # noqa: F401
```

- [ ] **Step 3: Commit**

```bash
git add app/db/models/ner_eval_judgment.py app/db/models/__init__.py
git commit -m "feat(ner): add NerEvalJudgment ORM model"
```

---

## Task 4: Settings — `NER_ACTIVE_MODEL`, `NER_SIDECAR_URL`

**Files:**
- Modify: `app/core/config.py`

- [ ] **Step 1: Add settings to `app/core/config.py`**

Append these lines inside the `Settings` class (after `OPENSEARCH_PASSWORD`):

```python
    # Local NER sidecar
    NER_SIDECAR_URL: str = os.getenv("NER_SIDECAR_URL", "http://ner:8001")
    NER_ACTIVE_MODEL: str = os.getenv("NER_ACTIVE_MODEL", "haiku-4-5")
    NER_REQUEST_TIMEOUT_S: float = float(os.getenv("NER_REQUEST_TIMEOUT_S", "30"))
```

- [ ] **Step 2: Commit**

```bash
git add app/core/config.py
git commit -m "feat(ner): add NER_ACTIVE_MODEL and sidecar settings"
```

---

## Task 5: Sidecar requirements file

**Files:**
- Create: `requirements.ner.txt`

- [ ] **Step 1: Write requirements**

```
--extra-index-url https://download.pytorch.org/whl/cpu
torch==2.4.1+cpu
transformers==4.44.2
fastapi==0.115.0
uvicorn[standard]==0.30.6
pydantic==2.9.0
huggingface_hub==0.25.0
```

(Pin the SecureModernBERT-NER commit SHA at implementation time using `huggingface-cli download attack-vector/SecureModernBERT-NER --revision <sha>`. Use the model card's "Latest commit" link from HuggingFace.)

- [ ] **Step 2: Commit**

```bash
git add requirements.ner.txt
git commit -m "feat(ner): add sidecar requirements"
```

---

## Task 6: `Dockerfile.ner`

**Files:**
- Create: `Dockerfile.ner`

- [ ] **Step 1: Write Dockerfile**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.ner.txt .
RUN pip install --no-cache-dir -r requirements.ner.txt

# Bake model into image at build time for reproducibility & offline cold-start
ARG NER_MODEL_ID=attack-vector/SecureModernBERT-NER
ARG NER_MODEL_REVISION=main
ENV HF_HOME=/app/.hf_cache
RUN python -c "from huggingface_hub import snapshot_download; \
    snapshot_download('${NER_MODEL_ID}', revision='${NER_MODEL_REVISION}', cache_dir='/app/.hf_cache')"

# App code
COPY app/services/ner_sidecar/ /app/app/services/ner_sidecar/

# Sidecar listens on 8001 internally
ENV PORT=8001
EXPOSE 8001

CMD ["uvicorn", "app.services.ner_sidecar.main:app", "--host", "0.0.0.0", "--port", "8001"]
```

- [ ] **Step 2: Commit**

```bash
git add Dockerfile.ner
git commit -m "feat(ner): add Dockerfile for sidecar service"
```

---

## Task 7: Sidecar model wrapper with asyncio lock

**Files:**
- Create: `app/services/__init__.py` (empty)
- Create: `app/services/ner_sidecar/__init__.py` (empty)
- Create: `app/services/ner_sidecar/model.py`

- [ ] **Step 1: Create the model wrapper**

`app/services/ner_sidecar/model.py`:

```python
"""SecureModernBERT-NER wrapper with serialized inference.

Loads the model once at startup. Inference is serialized via an asyncio.Lock
because multiple concurrent ingester coroutines may call /extract.
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import torch
from transformers import AutoModelForTokenClassification, AutoTokenizer

logger = logging.getLogger(__name__)

# Map model labels to internal entity types. Labels not in this dict are dropped.
LABEL_MAP: dict[str, str] = {
    "PRODUCT": "product",
    "MALWARE": "malware",
    "THREAT-ACTOR": "actor",
    "TOOL": "tool",
    "CAMPAIGN": "campaign",
    "CVE": "cve",
}

MODEL_ID = os.getenv("NER_MODEL_ID", "attack-vector/SecureModernBERT-NER")
MODEL_REVISION = os.getenv("NER_MODEL_REVISION", "main")
MAX_TOKENS = int(os.getenv("NER_MAX_TOKENS", "4096"))
CONFIDENCE_THRESHOLD = float(os.getenv("NER_CONFIDENCE_THRESHOLD", "0.5"))
MODEL_VERSION = os.getenv("NER_MODEL_VERSION", "securebert-v1")


@dataclass
class ExtractedEntity:
    type: str
    name: str
    score: float
    char_offset: int  # start position in original input text


class NerModel:
    """Singleton model wrapper. Use NerModel.get() after load()."""

    _instance: Optional["NerModel"] = None

    def __init__(self) -> None:
        self.tokenizer = None
        self.model = None
        self._lock = asyncio.Lock()
        self._device = torch.device("cpu")  # CPU-only per spec
        self._id2label: dict[int, str] = {}

    @classmethod
    async def load(cls) -> "NerModel":
        if cls._instance is not None:
            return cls._instance
        inst = cls()
        start = time.perf_counter()
        logger.info("Loading NER model %s@%s on CPU", MODEL_ID, MODEL_REVISION)
        inst.tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=MODEL_REVISION)
        inst.model = AutoModelForTokenClassification.from_pretrained(MODEL_ID, revision=MODEL_REVISION)
        inst.model.to(inst._device)
        inst.model.eval()
        inst._id2label = inst.model.config.id2label
        logger.info("Loaded NER model in %.2fs", time.perf_counter() - start)
        cls._instance = inst
        return inst

    @classmethod
    def get(cls) -> "NerModel":
        if cls._instance is None:
            raise RuntimeError("NerModel.load() not called before get()")
        return cls._instance

    async def extract(self, text: str) -> list[ExtractedEntity]:
        """Run NER over text and return entities mapped to internal types.

        Serialized via self._lock so concurrent callers do not interleave through
        the same torch model object.
        """
        if not text or not text.strip():
            return []

        async with self._lock:
            return await asyncio.get_running_loop().run_in_executor(
                None, self._extract_sync, text
            )

    def _extract_sync(self, text: str) -> list[ExtractedEntity]:
        enc = self.tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=MAX_TOKENS,
            return_offsets_mapping=True,
        )
        offsets = enc.pop("offset_mapping")[0].tolist()
        with torch.no_grad():
            logits = self.model(**enc).logits[0]
        probs = torch.softmax(logits, dim=-1)
        scores, label_ids = probs.max(dim=-1)
        scores = scores.tolist()
        label_ids = label_ids.tolist()
        labels = [self._id2label[i] for i in label_ids]

        return self._merge_bio(labels, scores, offsets, text)

    def _merge_bio(
        self,
        labels: list[str],
        scores: list[float],
        offsets: list[list[int]],
        text: str,
    ) -> list[ExtractedEntity]:
        """Merge BIO-tagged tokens into entities, applying threshold and label map."""
        out: list[ExtractedEntity] = []
        cur_type: Optional[str] = None
        cur_start: Optional[int] = None
        cur_end: int = 0
        cur_scores: list[float] = []

        def flush() -> None:
            nonlocal cur_type, cur_start, cur_end, cur_scores
            if cur_type is None or cur_start is None:
                cur_type = cur_start = None
                cur_scores = []
                return
            avg_score = sum(cur_scores) / len(cur_scores)
            if avg_score >= CONFIDENCE_THRESHOLD:
                span_text = text[cur_start:cur_end].strip()
                if span_text:
                    out.append(ExtractedEntity(
                        type=LABEL_MAP[cur_type],
                        name=span_text,
                        score=avg_score,
                        char_offset=cur_start,
                    ))
            cur_type = cur_start = None
            cur_scores = []

        for label, score, (start, end) in zip(labels, scores, offsets):
            if start == 0 and end == 0:
                # special tokens (CLS, SEP, PAD)
                flush()
                continue

            if label == "O" or label is None:
                flush()
                continue

            # Expect labels like "B-MALWARE", "I-MALWARE"; tolerate plain "MALWARE"
            tag, _, raw_type = label.partition("-")
            if not raw_type:
                raw_type = tag
                tag = "B"

            if raw_type not in LABEL_MAP:
                flush()
                continue

            if tag == "B" or cur_type != raw_type:
                flush()
                cur_type = raw_type
                cur_start = start
                cur_end = end
                cur_scores = [score]
            else:  # tag == "I" and cur_type == raw_type
                cur_end = end
                cur_scores.append(score)

        flush()
        return out
```

- [ ] **Step 2: Commit**

```bash
git add app/services/__init__.py app/services/ner_sidecar/__init__.py app/services/ner_sidecar/model.py
git commit -m "feat(ner): sidecar NER model wrapper with BIO merge and lock"
```

---

## Task 8: Sidecar FastAPI app

**Files:**
- Create: `app/services/ner_sidecar/main.py`

- [ ] **Step 1: Create the FastAPI app**

`app/services/ner_sidecar/main.py`:

```python
"""NER sidecar FastAPI app — single-purpose entity extraction service."""
import logging
import re
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.services.ner_sidecar.model import MODEL_VERSION, NerModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


class ExtractRequest(BaseModel):
    slug: str
    title: str
    body: str


class ExtractedEntityResponse(BaseModel):
    type: str
    name: str
    normalized_key: str
    score: float
    char_offset: int


class ExtractResponse(BaseModel):
    entities: list[ExtractedEntityResponse]
    model_version: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await NerModel.load()
    except Exception:
        logger.exception("Fatal: NER model failed to load. Exiting.")
        sys.exit(1)  # loud failure — container restarts and operator notices
    yield


app = FastAPI(lifespan=lifespan, title="kiber-ner")


def _normalize_key(name: str) -> str:
    """Convert entity name to slug form (mirrors entity_extractor._normalize_key)."""
    key = name.lower()
    key = re.sub(r"[^a-z0-9]+", "-", key)
    return key.strip("-")


@app.get("/health")
async def health() -> dict[str, str]:
    if NerModel._instance is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    return {"status": "ok", "model_version": MODEL_VERSION}


@app.post("/extract", response_model=ExtractResponse)
async def extract(req: ExtractRequest) -> ExtractResponse:
    model = NerModel.get()
    text = f"{req.title}\n\n{req.body or ''}"
    started = time.perf_counter()
    raw = await model.extract(text)
    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "extract slug=%s entities=%d latency_ms=%d",
        req.slug, len(raw), latency_ms,
    )
    return ExtractResponse(
        entities=[
            ExtractedEntityResponse(
                type=e.type,
                name=e.name,
                normalized_key=_normalize_key(e.name),
                score=e.score,
                # offset is relative to title+body concat; subtract title prefix for body-only offset
                char_offset=max(0, e.char_offset - (len(req.title) + 2)),
            )
            for e in raw
        ],
        model_version=MODEL_VERSION,
    )
```

- [ ] **Step 2: Commit**

```bash
git add app/services/ner_sidecar/main.py
git commit -m "feat(ner): sidecar FastAPI app with /health and /extract"
```

---

## Task 9: Add `ner` service to `docker-compose.yml`

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Insert the new service block after `briefing:` (and before `db:`)**

```yaml
  ner:
    build:
      context: .
      dockerfile: Dockerfile.ner
    restart: unless-stopped
    environment:
      NER_MODEL_ID: attack-vector/SecureModernBERT-NER
      NER_MODEL_REVISION: ${NER_MODEL_REVISION:-main}
      NER_MAX_TOKENS: "4096"
      NER_CONFIDENCE_THRESHOLD: "0.5"
      NER_MODEL_VERSION: securebert-v1
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8001/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 90s
    expose:
      - "8001"
```

Also add `NER_SIDECAR_URL: ${NER_SIDECAR_URL:-http://ner:8001}` and `NER_ACTIVE_MODEL: ${NER_ACTIVE_MODEL:-haiku-4-5}` to the `ingestion`, `backend`, and `briefing` service `environment` blocks.

Add `ner` to the `depends_on` list of the `ingestion` service.

- [ ] **Step 2: Build and bring up the sidecar to verify it boots**

Run: `docker compose build ner && docker compose up -d ner`
Wait ~60s. Then: `curl -f http://localhost/api/ner-debug-not-exposed || docker compose exec ner curl -f http://localhost:8001/health`
Expected: `{"status":"ok","model_version":"securebert-v1"}`.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(ner): add ner sidecar service to docker-compose"
```

---

## Task 10: NER client module (ingestion side)

**Files:**
- Create: `app/ingestion/ner_client.py`
- Create: `tests/test_ner_client.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_ner_client.py`:

```python
"""Tests for app.ingestion.ner_client."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.ner_client import extract_entities_local


@pytest.mark.asyncio
async def test_extract_returns_normalized_entities_on_cache_miss():
    fake_response = {
        "entities": [
            {"type": "malware", "name": "LockBit 3.0", "normalized_key": "lockbit-3-0", "score": 0.92, "char_offset": 42},
            {"type": "actor", "name": "Lazarus Group", "normalized_key": "lazarus-group", "score": 0.87, "char_offset": 110},
        ],
        "model_version": "securebert-v1",
    }

    mock_session = AsyncMock()
    miss = MagicMock()
    miss.fetchone.return_value = None
    mock_session.execute = AsyncMock(return_value=miss)

    mock_http = AsyncMock()
    http_resp = MagicMock()
    http_resp.raise_for_status = MagicMock()
    http_resp.json = MagicMock(return_value=fake_response)
    mock_http.post = AsyncMock(return_value=http_resp)

    with patch("app.ingestion.ner_client._get_http", return_value=mock_http):
        result = await extract_entities_local(
            slug="test-1",
            title="LockBit hits hospital",
            body="Lazarus Group affiliate deployed LockBit 3.0 in attack.",
            db_session=mock_session,
        )

    assert len(result) == 2
    assert result[0]["type"] == "malware"
    assert result[0]["normalized_key"] == "lockbit-3-0"
    # Cached: one SELECT + one INSERT
    assert mock_session.execute.call_count == 2


@pytest.mark.asyncio
async def test_extract_returns_cache_hit_without_http():
    cached_json = [
        {"type": "tool", "name": "Cobalt Strike", "normalized_key": "cobalt-strike", "score": 0.99, "char_offset": 5}
    ]
    mock_session = AsyncMock()
    hit = MagicMock()
    hit.fetchone.return_value = (cached_json,)
    mock_session.execute = AsyncMock(return_value=hit)

    mock_http = AsyncMock()
    mock_http.post = AsyncMock()

    with patch("app.ingestion.ner_client._get_http", return_value=mock_http):
        result = await extract_entities_local(
            slug="cache-hit",
            title="Cobalt Strike beacon",
            body="Discovered Cobalt Strike beacon.",
            db_session=mock_session,
        )

    mock_http.post.assert_not_called()
    assert result == cached_json


@pytest.mark.asyncio
async def test_extract_returns_empty_on_http_failure_and_does_not_cache():
    mock_session = AsyncMock()
    miss = MagicMock()
    miss.fetchone.return_value = None
    mock_session.execute = AsyncMock(return_value=miss)

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(side_effect=Exception("boom"))

    with patch("app.ingestion.ner_client._get_http", return_value=mock_http):
        result = await extract_entities_local(
            slug="fail-1",
            title="x",
            body="y",
            db_session=mock_session,
        )

    assert result == []
    # Only SELECT, no INSERT
    assert mock_session.execute.call_count == 1


@pytest.mark.asyncio
async def test_extract_skips_cache_when_db_session_none():
    fake_response = {"entities": [], "model_version": "securebert-v1"}
    mock_http = AsyncMock()
    http_resp = MagicMock()
    http_resp.raise_for_status = MagicMock()
    http_resp.json = MagicMock(return_value=fake_response)
    mock_http.post = AsyncMock(return_value=http_resp)

    with patch("app.ingestion.ner_client._get_http", return_value=mock_http):
        result = await extract_entities_local(
            slug="no-cache",
            title="t",
            body="b",
            db_session=None,
        )

    assert result == []
    mock_http.post.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ner_client.py -v`
Expected: ImportError (module doesn't exist yet).

- [ ] **Step 3: Implement `app/ingestion/ner_client.py`**

```python
"""HTTP client wrapper around the local NER sidecar.

Caches results in Postgres ner_cache keyed by (slug, model_version). On HTTP
failure returns [] and does not write to cache.

Same input/output shape as ner_llm.extract_entities_llm to minimize churn in
entity_extractor.extract_entities().
"""
import json
import logging
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)

_http_client: httpx.AsyncClient | None = None


def _get_http() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=settings.NER_SIDECAR_URL,
            timeout=httpx.Timeout(settings.NER_REQUEST_TIMEOUT_S),
        )
    return _http_client


async def _get_cached(
    slug: str, model_version: str, session: AsyncSession
) -> Optional[list[dict]]:
    result = await session.execute(
        text(
            "SELECT entities_json FROM ner_cache "
            "WHERE slug = :slug AND model_version = :version"
        ),
        {"slug": slug, "version": model_version},
    )
    row = result.fetchone()
    return row[0] if row else None


async def _write_cache(
    slug: str, model_version: str, entities: list[dict], session: AsyncSession
) -> None:
    await session.execute(
        text(
            "INSERT INTO ner_cache (slug, model_version, entities_json, extracted_at) "
            "VALUES (:slug, :version, :entities, NOW()) "
            "ON CONFLICT (slug, model_version) DO NOTHING"
        ),
        {"slug": slug, "version": model_version, "entities": json.dumps(entities)},
    )
    await session.commit()


async def extract_entities_local(
    slug: str,
    title: str,
    body: str,
    db_session: Optional[AsyncSession],
) -> list[dict]:
    """Extract entities via the local NER sidecar.

    Cache key: (slug, NER_ACTIVE_MODEL). Failures return [] and do not cache.
    """
    model_version = settings.NER_ACTIVE_MODEL
    if db_session is not None:
        cached = await _get_cached(slug, model_version, db_session)
        if cached is not None:
            return cached

    entities: list[dict] = []
    try:
        resp = await _get_http().post(
            "/extract",
            json={"slug": slug, "title": title, "body": body or ""},
        )
        resp.raise_for_status()
        data = resp.json()
        entities = data.get("entities", []) or []
        if db_session is not None:
            await _write_cache(slug, model_version, entities, db_session)
    except Exception as exc:
        logger.warning("Local NER failed for slug=%s: %s", slug, exc)

    return entities
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ner_client.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/ner_client.py tests/test_ner_client.py
git commit -m "feat(ner): add ner_client HTTP wrapper with versioned cache"
```

---

## Task 11: Swap `entity_extractor.py` to call `ner_client`

**Files:**
- Modify: `app/ingestion/entity_extractor.py` (lines 373-401 — the `extract_entities` function)
- Modify: `app/ingestion/ner_llm.py` (add module docstring marking it backfill-only)
- Modify: `tests/test_entity_extractor.py` (any mock of `extract_entities_llm` updated to `extract_entities_local`)

- [ ] **Step 1: Update `app/ingestion/entity_extractor.py`**

Replace the body of `extract_entities` (currently around line 373):

```python
async def extract_entities(
    article: NormalizedArticle,
    *,
    slug: str | None = None,
    db_session=None,
) -> list[dict]:
    """Extract entities from article. Local NER runs first if slug is provided; regex fills gaps."""
    model_entities: list[dict] = []
    if slug:
        from app.ingestion.ner_client import extract_entities_local
        body = article.get("content_html") or article.get("summary") or article.get("desc") or ""
        if body:
            body = strip_html(body)
        model_entities = await extract_entities_local(
            slug=slug,
            title=article.get("title") or "",
            body=body,
            db_session=db_session,
        )

    regex_entities = _extract_regex(article)

    seen_keys = {e["normalized_key"] for e in model_entities}
    merged = list(model_entities)
    for e in regex_entities:
        key = e["normalized_key"]
        # suppress if exact match OR if model already has a more-specific variant
        if key not in seen_keys and not any(k.startswith(key + "-") for k in seen_keys):
            merged.append(e)
            seen_keys.add(key)
    return merged
```

- [ ] **Step 2: Mark `ner_llm.py` as backfill-only**

Replace the module docstring at the top of `app/ingestion/ner_llm.py` with:

```python
"""Claude Haiku NER — BACKFILL / EVAL ONLY as of 2026-05-14.

The hot ingestion path now uses app.ingestion.ner_client (local sidecar).
This module is preserved for one-off backfills, A/B comparisons, and the
ner_eval_judgments harness. Do not call from production ingestion code.

Caches results in Postgres ner_cache table under model_version='haiku-4-5'.
Pass db_session=None to skip cache (useful for unit tests and one-off calls).
"""
```

Also update `_write_cache` in `ner_llm.py` to write under `model_version='haiku-4-5'` so its cache rows match the new schema. Replace the function body:

```python
async def _write_cache(slug: str, entities: list[dict], session: AsyncSession) -> None:
    await session.execute(
        text(
            "INSERT INTO ner_cache (slug, model_version, entities_json, extracted_at) "
            "VALUES (:slug, 'haiku-4-5', :entities, NOW()) "
            "ON CONFLICT (slug, model_version) DO NOTHING"
        ),
        {"slug": slug, "entities": json.dumps(entities)},
    )
    await session.commit()
```

And update `_get_cached` similarly:

```python
async def _get_cached(slug: str, session: AsyncSession) -> Optional[list[dict]]:
    result = await session.execute(
        text(
            "SELECT entities_json FROM ner_cache "
            "WHERE slug = :slug AND model_version = 'haiku-4-5'"
        ),
        {"slug": slug},
    )
    row = result.fetchone()
    return row[0] if row else None
```

- [ ] **Step 3: Update tests**

In `tests/test_ner_llm.py` only — the call_count assertions need updating because the SQL now includes the model_version filter but is still one SELECT + one INSERT. No code change needed in test assertions; verify by running.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_entity_extractor.py tests/test_ner_llm.py tests/test_ner_client.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/entity_extractor.py app/ingestion/ner_llm.py
git commit -m "feat(ner): route hot path through local sidecar; keep Haiku for backfill"
```

---

## Task 12: vuln_alias canonical list + seed script

**Files:**
- Create: `scripts/seed_vuln_aliases.py`

- [ ] **Step 1: Write the seed script**

```python
"""Seed vuln_alias entries into data/threat_keywords.json.

Union of:
1. All vuln_alias entities Haiku has stored in ner_cache (filtered to plausible values).
2. A hand-curated canonical list of famous named vulnerabilities.

Output: writes a new vuln_alias section into the keywords map. Existing
non-vuln_alias entries are preserved untouched. Existing vuln_alias entries
(if any) are merged by normalized_key, with the canonical list taking
precedence on display-name conflicts.
"""
import asyncio
import json
import re
from pathlib import Path

from sqlalchemy import text

from app.db.session import AsyncSessionLocal

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "threat_keywords.json"

CANONICAL_VULN_ALIASES: dict[str, str] = {
    "log4shell": "Log4Shell",
    "printnightmare": "PrintNightmare",
    "heartbleed": "Heartbleed",
    "citrixbleed": "CitrixBleed",
    "citrixbleed-2": "CitrixBleed 2",
    "spectre": "Spectre",
    "meltdown": "Meltdown",
    "bluekeep": "BlueKeep",
    "eternalblue": "EternalBlue",
    "zerologon": "ZeroLogon",
    "proxylogon": "ProxyLogon",
    "proxyshell": "ProxyShell",
    "follina": "Follina",
    "moveit": "MOVEit",
    "shellshock": "Shellshock",
    "poodle": "POODLE",
    "krack": "KRACK",
    "freak": "FREAK",
    "logjam": "Logjam",
    "drown": "DROWN",
    "rowhammer": "Rowhammer",
    "downfall": "Downfall",
    "regresshion": "regreSSHion",
    "looney-tunables": "Looney Tunables",
    "dirty-pipe": "Dirty Pipe",
    "dirty-cow": "Dirty COW",
}

_TRIVIAL_TOKENS = {"the", "a", "an", "vulnerability", "vuln", "rce", "lpe", "exploit"}


def _plausible(name: str, key: str) -> bool:
    """Filter Haiku junk: too short, generic words, raw CVE-style ids."""
    if not name or len(name) < 3:
        return False
    if name.lower().strip() in _TRIVIAL_TOKENS:
        return False
    if re.match(r"^cve-\d{4}-\d+", name.lower()):
        return False
    if not key or len(key) < 3:
        return False
    return True


async def _collect_from_cache() -> dict[str, str]:
    found: dict[str, str] = {}
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text("SELECT entities_json FROM ner_cache WHERE model_version = 'haiku-4-5'")
        )
        for (entities_json,) in rows.fetchall():
            for ent in entities_json or []:
                if ent.get("type") != "vuln_alias":
                    continue
                name = ent.get("name", "")
                key = ent.get("normalized_key", "")
                if not _plausible(name, key):
                    continue
                found.setdefault(key, name)
    return found


async def main() -> None:
    from_cache = await _collect_from_cache()
    print(f"Found {len(from_cache)} vuln_alias entries in ner_cache")
    print(f"Adding {len(CANONICAL_VULN_ALIASES)} canonical entries")

    # Load existing file
    with open(DATA_FILE) as f:
        data = json.load(f)

    keywords = data.setdefault("keywords", {})

    # Apply both sources, canonical takes precedence on display-name
    merged: dict[str, str] = {**from_cache, **CANONICAL_VULN_ALIASES}
    for key, name in merged.items():
        existing = keywords.get(key)
        if existing and existing[1] != "vuln_alias":
            # Don't clobber an entry that's already another type
            print(f"Skipping {key} (already classified as {existing[1]})")
            continue
        keywords[key] = [name, "vuln_alias"]

    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)

    total = sum(1 for v in keywords.values() if v[1] == "vuln_alias")
    print(f"Wrote {DATA_FILE} — total vuln_alias entries: {total}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run the seed script (requires DB up)**

Run: `docker compose exec ingestion python scripts/seed_vuln_aliases.py`
Expected output includes a final line like `Wrote ... — total vuln_alias entries: 28` (number will vary).

- [ ] **Step 3: Verify entity_extractor picks them up**

Run: `pytest tests/test_entity_extractor.py -v -k "log4shell or vuln_alias" || pytest tests/test_entity_extractor.py -v`
Expected: tests pass; spot-check by adding (locally, no commit) a temporary test asserting `await extract_entities({"title": "Log4Shell still found in 30% of Java apps"})` includes `log4shell`.

- [ ] **Step 4: Commit (data file + script)**

```bash
git add scripts/seed_vuln_aliases.py data/threat_keywords.json
git commit -m "feat(ner): seed vuln_alias list from ner_cache and canonical names"
```

---

## Task 13: Eval script — `scripts/eval_ner.py`

**Files:**
- Create: `scripts/eval_ner.py`

- [ ] **Step 1: Write the eval script**

```python
"""NER eval — diff local model output against cached Haiku output.

For each ner_cache row under model_version='haiku-4-5':
  1. Look up the article body in OpenSearch.
  2. Call the local sidecar to produce local entities (this also fills the
     'securebert-v1' cache row as a side effect — the backfill IS the eval).
  3. Compute per-entity diff vs. cached Haiku entities, classify by input_zone,
     write rows to ner_eval_judgments with verdict NULL (pending adjudication).

At the end, print a summary of agree / only-haiku / only-local counts per type.
"""
import asyncio
import logging
from datetime import datetime
from typing import Iterable

from sqlalchemy import text

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.db.opensearch import INDEX_NEWS, get_os_client
from app.ingestion.ner_client import extract_entities_local

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

HAIKU_INPUT_CUTOFF = 500  # chars Haiku ever saw of the body


async def _iter_haiku_slugs() -> Iterable[tuple[str, list[dict]]]:
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text(
                "SELECT slug, entities_json FROM ner_cache "
                "WHERE model_version = 'haiku-4-5' "
                "ORDER BY slug"
            )
        )
        for slug, entities_json in rows.fetchall():
            yield slug, entities_json or []


async def _get_article_body(slug: str) -> tuple[str, str]:
    """Return (title, body_text) for the article. Empty strings if not found."""
    try:
        doc = await get_os_client().get(index=INDEX_NEWS, id=slug)
        src = doc.get("_source") or {}
        title = src.get("title") or ""
        body = src.get("content_extracted") or src.get("summary") or src.get("desc") or ""
        return title, body
    except Exception as exc:
        logger.warning("Skipping slug=%s — not in OpenSearch: %s", slug, exc)
        return "", ""


def _classify_zone(char_offset: int | None) -> str:
    if char_offset is None:
        return "shared"
    return "shared" if char_offset < HAIKU_INPUT_CUTOFF else "new-input"


def _diff(haiku: list[dict], local: list[dict]) -> list[tuple[dict, str, str | None]]:
    """Return list of (entity, source, input_zone) tuples to write as judgments."""
    haiku_keys = {(e["entity_type"] if "entity_type" in e else e["type"], e["normalized_key"]) for e in haiku}
    local_keys = {(e["type"], e["normalized_key"]) for e in local}
    out: list[tuple[dict, str, str | None]] = []

    agree = haiku_keys & local_keys
    only_haiku = haiku_keys - local_keys
    only_local = local_keys - haiku_keys

    for h in haiku:
        h_type = h.get("type") or h.get("entity_type")
        h_key = h["normalized_key"]
        if (h_type, h_key) in agree:
            out.append(({"type": h_type, "name": h.get("name", h_key), "normalized_key": h_key}, "both", "shared"))
        elif (h_type, h_key) in only_haiku:
            out.append(({"type": h_type, "name": h.get("name", h_key), "normalized_key": h_key}, "haiku", "shared"))

    for l in local:
        if (l["type"], l["normalized_key"]) in only_local:
            zone = _classify_zone(l.get("char_offset"))
            out.append(({"type": l["type"], "name": l["name"], "normalized_key": l["normalized_key"]}, "local", zone))

    return out


async def _write_judgments(slug: str, judgments: list[tuple[dict, str, str | None]]) -> None:
    if not judgments:
        return
    async with AsyncSessionLocal() as session:
        for ent, source, zone in judgments:
            await session.execute(
                text(
                    "INSERT INTO ner_eval_judgments "
                    "(slug, entity_type, entity_normalized_key, source, input_zone) "
                    "VALUES (:slug, :etype, :ekey, :src, :zone) "
                    "ON CONFLICT (slug, entity_type, entity_normalized_key, source) DO NOTHING"
                ),
                {
                    "slug": slug,
                    "etype": ent["type"],
                    "ekey": ent["normalized_key"],
                    "src": source,
                    "zone": zone,
                },
            )
        await session.commit()


async def main() -> None:
    totals: dict[tuple[str, str], int] = {}  # (etype, status) -> count
    processed = 0
    async for slug, haiku_entities in _iter_haiku_slugs():
        title, body = await _get_article_body(slug)
        if not title and not body:
            continue
        async with AsyncSessionLocal() as session:
            local_entities = await extract_entities_local(
                slug=slug, title=title, body=body, db_session=session
            )

        judgments = _diff(haiku_entities, local_entities)
        await _write_judgments(slug, judgments)

        for ent, source, _ in judgments:
            key = (ent["type"], source)
            totals[key] = totals.get(key, 0) + 1
        processed += 1
        if processed % 50 == 0:
            logger.info("Processed %d articles", processed)

    print("\n=== EVAL SUMMARY ===")
    print(f"Articles processed: {processed}")
    by_type: dict[str, dict[str, int]] = {}
    for (etype, source), count in totals.items():
        by_type.setdefault(etype, {})[source] = count
    print(f"{'type':<14}{'agree':<10}{'only-haiku':<14}{'only-local':<14}")
    for etype, counts in sorted(by_type.items()):
        agree = counts.get("both", 0)
        only_h = counts.get("haiku", 0)
        only_l = counts.get("local", 0)
        print(f"{etype:<14}{agree:<10}{only_h:<14}{only_l:<14}")

    print("\nStopping criterion check (only-haiku rate vs haiku total per type):")
    for etype, counts in sorted(by_type.items()):
        haiku_total = counts.get("both", 0) + counts.get("haiku", 0)
        if haiku_total == 0:
            continue
        rate = counts.get("haiku", 0) / haiku_total
        threshold = 0.20 if etype == "campaign" else 0.10
        verdict = "PASS" if rate <= threshold else "FAIL"
        print(f"  {etype:<14}only-haiku rate={rate:.1%}  threshold={threshold:.0%}  {verdict}")

    print(f"\nFinished at {datetime.utcnow().isoformat()}Z")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Sanity-run on a tiny subset (manual)**

Edit `_iter_haiku_slugs` temporarily to add `LIMIT 5` for a smoke test:
`"... WHERE model_version = 'haiku-4-5' ORDER BY slug LIMIT 5"`. Run:

`docker compose exec ingestion python scripts/eval_ner.py`

Expected: completes within ~30s, prints summary table with non-zero counts. Verify `ner_eval_judgments` populated:
`docker compose exec db psql -U postgres -d avild_news -c "SELECT count(*) FROM ner_eval_judgments WHERE verdict IS NULL;"`

Revert the LIMIT before committing.

- [ ] **Step 3: Commit**

```bash
git add scripts/eval_ner.py
git commit -m "feat(ner): eval script comparing local model vs cached Haiku entities"
```

---

## Task 14: Admin eval API — list, article, verdict, metrics

**Files:**
- Create: `app/api/routes/admin_ner_eval.py`
- Modify: `app/main.py` (register the router)

- [ ] **Step 1: Create the router**

`app/api/routes/admin_ner_eval.py`:

```python
"""Admin UI + API for NER eval adjudication."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_db
from app.core.templates import templates
from app.db.opensearch import INDEX_NEWS, get_os_client

router = APIRouter(prefix="/admin/ner-eval", tags=["admin"])


def _check_admin(request: Request) -> None:
    secret = request.headers.get("x-admin-secret") or request.query_params.get("admin_secret")
    if not settings.ADMIN_SECRET or secret != settings.ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Admin auth required")


class VerdictIn(BaseModel):
    slug: str
    entity_type: str
    entity_normalized_key: str
    source: str
    verdict: str  # correct | wrong | skip


@router.get("", response_class=HTMLResponse)
async def list_pending(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    _check_admin(request)
    rows = await session.execute(
        text(
            "SELECT slug, COUNT(*) AS pending_count "
            "FROM ner_eval_judgments WHERE verdict IS NULL "
            "GROUP BY slug ORDER BY pending_count DESC LIMIT 200"
        )
    )
    pending = [{"slug": r[0], "pending_count": r[1]} for r in rows.fetchall()]
    return templates.TemplateResponse(
        "admin_ner_eval_list.html",
        {"request": request, "pending": pending, "admin_secret": settings.ADMIN_SECRET},
    )


@router.get("/article/{slug}", response_class=HTMLResponse)
async def adjudicate_article(
    slug: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    _check_admin(request)

    try:
        doc = await get_os_client().get(index=INDEX_NEWS, id=slug)
    except Exception:
        raise HTTPException(status_code=404, detail="Article not found")
    src = doc.get("_source") or {}
    title = src.get("title") or ""
    body = src.get("content_extracted") or src.get("summary") or src.get("desc") or ""

    rows = await session.execute(
        text(
            "SELECT entity_type, entity_normalized_key, source, input_zone, verdict "
            "FROM ner_eval_judgments WHERE slug = :slug "
            "ORDER BY source, entity_type, entity_normalized_key"
        ),
        {"slug": slug},
    )
    judgments = [
        {
            "entity_type": r[0],
            "entity_normalized_key": r[1],
            "source": r[2],
            "input_zone": r[3],
            "verdict": r[4],
        }
        for r in rows.fetchall()
    ]

    return templates.TemplateResponse(
        "admin_ner_eval_article.html",
        {
            "request": request,
            "slug": slug,
            "title": title,
            "body": body,
            "judgments": judgments,
            "admin_secret": settings.ADMIN_SECRET,
        },
    )


@router.post("/verdict")
async def post_verdict(
    payload: VerdictIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    _check_admin(request)
    if payload.verdict not in ("correct", "wrong", "skip"):
        raise HTTPException(status_code=400, detail="invalid verdict")
    await session.execute(
        text(
            "UPDATE ner_eval_judgments SET verdict = :v, judged_at = :ts "
            "WHERE slug = :slug AND entity_type = :etype "
            "AND entity_normalized_key = :ekey AND source = :src"
        ),
        {
            "v": payload.verdict,
            "ts": datetime.now(timezone.utc),
            "slug": payload.slug,
            "etype": payload.entity_type,
            "ekey": payload.entity_normalized_key,
            "src": payload.source,
        },
    )
    await session.commit()
    return {"status": "ok"}


@router.get("/metrics")
async def metrics(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict:
    _check_admin(request)
    rows = await session.execute(
        text(
            "SELECT entity_type, source, verdict, COUNT(*) "
            "FROM ner_eval_judgments WHERE verdict IS NOT NULL "
            "GROUP BY entity_type, source, verdict"
        )
    )
    out: dict[str, dict[str, dict[str, int]]] = {}
    for etype, source, verdict, count in rows.fetchall():
        out.setdefault(etype, {}).setdefault(source, {})[verdict] = count
    return {"by_type": out}
```

- [ ] **Step 2: Register the router in `app/main.py`**

Find the existing router includes (search for `app.include_router(`) and add:

```python
from app.api.routes import admin_ner_eval  # noqa
app.include_router(admin_ner_eval.router, prefix="/api")
```

- [ ] **Step 3: Sanity check the routes load**

Run: `docker compose up -d backend && curl -fs http://localhost/api/admin/ner-eval/metrics -H "x-admin-secret: ${ADMIN_SECRET}" | head -c 200`
Expected: JSON response (initially `{"by_type":{}}` before any verdicts).

- [ ] **Step 4: Commit**

```bash
git add app/api/routes/admin_ner_eval.py app/main.py
git commit -m "feat(ner): admin ner-eval API routes (list, article, verdict, metrics)"
```

---

## Task 15: Admin eval templates

**Files:**
- Create: `templates/admin_ner_eval_list.html`
- Create: `templates/admin_ner_eval_article.html`

- [ ] **Step 1: Create list template**

`templates/admin_ner_eval_list.html`:

```html
<!doctype html>
<html><head>
  <meta charset="utf-8">
  <title>NER Eval — Pending</title>
  <style>
    body { font-family: -apple-system, sans-serif; max-width: 900px; margin: 2em auto; padding: 0 1em; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 0.5em 1em; border-bottom: 1px solid #ddd; text-align: left; }
    th { background: #f5f5f5; }
    a { color: #0066cc; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .count { font-variant-numeric: tabular-nums; color: #888; }
  </style>
</head><body>
  <h1>NER Eval — Pending Adjudication</h1>
  <p><a href="/api/admin/ner-eval/metrics?admin_secret={{ admin_secret }}">View metrics</a></p>
  <table>
    <thead><tr><th>Article</th><th>Pending entities</th></tr></thead>
    <tbody>
      {% for row in pending %}
      <tr>
        <td><a href="/api/admin/ner-eval/article/{{ row.slug }}?admin_secret={{ admin_secret }}">{{ row.slug }}</a></td>
        <td class="count">{{ row.pending_count }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</body></html>
```

- [ ] **Step 2: Create article adjudication template**

`templates/admin_ner_eval_article.html`:

```html
<!doctype html>
<html><head>
  <meta charset="utf-8">
  <title>NER Eval — {{ slug }}</title>
  <style>
    body { font-family: -apple-system, sans-serif; max-width: 1100px; margin: 2em auto; padding: 0 1em; }
    .grid { display: grid; grid-template-columns: 1.6fr 1fr; gap: 2em; }
    .body { white-space: pre-wrap; font-size: 0.92em; line-height: 1.5; background: #fafafa; padding: 1em; border: 1px solid #eee; max-height: 70vh; overflow: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 0.92em; }
    th, td { padding: 0.4em 0.5em; border-bottom: 1px solid #eee; text-align: left; }
    .src-haiku { background: #fff5e6; }
    .src-local { background: #e6f3ff; }
    .src-both  { background: #e6ffe6; }
    .verdict-buttons button { margin-right: 0.3em; cursor: pointer; }
    .verdict-correct { color: #060; }
    .verdict-wrong   { color: #c00; }
    .verdict-skip    { color: #888; }
    .zone-new-input  { color: #b58; font-size: 0.85em; }
    .zone-shared     { color: #888; font-size: 0.85em; }
    .done { opacity: 0.4; }
  </style>
</head><body>
  <h1>{{ title or slug }}</h1>
  <p><a href="/api/admin/ner-eval?admin_secret={{ admin_secret }}">&laquo; Back to list</a></p>
  <div class="grid">
    <div>
      <h3>Body</h3>
      <div class="body">{{ body }}</div>
    </div>
    <div>
      <h3>Entities ({{ judgments|length }})</h3>
      <table id="entities">
        <thead><tr><th>Source</th><th>Type</th><th>Key</th><th>Zone</th><th>Verdict</th></tr></thead>
        <tbody>
        {% for j in judgments %}
          <tr class="src-{{ j.source }} {% if j.verdict %}done{% endif %}"
              data-type="{{ j.entity_type }}"
              data-key="{{ j.entity_normalized_key }}"
              data-source="{{ j.source }}">
            <td>{{ j.source }}</td>
            <td>{{ j.entity_type }}</td>
            <td>{{ j.entity_normalized_key }}</td>
            <td class="zone-{{ j.input_zone or 'shared' }}">{{ j.input_zone or '' }}</td>
            <td class="verdict-buttons">
              {% if j.verdict %}
                <span class="verdict-{{ j.verdict }}">{{ j.verdict }}</span>
              {% else %}
                <button onclick="submitVerdict(this, 'correct')">&#10003; correct</button>
                <button onclick="submitVerdict(this, 'wrong')">&#10007; wrong</button>
                <button onclick="submitVerdict(this, 'skip')">? skip</button>
              {% endif %}
            </td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  <script>
    const ADMIN_SECRET = "{{ admin_secret }}";
    const SLUG = "{{ slug }}";
    async function submitVerdict(btn, verdict) {
      const row = btn.closest("tr");
      const payload = {
        slug: SLUG,
        entity_type: row.dataset.type,
        entity_normalized_key: row.dataset.key,
        source: row.dataset.source,
        verdict: verdict,
      };
      const resp = await fetch("/api/admin/ner-eval/verdict?admin_secret=" + encodeURIComponent(ADMIN_SECRET), {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify(payload),
      });
      if (resp.ok) {
        row.classList.add("done");
        row.querySelector(".verdict-buttons").innerHTML =
          '<span class="verdict-' + verdict + '">' + verdict + '</span>';
      } else {
        alert("Failed: " + resp.status);
      }
    }
  </script>
</body></html>
```

- [ ] **Step 3: Visual smoke test**

Run: `docker compose up -d backend`. Open `http://localhost/api/admin/ner-eval?admin_secret=${ADMIN_SECRET}`. Expected: list page renders with a row per article that has pending judgments. Click one — article page renders with entities table and the body in a scrollable panel. Click `correct` on a row — the row becomes greyed-out, the verdict appears.

- [ ] **Step 4: Commit**

```bash
git add templates/admin_ner_eval_list.html templates/admin_ner_eval_article.html
git commit -m "feat(ner): admin eval UI templates"
```

---

## Task 16: Cutover script

**Files:**
- Create: `scripts/cutover_ner.py`

- [ ] **Step 1: Write the cutover script**

```python
"""Local NER cutover orchestrator.

Steps:
  1. Snapshot ner_cache to data/ner_cache_snapshot_<timestamp>.json (rollback insurance).
  2. Run scripts/eval_ner.py (no-op import-and-run) to populate securebert-v1 cache
     rows AND seed ner_eval_judgments.
  3. PROMPT user to open /admin/ner-eval and adjudicate. Wait for typed 'yes' to continue.
  4. Print reminder to set NER_ACTIVE_MODEL=securebert-v1 in .env and restart ingestion.
  5. PROMPT for confirmation that .env was updated and ingestion restarted.
  6. Run scripts/cluster_articles.py --reset (13-min full rebuild).

This script does NOT modify .env or restart containers automatically — operator must
do those steps manually so the cutover stays observable and rollback stays one
config change away.
"""
import asyncio
import datetime
import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text

from app.db.session import AsyncSessionLocal

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data"


async def _snapshot_cache() -> Path:
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out = SNAPSHOT_DIR / f"ner_cache_snapshot_{ts}.json"
    rows = []
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT slug, model_version, entities_json, extracted_at FROM ner_cache")
        )
        for slug, mv, ents, ts_col in result.fetchall():
            rows.append({
                "slug": slug,
                "model_version": mv,
                "entities_json": ents,
                "extracted_at": ts_col.isoformat() if ts_col else None,
            })
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    return out


def _prompt(msg: str) -> None:
    print(f"\n{msg}")
    ans = input("Type 'yes' to continue, anything else to abort: ").strip().lower()
    if ans != "yes":
        print("Aborted.")
        sys.exit(1)


def _run(cmd: list[str]) -> None:
    print(f"\n$ {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print(f"FAILED with exit code {proc.returncode}")
        sys.exit(proc.returncode)


async def main() -> None:
    print("=== NER cutover orchestrator ===")
    print("Step 1/4: snapshot ner_cache ...")
    snap = await _snapshot_cache()
    print(f"  -> {snap}")

    print("\nStep 2/4: running eval_ner.py (this populates securebert-v1 cache rows)")
    print("This can take several minutes for ~1000 articles. Logs will stream below.\n")
    _run([sys.executable, "scripts/eval_ner.py"])

    print("\nStep 3/4: open the admin UI and adjudicate disagreements:")
    print("    http://localhost/api/admin/ner-eval?admin_secret=$ADMIN_SECRET")
    print("Check /metrics — proceed only when only-haiku rates are within thresholds:")
    print("    product / malware / actor / tool   <= 10%")
    print("    campaign                            <= 20%")
    _prompt("Have you adjudicated and confirmed the stopping criterion is met?")

    print("\nStep 4/4: manual operator actions required:")
    print("  a) Set NER_ACTIVE_MODEL=securebert-v1 in .env")
    print("  b) Restart ingestion:  docker compose restart ingestion")
    _prompt("Have you completed (a) and (b)?")

    print("\nFinal: rebuilding clusters (~13 min) ...")
    _run(["docker", "compose", "exec", "ingestion", "python", "scripts/cluster_articles.py", "--reset"])

    print("\nCutover complete. Spot-check the site.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Lint/syntax check only — do not run for real yet**

Run: `python -c "import ast; ast.parse(open('scripts/cutover_ner.py').read())"`
Expected: no output (syntax OK).

- [ ] **Step 3: Commit**

```bash
git add scripts/cutover_ner.py
git commit -m "feat(ner): cutover orchestrator script"
```

---

## Task 17: `.env.example` and CLAUDE.md note

**Files:**
- Modify: `.env.example` (if it exists; otherwise add the lines to README or skip)
- Modify: `.claude/CLAUDE.md` (add a paragraph under "Architecture Principles" about the local NER sidecar)

- [ ] **Step 1: Append to `.env.example`**

(If `.env.example` does not exist in the repo, skip this step.)

```
# Local NER sidecar
NER_SIDECAR_URL=http://ner:8001
NER_ACTIVE_MODEL=haiku-4-5
NER_REQUEST_TIMEOUT_S=30
NER_MODEL_REVISION=main
```

- [ ] **Step 2: Add a paragraph to `.claude/CLAUDE.md`**

After the existing "Source credibility" paragraph in the Architecture Principles section, insert:

```markdown
**NER (Named Entity Recognition)** runs in a dedicated sidecar service `ner` (Docker
Compose service) that loads `attack-vector/SecureModernBERT-NER` on CPU. The ingestion
container calls it over HTTP via `app/ingestion/ner_client.py`. The `ner_cache` table
is keyed on `(slug, model_version)`; the active version is set by env var
`NER_ACTIVE_MODEL`. `app/ingestion/ner_llm.py` (Haiku) is preserved for backfills and
the eval harness but is not on the hot path. The seventh entity type, `vuln_alias`,
is handled by a curated regex list in `data/threat_keywords.json`, seeded by
`scripts/seed_vuln_aliases.py`.
```

- [ ] **Step 3: Commit**

```bash
git add .env.example .claude/CLAUDE.md
git commit -m "docs(ner): document local sidecar setup in CLAUDE.md and .env.example"
```

---

## Final verification

- [ ] **Run the full test suite**

Run: `docker compose run --rm backend pytest tests/ -v`
Expected: all tests pass. Investigate any failure before declaring complete.

- [ ] **Bring the full stack up**

Run: `docker compose up -d`
Expected: all services healthy. `docker compose ps` should show `ner` as healthy after ~90s.

- [ ] **Spot-check production path (with NER_ACTIVE_MODEL still=haiku-4-5)**

The hot path will use `extract_entities_local` against `ner` sidecar but cache under `haiku-4-5` model_version — wait, this is a behavioral note: with `NER_ACTIVE_MODEL=haiku-4-5` and the local sidecar live, the local client will write its output under `model_version=haiku-4-5`, mixing namespaces. **Therefore:** before any production ingest with the new code, set `NER_ACTIVE_MODEL=securebert-v1` in `.env`. This is the cutover step. The plan default of `haiku-4-5` in code is only meaningful for first-deploy backward-compat reading; for writes, set the env var.

Do NOT run the full cutover (`scripts/cutover_ner.py`) until you have manually adjudicated at least a small sample via the admin UI and confirmed the stopping criterion is met. See Task 16.

---

## Notes for the engineer

- **Concurrency**: the sidecar uses a single `asyncio.Lock` inside `NerModel.extract`. Concurrent callers serialize cleanly. Do not remove the lock.
- **Image size**: `Dockerfile.ner` adds ~1.2 GB to the `ner` image only. `Dockerfile.backend` is unchanged — backend / ingestion / briefing remain torch-free.
- **Model version string**: `securebert-v1` is the convention used in this plan. If you change it (e.g., upgrade to a fine-tuned variant), bump it everywhere: `Dockerfile.ner` ARG, `docker-compose.yml` env, sidecar env, and the cutover script's stopping-criterion reminder.
- **Cluster rebuild on cutover** is non-optional: entities feed clustering per CLAUDE.md. Without `cluster_articles.py --reset`, existing clusters keep their old entity signals.
- **Rollback**: if quality regresses after cutover, set `NER_ACTIVE_MODEL=haiku-4-5` in `.env`, restart ingestion, run `cluster_articles.py --reset` again. The Haiku rows are still in `ner_cache` and the snapshot from cutover step 1 is on disk.
