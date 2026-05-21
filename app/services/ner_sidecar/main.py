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
    mentions: int = 1


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
                char_offset=max(0, e.char_offset - (len(req.title) + 2)),
                mentions=e.mentions,
            )
            for e in raw
        ],
        model_version=MODEL_VERSION,
    )
