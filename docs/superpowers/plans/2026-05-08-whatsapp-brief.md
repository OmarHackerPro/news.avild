# WhatsApp Daily Brief — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Daily WhatsApp message with top cybersecurity clusters from the last 24h, AI-summarised by Claude Haiku, sent at 08:00 UTC.

**Architecture:** New `app/briefing/` module (selector → generator → formatter → sender → pipeline). Postgres `brief_log` table for idempotency. New `briefing` Docker service. Stub sender defaults to file/log; live mode via Twilio env vars.

**Tech Stack:** Python 3.12, asyncio, opensearch-py (async), anthropic SDK, twilio, SQLAlchemy async, Alembic, Docker Compose

**Worktree:** `.worktrees/whatsapp-brief` on branch `feature/whatsapp-brief`

---

## Task 1: Alembic migration — `brief_log` table

**Files:**
- Create: `alembic/versions/f7b8c9d0e1f2_add_brief_log.py`

- [ ] **Step 1: Write the migration**

```python
"""Add brief_log table

Revision ID: f7b8c9d0e1f2
Revises: 0a1b2c3d4e5f
Create Date: 2026-05-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "0a1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "brief_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("period_date", sa.Date(), nullable=False),
        sa.Column("cluster_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="sent"),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("period_date", name="uq_brief_log_period_date"),
    )


def downgrade() -> None:
    op.drop_table("brief_log")
```

- [ ] **Step 2: Commit**

```bash
git add alembic/versions/f7b8c9d0e1f2_add_brief_log.py
git commit -m "feat(brief): add brief_log migration"
```

---

## Task 2: `app/briefing/selector.py`

**Files:**
- Create: `app/briefing/__init__.py` (empty)
- Create: `app/briefing/selector.py`
- Create: `tests/briefing/__init__.py` (empty)
- Create: `tests/briefing/test_selector.py`

- [ ] **Step 1: Write `tests/briefing/test_selector.py`**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.briefing.selector import fetch_top_clusters


@pytest.mark.asyncio
async def test_fetch_top_clusters_returns_list():
    fake_hits = [
        {
            "_id": "cluster-1",
            "_source": {
                "label": "Apache RCE",
                "summary": "Remote code execution in Apache.",
                "why_it_matters": "Widely deployed.",
                "score": 85.0,
                "max_cvss": 9.8,
                "cisa_kev": True,
                "cve_ids": ["CVE-2026-1234"],
                "article_count": 5,
                "entity_keys": ["apache"],
            },
        }
    ]
    mock_client = MagicMock()
    mock_client.search = AsyncMock(return_value={
        "hits": {"hits": fake_hits, "total": {"value": 1}}
    })

    result = await fetch_top_clusters(mock_client, top_n=7, hours=24)
    assert len(result) == 1
    assert result[0]["id"] == "cluster-1"
    assert result[0]["label"] == "Apache RCE"
    assert result[0]["score"] == 85.0
    assert result[0]["cisa_kev"] is True


@pytest.mark.asyncio
async def test_fetch_top_clusters_empty_index():
    mock_client = MagicMock()
    mock_client.search = AsyncMock(return_value={
        "hits": {"hits": [], "total": {"value": 0}}
    })

    result = await fetch_top_clusters(mock_client, top_n=7, hours=24)
    assert result == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/briefing/test_selector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.briefing'`

- [ ] **Step 3: Write `app/briefing/__init__.py` and `app/briefing/selector.py`**

`app/briefing/__init__.py` — empty file.

`app/briefing/selector.py`:
```python
"""Select top clusters from OpenSearch for the daily brief."""
import logging
from typing import Any

from app.db.opensearch import INDEX_CLUSTERS

logger = logging.getLogger(__name__)

_SOURCE_FIELDS = [
    "label", "summary", "why_it_matters", "score",
    "max_cvss", "cisa_kev", "cve_ids", "article_count", "entity_keys",
]


async def fetch_top_clusters(
    client: Any,
    top_n: int = 7,
    hours: int = 24,
) -> list[dict]:
    """Return up to top_n clusters with activity in the last `hours` hours, sorted by score desc."""
    body = {
        "size": top_n,
        "_source": _SOURCE_FIELDS,
        "query": {
            "range": {
                "latest_at": {"gte": f"now-{hours}h"}
            }
        },
        "sort": [{"score": {"order": "desc"}}],
    }
    try:
        resp = await client.search(index=INDEX_CLUSTERS, body=body)
    except Exception as exc:
        logger.error("OpenSearch cluster query failed: %s", exc)
        return []

    clusters = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        clusters.append({
            "id": hit["_id"],
            "label": src.get("label", ""),
            "summary": src.get("summary", ""),
            "why_it_matters": src.get("why_it_matters", ""),
            "score": src.get("score", 0.0),
            "max_cvss": src.get("max_cvss"),
            "cisa_kev": src.get("cisa_kev", False),
            "cve_ids": src.get("cve_ids") or [],
            "article_count": src.get("article_count", 0),
            "entity_keys": src.get("entity_keys") or [],
        })
    return clusters
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/briefing/test_selector.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/briefing/__init__.py app/briefing/selector.py tests/briefing/__init__.py tests/briefing/test_selector.py
git commit -m "feat(brief): add cluster selector"
```

---

## Task 3: `app/briefing/generator.py`

**Files:**
- Create: `app/briefing/generator.py`
- Create: `tests/briefing/test_generator.py`

- [ ] **Step 1: Write `tests/briefing/test_generator.py`**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.briefing.generator import generate_cluster_summary


@pytest.mark.asyncio
async def test_generate_summary_returns_text():
    cluster = {
        "id": "c1",
        "label": "Log4Shell Exploitation",
        "summary": "Attackers exploiting Log4j.",
        "why_it_matters": "Millions of systems affected.",
        "cve_ids": ["CVE-2021-44228"],
        "max_cvss": 10.0,
    }

    fake_response = MagicMock()
    fake_response.content = [MagicMock(type="text", text="Critical Log4j RCE. Millions affected.")]

    fake_client = MagicMock()
    fake_client.messages = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=fake_response)

    result = await generate_cluster_summary(cluster, client=fake_client)
    assert "Log4j" in result or len(result) > 10


@pytest.mark.asyncio
async def test_generate_summary_falls_back_on_failure():
    cluster = {
        "id": "c1",
        "label": "Test Cluster",
        "summary": "Fallback summary text.",
        "why_it_matters": "Important.",
        "cve_ids": [],
        "max_cvss": None,
    }

    fake_client = MagicMock()
    fake_client.messages = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=Exception("API error"))

    result = await generate_cluster_summary(cluster, client=fake_client)
    assert result == "Fallback summary text."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/briefing/test_generator.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write `app/briefing/generator.py`**

```python
"""Generate per-cluster WhatsApp summaries via Claude Haiku."""
import logging
import os
from typing import Any, Optional

import anthropic
import httpx

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 256

_SYSTEM = (
    "You are writing a WhatsApp security brief for a security professional. "
    "In exactly 2-3 punchy sentences, summarise this story. "
    "Cover: what happened, what/who is affected, and the severity or urgency. "
    "No bullet points. No markdown. Plain text only."
)

_client: Optional[anthropic.AsyncAnthropic] = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            timeout=httpx.Timeout(30.0),
        )
    return _client


async def generate_cluster_summary(
    cluster: dict,
    client: Optional[Any] = None,
) -> str:
    """Return 2-3 sentence plain-text summary. Falls back to cluster['summary'] on error."""
    c = client or _get_client()
    cves = ", ".join(cluster.get("cve_ids") or [])
    cvss = cluster.get("max_cvss")
    user_msg = (
        f"Story: {cluster['label']}\n"
        f"Summary: {cluster.get('summary', '')}\n"
        f"Why it matters: {cluster.get('why_it_matters', '')}\n"
    )
    if cves:
        user_msg += f"CVEs: {cves}\n"
    if cvss:
        user_msg += f"Max CVSS: {cvss}\n"

    try:
        resp = await c.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        text_block = next((b for b in resp.content if b.type == "text"), None)
        if text_block and text_block.text.strip():
            return text_block.text.strip()
    except Exception as exc:
        logger.warning("LLM summary failed for cluster %s: %s", cluster.get("id"), exc)

    return cluster.get("summary") or ""
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/briefing/test_generator.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/briefing/generator.py tests/briefing/test_generator.py
git commit -m "feat(brief): add Claude Haiku cluster summariser"
```

---

## Task 4: `app/briefing/formatter.py`

**Files:**
- Create: `app/briefing/formatter.py`
- Create: `tests/briefing/test_formatter.py`

- [ ] **Step 1: Write `tests/briefing/test_formatter.py`**

```python
from datetime import date
from app.briefing.formatter import format_brief


def test_format_brief_structure():
    clusters = [
        {
            "label": "Apache RCE",
            "summary_text": "Critical RCE in Apache. Millions affected. Patch now.",
            "cve_ids": ["CVE-2026-1234"],
            "max_cvss": 9.8,
            "cisa_kev": True,
        },
        {
            "label": "Windows Zero-Day",
            "summary_text": "Active exploitation of Windows kernel flaw.",
            "cve_ids": [],
            "max_cvss": 7.5,
            "cisa_kev": False,
        },
    ]
    brief_date = date(2026, 5, 8)
    result = format_brief(clusters, brief_date)

    assert "*Kiber Daily Brief*" in result
    assert "May 8, 2026" in result
    assert "2 stories" in result
    assert "*Apache RCE*" in result
    assert "CVE-2026-1234" in result
    assert "CVSS 9.8" in result
    assert "CISA KEV" in result
    assert "*Windows Zero-Day*" in result
    assert "news.avild.com" in result


def test_format_brief_no_cve_no_cvss():
    clusters = [
        {
            "label": "Generic Threat",
            "summary_text": "A generic threat was observed.",
            "cve_ids": [],
            "max_cvss": None,
            "cisa_kev": False,
        }
    ]
    result = format_brief(clusters, date(2026, 5, 8))
    assert "CVE" not in result
    assert "CVSS" not in result
    assert "CISA KEV" not in result
    assert "*Generic Threat*" in result


def test_format_brief_single_story_grammar():
    clusters = [
        {
            "label": "Solo Incident",
            "summary_text": "One story only.",
            "cve_ids": [],
            "max_cvss": None,
            "cisa_kev": False,
        }
    ]
    result = format_brief(clusters, date(2026, 5, 8))
    assert "1 story" in result
    assert "stories" not in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/briefing/test_formatter.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write `app/briefing/formatter.py`**

```python
"""Format the WhatsApp brief message string."""
from datetime import date


def format_brief(clusters: list[dict], brief_date: date) -> str:
    """Return the full WhatsApp message string."""
    n = len(clusters)
    date_str = brief_date.strftime("%B %-d, %Y") if hasattr(brief_date, 'strftime') else str(brief_date)
    # Windows strftime doesn't support %-d; use a workaround
    try:
        date_str = brief_date.strftime("%B %-d, %Y")
    except ValueError:
        date_str = brief_date.strftime("%B %d, %Y").replace(" 0", " ")

    story_word = "story" if n == 1 else "stories"
    lines = [
        f"🔐 *Kiber Daily Brief* — {date_str}",
        "",
        f"{n} {story_word} trending today:",
        "",
        "━━━━━━━━━━",
    ]

    for i, cluster in enumerate(clusters, start=1):
        lines.append("")
        lines.append(f"{i}. *{cluster['label']}*")
        lines.append(cluster["summary_text"])

        meta = []
        cve_ids = cluster.get("cve_ids") or []
        if cve_ids:
            meta.append("📌 " + ", ".join(cve_ids[:3]))
        cvss = cluster.get("max_cvss")
        if cvss and cvss >= 7.0:
            meta.append(f"🔴 CVSS {cvss:.1f}")
        if cluster.get("cisa_kev"):
            meta.append("⚠️ CISA KEV")
        if meta:
            lines.append(" | ".join(meta))

    lines += [
        "",
        "━━━━━━━━━━",
        "🌐 news.avild.com",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/briefing/test_formatter.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/briefing/formatter.py tests/briefing/test_formatter.py
git commit -m "feat(brief): add WhatsApp message formatter"
```

---

## Task 5: `app/briefing/sender.py`

**Files:**
- Create: `app/briefing/sender.py`
- Create: `tests/briefing/test_sender.py`

- [ ] **Step 1: Write `tests/briefing/test_sender.py`**

```python
import os
import pytest
from pathlib import Path
from app.briefing.sender import send_brief


@pytest.mark.asyncio
async def test_stub_mode_writes_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER", "+1234567890")

    ok = await send_brief("Hello brief", output_dir=str(tmp_path), date_str="2026-05-08")
    assert ok is True

    files = list(tmp_path.glob("*.txt"))
    assert len(files) == 1
    assert files[0].read_text() == "Hello brief"


@pytest.mark.asyncio
async def test_stub_mode_no_phone_number_still_returns_true(tmp_path, monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("WHATSAPP_PHONE_NUMBER", raising=False)

    ok = await send_brief("Hello brief", output_dir=str(tmp_path), date_str="2026-05-08")
    assert ok is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/briefing/test_sender.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write `app/briefing/sender.py`**

```python
"""Send the WhatsApp brief.

Stub mode (default): writes brief to a file and logs to stdout.
Live mode: sends via Twilio when TWILIO_ACCOUNT_SID is set.
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT_DIR = "/app/briefs"


async def send_brief(
    text: str,
    output_dir: str = _DEFAULT_OUTPUT_DIR,
    date_str: str | None = None,
) -> bool:
    """Send or stub-send the brief. Returns True on success."""
    twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()

    if twilio_sid:
        return await _send_twilio(text)

    return _send_stub(text, output_dir=output_dir, date_str=date_str)


def _send_stub(text: str, output_dir: str, date_str: str | None) -> bool:
    logger.info("=== WHATSAPP BRIEF (stub) ===\n%s\n=== END BRIEF ===", text)
    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        fname = f"{date_str or 'brief'}.txt"
        (Path(output_dir) / fname).write_text(text)
    except Exception as exc:
        logger.warning("Could not write brief file: %s", exc)
    return True


async def _send_twilio(text: str) -> bool:
    try:
        from twilio.rest import Client  # noqa: PLC0415
    except ImportError:
        logger.error("twilio package not installed; cannot send live message")
        return False

    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ.get("TWILIO_FROM_NUMBER", "")
    to_number = os.environ.get("WHATSAPP_PHONE_NUMBER", "")

    if not to_number:
        logger.error("WHATSAPP_PHONE_NUMBER not set; cannot send")
        return False

    try:
        client = Client(sid, token)
        client.messages.create(
            from_=f"whatsapp:{from_number}" if not from_number.startswith("whatsapp:") else from_number,
            to=f"whatsapp:{to_number}" if not to_number.startswith("whatsapp:") else to_number,
            body=text,
        )
        logger.info("WhatsApp brief sent to %s", to_number)
        return True
    except Exception as exc:
        logger.error("Twilio send failed: %s", exc)
        return False
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/briefing/test_sender.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/briefing/sender.py tests/briefing/test_sender.py
git commit -m "feat(brief): add WhatsApp sender (stub + Twilio)"
```

---

## Task 6: `app/briefing/pipeline.py`

**Files:**
- Create: `app/briefing/pipeline.py`
- Create: `tests/briefing/test_pipeline.py`

- [ ] **Step 1: Write `tests/briefing/test_pipeline.py`**

```python
import pytest
from datetime import date, timezone, datetime
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_pipeline_idempotency_blocks_resend():
    """If brief_log already has a 'sent' row for today, pipeline returns early."""
    from app.briefing.pipeline import run_brief_pipeline

    with patch("app.briefing.pipeline._check_already_sent", new_callable=AsyncMock, return_value=True):
        result = await run_brief_pipeline(
            os_client=MagicMock(),
            db_session=MagicMock(),
            brief_date=date(2026, 5, 8),
            dry_run=False,
            force=False,
            top_n=7,
        )
    assert result["skipped"] is True


@pytest.mark.asyncio
async def test_pipeline_force_overrides_idempotency():
    """--force flag bypasses the idempotency check."""
    from app.briefing.pipeline import run_brief_pipeline

    clusters = [
        {
            "id": "c1", "label": "Test", "summary": "Summary.",
            "why_it_matters": "", "cve_ids": [], "max_cvss": None,
            "cisa_kev": False, "article_count": 1, "entity_keys": [],
            "score": 50.0,
        }
    ]

    with (
        patch("app.briefing.pipeline._check_already_sent", new_callable=AsyncMock, return_value=True),
        patch("app.briefing.pipeline.fetch_top_clusters", new_callable=AsyncMock, return_value=clusters),
        patch("app.briefing.pipeline.generate_cluster_summary", new_callable=AsyncMock, return_value="Summary."),
        patch("app.briefing.pipeline.send_brief", new_callable=AsyncMock, return_value=True),
        patch("app.briefing.pipeline._write_brief_log", new_callable=AsyncMock),
    ):
        result = await run_brief_pipeline(
            os_client=MagicMock(),
            db_session=MagicMock(),
            brief_date=date(2026, 5, 8),
            dry_run=False,
            force=True,
            top_n=7,
        )
    assert result["skipped"] is False
    assert result["cluster_count"] == 1


@pytest.mark.asyncio
async def test_pipeline_dry_run_skips_send():
    """dry_run=True formats the brief but does not send or write to DB."""
    from app.briefing.pipeline import run_brief_pipeline

    clusters = [
        {
            "id": "c1", "label": "Test", "summary": "Summary.",
            "why_it_matters": "", "cve_ids": [], "max_cvss": None,
            "cisa_kev": False, "article_count": 1, "entity_keys": [],
            "score": 50.0,
        }
    ]
    send_calls = []

    async def fake_send(text, **kwargs):
        send_calls.append(text)
        return True

    with (
        patch("app.briefing.pipeline._check_already_sent", new_callable=AsyncMock, return_value=False),
        patch("app.briefing.pipeline.fetch_top_clusters", new_callable=AsyncMock, return_value=clusters),
        patch("app.briefing.pipeline.generate_cluster_summary", new_callable=AsyncMock, return_value="Summary."),
        patch("app.briefing.pipeline.send_brief", side_effect=fake_send),
        patch("app.briefing.pipeline._write_brief_log", new_callable=AsyncMock),
    ):
        result = await run_brief_pipeline(
            os_client=MagicMock(),
            db_session=MagicMock(),
            brief_date=date(2026, 5, 8),
            dry_run=True,
            force=False,
            top_n=7,
        )
    assert len(send_calls) == 0
    assert result["dry_run"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/briefing/test_pipeline.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write `app/briefing/pipeline.py`**

```python
"""Orchestrate the daily brief pipeline with idempotency."""
import asyncio
import logging
from datetime import date, timezone, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.briefing.formatter import format_brief
from app.briefing.generator import generate_cluster_summary
from app.briefing.selector import fetch_top_clusters
from app.briefing.sender import send_brief

logger = logging.getLogger(__name__)


async def _check_already_sent(session: AsyncSession, period_date: date) -> bool:
    result = await session.execute(
        text("SELECT status FROM brief_log WHERE period_date = :d"),
        {"d": period_date},
    )
    row = result.fetchone()
    return row is not None and row[0] == "sent"


async def _write_brief_log(
    session: AsyncSession,
    period_date: date,
    cluster_count: int,
    body: str,
    status: str,
    error_msg: str | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO brief_log (period_date, cluster_count, body, status, error_msg) "
            "VALUES (:d, :cc, :body, :status, :err) "
            "ON CONFLICT (period_date) DO UPDATE SET "
            "cluster_count = EXCLUDED.cluster_count, body = EXCLUDED.body, "
            "status = EXCLUDED.status, error_msg = EXCLUDED.error_msg, "
            "sent_at = NOW()"
        ),
        {"d": period_date, "cc": cluster_count, "body": body, "status": status, "err": error_msg},
    )
    await session.commit()


async def run_brief_pipeline(
    os_client,
    db_session: AsyncSession,
    brief_date: date | None = None,
    dry_run: bool = False,
    force: bool = False,
    top_n: int = 7,
    hours: int = 24,
) -> dict:
    """Run the full pipeline. Returns a result dict with keys: skipped, dry_run, cluster_count, body."""
    today = brief_date or datetime.now(timezone.utc).date()

    if not force and not dry_run:
        already = await _check_already_sent(db_session, today)
        if already:
            logger.info("Brief already sent for %s; skipping (use --force to override)", today)
            return {"skipped": True, "dry_run": False, "cluster_count": 0, "body": ""}

    clusters = await fetch_top_clusters(os_client, top_n=top_n, hours=hours)
    if not clusters:
        logger.warning("No clusters found for brief date %s", today)
        if not dry_run:
            await _write_brief_log(db_session, today, 0, "", "failed", "No clusters found")
        return {"skipped": False, "dry_run": dry_run, "cluster_count": 0, "body": ""}

    summaries = await asyncio.gather(*[generate_cluster_summary(c) for c in clusters])
    enriched = [
        {**c, "summary_text": s}
        for c, s in zip(clusters, summaries)
    ]

    body = format_brief(enriched, today)

    if dry_run:
        logger.info("[DRY RUN] Brief for %s (%d clusters):\n%s", today, len(clusters), body)
        return {"skipped": False, "dry_run": True, "cluster_count": len(clusters), "body": body}

    date_str = today.isoformat()
    ok = await send_brief(body, date_str=date_str)
    status = "sent" if ok else "failed"
    await _write_brief_log(db_session, today, len(clusters), body, status)

    logger.info("Brief pipeline complete: date=%s clusters=%d status=%s", today, len(clusters), status)
    return {"skipped": False, "dry_run": False, "cluster_count": len(clusters), "body": body}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/briefing/test_pipeline.py -v`
Expected: 3 passed

- [ ] **Step 5: Run the full briefing test suite**

Run: `pytest tests/briefing/ -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add app/briefing/pipeline.py tests/briefing/test_pipeline.py
git commit -m "feat(brief): add pipeline orchestrator with idempotency"
```

---

## Task 7: `scripts/send_daily_brief.py`

**Files:**
- Create: `scripts/send_daily_brief.py`

No unit tests for the CLI script itself — it's thin orchestration over tested components.

- [ ] **Step 1: Write `scripts/send_daily_brief.py`**

```python
#!/usr/bin/env python
"""Send the daily WhatsApp brief.

Usage:
    python scripts/send_daily_brief.py              # send today's brief
    python scripts/send_daily_brief.py --dry-run    # format only, no send, no DB write
    python scripts/send_daily_brief.py --force      # override idempotency check
    python scripts/send_daily_brief.py --top-n 5    # select top 5 clusters (default 7)
    python scripts/send_daily_brief.py --hours 48   # 48h look-back window (default 24)
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.briefing.pipeline import run_brief_pipeline
from app.db.opensearch import get_os_client
from app.db.session import AsyncSessionLocal

logger = logging.getLogger("send_daily_brief")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--top-n", type=int, default=7)
    p.add_argument("--hours", type=int, default=24)
    return p.parse_args()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = parse_args()
    logger.info(
        "send_daily_brief starting | dry_run=%s force=%s top_n=%s hours=%s",
        args.dry_run, args.force, args.top_n, args.hours,
    )

    os_client = get_os_client()
    async with AsyncSessionLocal() as session:
        result = await run_brief_pipeline(
            os_client=os_client,
            db_session=session,
            dry_run=args.dry_run,
            force=args.force,
            top_n=args.top_n,
            hours=args.hours,
        )

    if result["skipped"]:
        logger.info("Brief skipped (already sent today)")
    elif result["dry_run"]:
        logger.info("[DRY RUN] Brief generated for %d clusters", result["cluster_count"])
    else:
        logger.info("Brief sent | clusters=%d", result["cluster_count"])


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Commit**

```bash
git add scripts/send_daily_brief.py
git commit -m "feat(brief): add CLI script send_daily_brief.py"
```

---

## Task 8: Docker service + requirements.txt

**Files:**
- Modify: `requirements.txt`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add `twilio` to requirements.txt**

Open `requirements.txt`. Add this line in alphabetical order among the third-party packages:

```
twilio
```

- [ ] **Step 2: Add `briefing` service and `brief_outputs` volume to docker-compose.yml**

Add a new service block after the `ingestion:` service:

```yaml
  briefing:
    build:
      context: .
      dockerfile: Dockerfile.backend
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:${DB_PASSWORD:-password}@db:5432/avild_news
      OPENSEARCH_URL: ${OPENSEARCH_URL:-}
      OPENSEARCH_USER: ${OPENSEARCH_USER:-}
      OPENSEARCH_PASSWORD: ${OPENSEARCH_PASSWORD:-}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
      WHATSAPP_PHONE_NUMBER: ${WHATSAPP_PHONE_NUMBER:-}
      TWILIO_ACCOUNT_SID: ${TWILIO_ACCOUNT_SID:-}
      TWILIO_AUTH_TOKEN: ${TWILIO_AUTH_TOKEN:-}
      TWILIO_FROM_NUMBER: ${TWILIO_FROM_NUMBER:-}
    volumes:
      - brief_outputs:/app/briefs
    depends_on:
      - db
    command: >
      sh -c 'while true; do
        python scripts/send_daily_brief.py;
        SECS=$(python3 -c "
import datetime
now = datetime.datetime.utcnow()
nxt = now.replace(hour=8, minute=0, second=0, microsecond=0)
if now >= nxt:
    import datetime as dt
    nxt += dt.timedelta(days=1)
print(max(int((nxt - now).total_seconds()), 60))
");
        echo "=== Sleeping ${SECS}s until next 08:00 UTC ===";
        sleep $SECS;
      done'
```

Also add `brief_outputs:` under the `volumes:` section at the bottom of the file.

- [ ] **Step 3: Verify the full test suite still passes**

Run: `pytest tests/briefing/ -v`
Expected: all passed

- [ ] **Step 4: Commit**

```bash
git add requirements.txt docker-compose.yml
git commit -m "feat(brief): add briefing Docker service and twilio dependency"
```

---

## Task 9: Merge to main

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/briefing/ -v`
Expected: all passed

- [ ] **Step 2: Merge feature branch to main**

```bash
cd ../../   # back to main worktree root
git merge feature/whatsapp-brief --no-ff -m "feat: add daily WhatsApp brief pipeline"
```

- [ ] **Step 3: Remove worktree**

```bash
git worktree remove .worktrees/whatsapp-brief
git branch -d feature/whatsapp-brief
```
