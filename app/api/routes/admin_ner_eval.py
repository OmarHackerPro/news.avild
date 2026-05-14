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
    if not settings.NER_EVAL_ADMIN_SECRET or secret != settings.NER_EVAL_ADMIN_SECRET:
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
        {"request": request, "pending": pending, "admin_secret": settings.NER_EVAL_ADMIN_SECRET},
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
            "admin_secret": settings.NER_EVAL_ADMIN_SECRET,
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
