# Daily WhatsApp Brief — Design Spec

## Goal

Deliver a daily WhatsApp message to a configured phone number summarising the top cybersecurity clusters from the last 24 hours, with per-cluster AI summaries written by Claude Haiku.

---

## Architecture

### New module: `app/briefing/`

Five focused files, each with one responsibility:

| File | Responsibility |
|------|---------------|
| `selector.py` | Query OpenSearch clusters index for top N active clusters |
| `generator.py` | Call Claude Haiku to produce a 2-3 sentence WhatsApp-ready summary per cluster |
| `formatter.py` | Assemble the full WhatsApp message string |
| `sender.py` | Send the message (stub: write to file + log; live: Twilio) |
| `pipeline.py` | Orchestrate selector → generator → formatter → sender with idempotency |

### Script: `scripts/send_daily_brief.py`

CLI entry point. Flags: `--dry-run` (skip send + DB write), `--force` (override idempotency check), `--top-n N` (default 7), `--hours N` (look-back window, default 24).

### Postgres table: `brief_log`

Idempotency store. One row per calendar day (UTC). Prevents double-sends if the container restarts mid-day.

```sql
CREATE TABLE brief_log (
    id          SERIAL PRIMARY KEY,
    period_date DATE NOT NULL UNIQUE,
    cluster_count INTEGER NOT NULL DEFAULT 0,
    body        TEXT NOT NULL,
    status      VARCHAR(20) NOT NULL DEFAULT 'sent',
    error_msg   TEXT,
    sent_at     TIMESTAMPTZ DEFAULT NOW()
);
```

### Docker service: `briefing`

New service in `docker-compose.yml`. Uses same `Dockerfile.backend` image as `ingestion`. On startup, calculates seconds until next 08:00 UTC and sleeps; then runs the script; then loops. No external scheduler required.

---

## Cluster Selection

Query `clusters` index:

- Filter: `latest_at >= now - {hours}h` (clusters that have had new activity in the window)
- Sort: `score` desc
- Take top N (default 7)
- Fetch fields: `_id`, `label`, `summary`, `why_it_matters`, `score`, `max_cvss`, `cisa_kev`, `cve_ids`, `article_count`, `entity_keys`

---

## LLM Summarisation

**Model:** `claude-haiku-4-5-20251001` (fast, cheap — same as NER)

**Per-cluster prompt:**

> You are writing a WhatsApp security brief for a security professional. In exactly 2-3 punchy sentences, summarise this story. Cover: what happened, what/who is affected, and the severity/urgency. No bullet points. No markdown. Plain text only.

Input: cluster label + existing summary + existing why_it_matters + top CVE IDs + max CVSS

Falls back to `summary` field verbatim if LLM call fails.

---

## Brief Format

```
🔐 *Kiber Daily Brief* — {Day, Month D, YYYY}

{N} stories trending today:

━━━━━━━━━━

1. *{cluster label}*
{2-3 sentence LLM summary}
{CVE line if any: 📌 CVE-2026-XXXX}
{CVSS line if ≥7.0: 🔴 CVSS {score}}
{KEV badge if flagged: ⚠️ CISA KEV}

2. ...

━━━━━━━━━━
🌐 news.avild.com
```

---

## Delivery (Stub → Twilio)

`sender.py` checks environment:

- **Stub mode** (default — no `TWILIO_ACCOUNT_SID`): writes brief to `/app/briefs/YYYY-MM-DD.txt` and logs to stdout. Returns `True`.
- **Live mode** (`TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` + `TWILIO_FROM_NUMBER` all set): sends via `twilio` Python SDK to `WHATSAPP_PHONE_NUMBER`.

`twilio` is added to `requirements.txt` but importing it is guarded by the env-var check so the container doesn't crash if the SDK isn't used.

---

## Idempotency

`pipeline.py` checks `brief_log` for `period_date = today (UTC)` before running. If a `sent` row exists, it returns immediately (no LLM call, no send). `--force` flag skips this check and upserts the row.

---

## Environment Variables

| Variable | Required | Notes |
|----------|----------|-------|
| `ANTHROPIC_API_KEY` | Yes | Claude Haiku calls |
| `WHATSAPP_PHONE_NUMBER` | Yes | Recipient e.g. `+1234567890` |
| `TWILIO_ACCOUNT_SID` | No | Blank = stub mode |
| `TWILIO_AUTH_TOKEN` | No | Blank = stub mode |
| `TWILIO_FROM_NUMBER` | No | e.g. `whatsapp:+14155238886` |

---

## Error Handling

- LLM failure per cluster: fall back to raw `summary` field
- OpenSearch query failure: log + mark `brief_log` row as `failed`
- Send failure: log + mark `brief_log` row as `failed`; does not retry (next daily run will produce a fresh brief)

---

## Tests

- `tests/briefing/test_selector.py` — mock OS response, assert cluster shape
- `tests/briefing/test_generator.py` — mock Anthropic client, assert fallback on failure
- `tests/briefing/test_formatter.py` — assert message structure (header, N stories, footer)
- `tests/briefing/test_sender.py` — stub mode writes file; no Twilio credentials → no Twilio import
- `tests/briefing/test_pipeline.py` — idempotency check blocks re-run; `--force` overrides

---

## File Map

**New files:**
- `app/briefing/__init__.py`
- `app/briefing/selector.py`
- `app/briefing/generator.py`
- `app/briefing/formatter.py`
- `app/briefing/sender.py`
- `app/briefing/pipeline.py`
- `scripts/send_daily_brief.py`
- `tests/briefing/__init__.py`
- `tests/briefing/test_selector.py`
- `tests/briefing/test_generator.py`
- `tests/briefing/test_formatter.py`
- `tests/briefing/test_sender.py`
- `tests/briefing/test_pipeline.py`
- `alembic/versions/f7b8c9d0e1f2_add_brief_log.py`

**Modified files:**
- `docker-compose.yml` — add `briefing` service + `brief_outputs` volume
- `requirements.txt` — add `twilio`
