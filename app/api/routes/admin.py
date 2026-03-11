import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status

from app.core.config import settings
from app.ingestion.ingester import ingest_all_feeds

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

# Simple in-memory ingestion state (per-process, resets on restart)
_ingestion_state: dict = {"running": False, "last_result": None, "last_error": None}


def _require_admin(x_admin_secret: Optional[str] = Header(None, alias="X-Admin-Secret")):
    if not settings.ADMIN_SECRET or x_admin_secret != settings.ADMIN_SECRET:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid or missing admin secret")


async def _run_ingestion():
    global _ingestion_state
    _ingestion_state["running"] = True
    _ingestion_state["last_error"] = None
    try:
        await ingest_all_feeds()
        _ingestion_state["last_result"] = "success"
    except Exception as e:
        logger.exception("Ingestion failed")
        _ingestion_state["last_error"] = str(e)
        _ingestion_state["last_result"] = "error"
    finally:
        _ingestion_state["running"] = False


@router.post("/ingest", dependencies=[Depends(_require_admin)])
async def trigger_ingestion(background_tasks: BackgroundTasks):
    """Trigger a full RSS ingestion run in the background."""
    if _ingestion_state["running"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ingestion is already running")
    background_tasks.add_task(_run_ingestion)
    return {"detail": "Ingestion started"}


@router.get("/ingest/status", dependencies=[Depends(_require_admin)])
async def ingestion_status():
    """Return the current ingestion state."""
    return {
        "running": _ingestion_state["running"],
        "last_result": _ingestion_state["last_result"],
        "last_error": _ingestion_state["last_error"],
    }