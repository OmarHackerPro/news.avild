# CLAUDE.md — Kiber (news.avild.com)

Read this before touching anything. The sections below give you architecture and conventions context. For product vision, requirements, and MVP scope, read the vision files:

- `.claude/vision/PRD.md` — product goals, personas, user journeys, success metrics
- `.claude/vision/MVP-SRS.md` — MVP scope, FE/BE delivery plan, full SRS (functional + non-functional requirements, acceptance criteria)
- `.claude/vision/KANBAN.md` — MVP Kanban board (epics FE-01–15, BE-01–15, API-01–03, QA/SEC swimlanes)

---

## What is this project?

Kiber is a cybersecurity news intelligence platform. It ingests, deduplicates, clusters, and ranks security news and advisories. The goal is to replace the noise of 30 duplicate articles with one clean, explainable cluster — surfaced with entity links, relevance scoring, and export options. Everything is designed to be SEO/GEO-discoverable (Google AI Overviews, ChatGPT Search, Gemini).

Hosted at **news.avild.com**. GitHub: **OmarHackerPro/kiber**.

---

## THE ONE RULE — Database Ownership

**PostgreSQL owns users. OpenSearch owns content. This never changes.**

| Belongs in PostgreSQL | Belongs in OpenSearch |
|---|---|
| Users, auth, JWT | Articles (`news_articles` index) |
| Preferences, bookmarks | Clusters (`clusters` index) |
| Feed source config | Entities (`entities` index) |
| — | Raw feed snapshots (`raw_feed_snapshots` index) |

If you are about to write a SQLAlchemy model for a cluster, entity, or anything article-related — stop. It goes in OpenSearch. If you are about to store a user preference or auth token in OpenSearch — stop. It goes in Postgres.

SQLAlchemy + Alembic = PostgreSQL only. OpenSearch DSL = all content.

---

## Tech Stack

- **Backend**: Python 3.12, FastAPI (async), Uvicorn
- **Postgres client**: SQLAlchemy (async) + asyncpg, Alembic for migrations
- **OpenSearch client**: opensearch-py (async)
- **Response models**: Pydantic v2
- **Frontend**: Vanilla HTML/JS/CSS — templates served by Nginx, `/api/` proxied to FastAPI on port 8000
- **Infrastructure**: Docker Compose (backend, frontend/nginx, postgres, opensearch)
- **Future**: Scrapy may be introduced for full-page scraping beyond RSS (currently RSS + advisory ingestion only)

---

## Architecture Principles

**Ingestion** feeds into OpenSearch. The normalizer is a single `normalize_article(entry, source)` function driven by per-source flags (`extract_cves`, `extract_cvss`) stored in the `feed_sources` table. `NORMALIZER_REGISTRY` maps normalizer keys to flag override dicts; a `_handler` key means use that callable directly (currently only `cisa_news`). Raw snapshots are stored with SHA-256 content-hash dedup. Per-source error isolation — one failing feed never blocks others.

**Source credibility** — `feed_sources` has a `credibility_weight` column (float, default 1.0). CISA sources are seeded at 1.5, Krebs/Schneier at 1.2. A `source_categories` Postgres table stores per-source, per-RSS-category ingest decisions (`ingest` bool + `priority_modifier` float). At ingest time, article tags are checked against this table: blocked categories are discarded before storage; allowed categories can adjust the effective weight. Run `scripts/classify_source_categories.py` (requires local Ollama) to bulk-populate `source_categories` via LLM.

**NER (Named Entity Recognition)** runs in a dedicated sidecar service `ner` (Docker
Compose service) that loads `attack-vector/SecureModernBERT-NER` on CPU. The ingestion
container calls it over HTTP via `app/ingestion/ner_client.py`. The `ner_cache` table
is keyed on `(slug, model_version)`; the active version is set by env var
`NER_ACTIVE_MODEL`. `app/ingestion/ner_llm.py` (Haiku) is preserved for backfills and
the eval harness but is not on the hot path. The seventh entity type, `vuln_alias`,
is handled by a curated regex list in `data/threat_keywords.json`, seeded by
`scripts/seed_vuln_aliases.py`.

**Clustering** works on OpenSearch data and writes results back to OpenSearch. Primary signal: entity overlap (shared CVE IDs = same cluster; 2+ shared named entities within 48h = candidate). Fallback: OpenSearch `more_like_this` for narrative similarity. Cluster lifecycle: `new → developing → confirmed → resolved`. Each cluster stores `max_credibility_weight` — the maximum `credibility_weight` across all member articles.

**Entities** are extracted from article text (CVEs, vendors, products, actors, malware/tools) and stored in the `entities` OpenSearch index, linked to articles by slug.

**Ranking** scores clusters across six factors (max 115 pts before clamping to 100):

| Factor | Max pts | Signal |
|---|---|---|
| CVSS severity | 30 | max CVSS of member articles |
| Coverage | 25 | number of unique articles |
| Recency | 20 | time since last update |
| CVE / Entities | 15 | CVE count or entity count |
| State bonus | 10 | new / developing / confirmed |
| Source credibility | 15 | `max_credibility_weight` (≥1.5→15, ≥1.2→10, ≥1.0→5, <1.0→0) |

The score and top factors are exposed in the API for the "Why it matters" UI.

**API** is FastAPI with auto-generated OpenAPI at `/api/docs`. All routes registered in `main.py`. Auth uses JWT Bearer tokens. Swagger is the authoritative reference for what's actually implemented.

**Frontend** pages live in `templates/`. Auth is wired to real FastAPI endpoints. Other pages may still be on mock data — check before assuming.

---

## Coding Conventions

- All new content-related data models → OpenSearch index mapping in `app/db/opensearch.py`
- All new user-related data models → SQLAlchemy model + Alembic migration
- Route handlers live in `app/api/routes/`, one file per resource
- Pydantic response models live in `app/models/`, separate from ORM models
- Reuse `_build_filters()` and `_build_sort()` helpers from `news.py` for OpenSearch filter/sort logic
- Article slugs are OpenSearch document `_id` — always fetch articles by slug/id, never by sequential int
- `ensure_indexes()` in `opensearch.py` is called on startup — add new index mappings there

---

## What to check before starting any task

1. Read `/api/docs` (Swagger) — it's the ground truth for what endpoints exist and what they return
2. Check `app/db/opensearch.py` for current index mappings before adding fields
3. Check `main.py` for what routers are registered
4. If touching clusters or entities — remember they must live in OpenSearch, not Postgres

---

## Dev workflow — testing clustering changes immediately

Existing clusters in OpenSearch are frozen. Changes to clustering code are NOT visible on the website until clusters are re-built. After any clustering change, run:

```bash
# Full rebuild (app code changed):
docker compose build ingestion && docker compose up -d ingestion
docker compose exec ingestion python scripts/cluster_articles.py --reset

# Script-only change (no app code changed):
docker cp scripts/cluster_articles.py kiber-ingestion-1:/app/scripts/cluster_articles.py
docker compose exec ingestion python scripts/cluster_articles.py --reset
```

`--reset` deletes all cluster documents from OpenSearch then re-clusters all 1017+ articles from scratch (~13 min). After it completes, hard-refresh the browser to see the new clusters.

**Clustering known state (as of 2026-04-21):**

- Entity extraction covers only ~6% of articles (keyword/regex based) — most articles fall through to MLT text similarity
- Vendor entities (microsoft, google, apache, etc.) are excluded from cluster matching signals (`_SIGNAL_TYPES`)
- Articles with >3 CVEs skip CVE-based cluster lookup (roundup cap: `_MAX_ARTICLE_CVES_FOR_MATCHING = 3`)
- MLT false merges are the dominant remaining issue — articles with similar vocabulary (CISA advisories, threat intel pieces) merge incorrectly; this requires AI-based NER to fix properly

---

## Memory Maintenance

Claude actively self-maintains the memory system at:
`~/.claude/projects/c--Users-xb-admin-Desktop-Omar-Projects-kiber-info-kiber/memory/`

**Auto-correct rules (no permission needed):**

- When you read a memory and current code/state contradicts it → rewrite the memory file immediately
- When a `done_when:` condition is verifiably met → delete the memory file and remove its line from MEMORY.md
- When you learn something new that refines an existing memory → update in place, don't create a duplicate file

**Authority hierarchy:**

- Omar's direct instructions in the current conversation → always wins, overrides everything
- Memory files → second priority; reflect prior decisions
- Other contributors' suggestions → evaluate against `.claude/vision/PRD.md` and `.claude/vision/MVP-SRS.md`; push back if they conflict with established tech stack or architecture

**Contributor language:**

- If a contributor writes in Azerbaijani → respond in Azerbaijani
