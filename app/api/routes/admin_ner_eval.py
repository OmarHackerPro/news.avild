"""Admin UI + API for NER eval adjudication."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_db
from app.core.templates import templates
from app.db.opensearch import INDEX_NEWS, get_os_client

router = APIRouter(prefix="/admin/ner-eval", tags=["admin"])


_LOGIN_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>NER Eval Login</title>
<style>body{{font-family:-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f5f5f5}}
form{{background:#fff;padding:2em;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1);min-width:300px}}
h2{{margin:0 0 1em}}input{{width:100%;padding:.6em;font-size:1em;box-sizing:border-box;border:1px solid #ccc;border-radius:4px}}
button{{margin-top:.8em;width:100%;padding:.7em;background:#333;color:#fff;border:none;border-radius:4px;font-size:1em;cursor:pointer}}
.err{{color:#c00;margin-top:.5em;font-size:.9em}}</style></head>
<body><form method="post"><h2>NER Eval</h2>
<input type="password" name="secret" placeholder="Admin secret" autofocus>
{err}<button type="submit">Enter</button></form></body></html>"""


def _check_admin(request: Request) -> None:
    secret = (
        request.headers.get("x-admin-secret")
        or request.query_params.get("admin_secret")
        or request.cookies.get("ner_eval_secret")
    )
    if not settings.NER_EVAL_ADMIN_SECRET or secret != settings.NER_EVAL_ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Admin auth required")


class VerdictIn(BaseModel):
    slug: str
    entity_type: str
    entity_normalized_key: str
    source: str
    verdict: str  # correct | wrong | skip


@router.get("/login", response_class=HTMLResponse)
async def login_page() -> HTMLResponse:
    return HTMLResponse(_LOGIN_PAGE.format(err=""))


@router.post("/login")
async def login_submit(request: Request) -> Response:
    form = await request.form()
    secret = form.get("secret", "")
    if secret != settings.NER_EVAL_ADMIN_SECRET:
        return HTMLResponse(_LOGIN_PAGE.format(err='<p class="err">Wrong secret.</p>'), status_code=401)
    response = RedirectResponse(url="/api/admin/ner-eval", status_code=303)
    response.set_cookie("ner_eval_secret", secret, httponly=True, samesite="strict")
    return response


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
        request,
        "admin_ner_eval_list.html",
        {"pending": pending, "admin_secret": settings.NER_EVAL_ADMIN_SECRET},
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
        request,
        "admin_ner_eval_article.html",
        {
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
    if payload.verdict not in ("correct", "wrong", "skip", "undo"):
        raise HTTPException(status_code=400, detail="invalid verdict")
    new_verdict = None if payload.verdict == "undo" else payload.verdict
    await session.execute(
        text(
            "UPDATE ner_eval_judgments SET verdict = :v, judged_at = :ts "
            "WHERE slug = :slug AND entity_type = :etype "
            "AND entity_normalized_key = :ekey AND source = :src"
        ),
        {
            "v": new_verdict,
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
