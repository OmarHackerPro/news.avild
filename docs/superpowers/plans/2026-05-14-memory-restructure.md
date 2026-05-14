# Memory Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the Claude auto-memory system for token efficiency (split 184-line entity_todos by subsystem), add condition-based obsolescence via `done_when:` frontmatter, add user profile, and wire self-correcting behavior into CLAUDE.md.

**Architecture:** All changes are file-level — create/edit markdown files in the project memory directory and add a `## Memory Maintenance` section to CLAUDE.md. No application code changes. Memory directory: `C:\Users\xb_admin\.claude\projects\c--Users-xb-admin-Desktop-Omar-Projects-kiber-info-kiber\memory\`

**Tech Stack:** Markdown, YAML frontmatter, Python 3 (audit script only)

---

### Task 1: Add Memory Maintenance section to CLAUDE.md

**Files:**
- Modify: `.claude/CLAUDE.md` (append after line 128)

- [ ] **Step 1: Append Memory Maintenance section**

Add the following to the end of `.claude/CLAUDE.md`:

```markdown

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
```

- [ ] **Step 2: Verify**

Open `.claude/CLAUDE.md`. Confirm it now has `## Memory Maintenance` as the last section with all four subsections (Auto-correct rules, Authority hierarchy, Contributor language). File should be ~145 lines.

- [ ] **Step 3: Commit**

```bash
git add .claude/CLAUDE.md
git commit -m "docs(claude): add memory maintenance rules and authority hierarchy"
```

---

### Task 2: Create user_omar.md and feedback_commit_style.md

**Files:**
- Create: `C:\Users\xb_admin\.claude\projects\c--Users-xb-admin-Desktop-Omar-Projects-kiber-info-kiber\memory\user_omar.md`
- Create: `C:\Users\xb_admin\.claude\projects\c--Users-xb-admin-Desktop-Omar-Projects-kiber-info-kiber\memory\feedback_commit_style.md`

- [ ] **Step 1: Create user_omar.md**

```markdown
---
name: user-omar-profile
description: Omar Shukurov — role, expertise, communication preferences, and collaboration style
metadata:
  type: user
---

Building Kiber (news.avild.com) — cybersecurity news intelligence platform.

**Expertise:** Backend/Python native (FastAPI, async, Docker, OpenSearch, SQLAlchemy). Comfortable with all infra layers. Can write vanilla JS/HTML/CSS frontend but backend is home territory. Has cybersecurity domain knowledge (CVEs, APTs, threat intel, CVSS) — don't explain infosec concepts.

**Communication:** Terse. No emojis. No trailing "here's what I did" summaries. No co-author lines or any mention of Claude in commits.

**API cost sensitivity:** Credits matter. Always state cost/API impact before running any LLM operation. Never run LLM/NER ops without explicit written go-ahead in the current turn.

**Work pace:** Long focused sessions with extended gaps between them. Not a daily-active workflow. Do not use calendar-based expiry on memories.

**Autonomy:** Stop and report after each explicitly requested task. Do not chain into "next logical step" without asking. When the next action has cost, is destructive, or involves architecture — ask first.
```

- [ ] **Step 2: Create feedback_commit_style.md**

```markdown
---
name: feedback-commit-style
description: Commit message rules — short descriptive subject, body for detail, zero Claude attribution
metadata:
  type: feedback
---

Write short, descriptive commit messages. Use the body for detail when needed.

**Why:** Omar's preference for clean git history without AI attribution noise.

**How to apply:** Never include "Co-Authored-By: Claude", "Generated with Claude Code", or any mention of Claude or AI in any part of a commit message. Subject line ≤50 chars. Body for context if needed.
```

- [ ] **Step 3: Verify both files exist and have correct frontmatter**

Both files should have `name`, `description`, and `metadata: type:` fields. `user_omar.md` has type `user`. `feedback_commit_style.md` has type `feedback`. Neither has a `done_when:` field.

---

### Task 3: Create project_entity_system.md

**Files:**
- Create: `C:\Users\xb_admin\.claude\projects\c--Users-xb-admin-Desktop-Omar-Projects-kiber-info-kiber\memory\project_entity_system.md`

This file absorbs TODOs 1, 2, 4, 5 from the old `project_entity_todos.md`.

- [ ] **Step 1: Create the file**

```markdown
---
name: project-entity-system
description: Open TODOs for entity extraction quality — CVE enrichment, blackfile dedup, threat_keywords migration, NER gating
metadata:
  type: project
done_when: threat_keywords.json migrated to OpenSearch index; blackfile dedup fix merged; CVE CVSS enrichment wired to NIST NVD API
---

## CVE CVSS Enrichment (deferred)
CVEs extracted by NER have no CVSS scores attached. NIST NVD API integration will fill these — see [[reference-nist-api]]. Do not implement severity extraction from article text or hardcode values. Deferred.

## Blackfile Dedup Bug
Regex extractor stores `blackfile`; LLM extractor stores `blackfile-extortion-campaign`. Both get stored as separate entity records because `normalized_key` differs.

**Root cause to investigate:** Is the gazetteer entry too short/ambiguous? Is `store_article_entities` missing a prefix-match dedup? Is the LLM over-specifying the campaign name?

**Fix direction:** Canonicalization step — when storing, check if any existing entity's `normalized_key` is a prefix/substring of the new one and merge, keeping the more specific name. Or: alias table mapping known synonyms to a canonical key.

## threat_keywords.json → OpenSearch Index
Currently a static flat file loaded at module import by `entity_extractor.py`. Per "OpenSearch owns content" rule, this must move to an OpenSearch index (e.g. `threat_keywords`) with keyword/type/aliases fields.

**Why:** Flat file requires redeployment to update. OpenSearch storage allows dynamic refresh via `sync_mitre_attack.py` without container restart.

**How to apply:** Create `threat_keywords` OpenSearch index. Update `entity_extractor.py` to load on startup from OpenSearch. Update `sync_mitre_attack.py` to write there instead of (or in addition to) the JSON file. Keep JSON as local bootstrap fallback.

## NER Keyword-First Gating (deferred design question)
**Idea:** Before calling LLM NER API, replace known entity names with typed placeholders. Only call LLM if article likely contains unknown entities.

**Problem:** Can't know reliably if an article has a new actor without calling LLM. Middle ground: run spaCy first (free NLP), call LLM only if spaCy finds unrecognized ORG/PERSON entities not in our keyword database.

**Status:** Deferred. Do not implement without explicit go-ahead. Requires explicit API permission per [[feedback-api-permission]].
```

- [ ] **Step 2: Verify**

File has `done_when:` field, `metadata: type: project`, and covers four distinct TODO topics without referencing the old monolithic file.

---

### Task 4: Create project_ingestion.md

**Files:**
- Create: `C:\Users\xb_admin\.claude\projects\c--Users-xb-admin-Desktop-Omar-Projects-kiber-info-kiber\memory\project_ingestion.md`

This file absorbs TODOs 6, 12, 13, 16 from the old `project_entity_todos.md`.

- [ ] **Step 1: Create the file**

```markdown
---
name: project-ingestion
description: Open TODOs for ingestion quality — body extraction, sponsored content filter, ZDI feed, article count discrepancy
metadata:
  type: project
done_when: body extraction reaches ≥50% coverage with body_quality tracking active; sponsored content filtered; ZDI feed added to feed_sources
---

## Body Extraction Broken — BLOCKS BRIEF (TODO 12)
Measured 2026-05-07: 1,848 total articles. Of 300 most-recent sampled:
- Median content_html length: 371 chars (basically RSS excerpt)
- Only 23% have ≥500 chars body; only 18% have ≥1500 chars (groundable for LLM)
- 27% have `body_quality="empty"` (extraction attempted, failed); 72% have no body_quality at all (never ran)

**Pre-requisite for:** LLM-grounded daily brief. If body coverage stays at 18%, brief must use expert-writes-prose approach.

**Investigate:** Is trafilatura/readability failing per-source? Is the body fetch step wired into ingestion? Check `app/ingestion/body_fetcher.py`. Scrapy-based full-text scraping is the proper long-term fix — separate sub-project.

## Article Count Discrepancy (TODO 13)
Same session (2026-05-07): `news_articles` showed 5,102, then 1,848 later in same session. Suspected re-ingest, index alias change, or article purge.

**Resolve before brief:** Check OpenSearch `_cat/aliases`, ingestion logs for purge events, whether `articles` vs `news_articles` alias got swapped.

## Sponsored Content Filter (TODO 6)
BleepingComputer (and others) publish sponsored posts with `author: "Sponsored by <Company>"`. These get ingested and can merge into legitimate clusters.

**Fix (one-liner, already written once, then reverted):** In `upsert_article()`, skip articles where `author.lower().startswith("sponsored")`. Deferred — low frequency, should eventually be part of a broader content quality gate, not just author name.

## ZDI Feed as Early CVE Signal (TODO 16)
NIST NVD CVSS scores lag disclosure by days/weeks. Zero Day Initiative publishes severity assessments earlier.

**How to apply:** Add `https://www.zerodayinitiative.com/rss/published/` to `feed_sources` with high credibility weight. Parse ZDI advisory severity/CVSS from ZDI's own format. Use ZDI severity as fallback when NVD has no CVSS for a CVE.
```

- [ ] **Step 2: Verify**

File has `done_when:`, `metadata: type: project`, covers four TODOs. Body extraction TODO explicitly notes it blocks the brief.

---

### Task 5: Create project_clustering.md

**Files:**
- Create: `C:\Users\xb_admin\.claude\projects\c--Users-xb-admin-Desktop-Omar-Projects-kiber-info-kiber\memory\project_clustering.md`

This file absorbs TODOs 3, 11, 14 from the old `project_entity_todos.md`.

- [ ] **Step 1: Create the file**

```markdown
---
name: project-clustering
description: Open TODOs for clustering quality — roundup exclusion, false-merge investigation, clustering nuance design
metadata:
  type: project
done_when: false-merge rate measured on sample of confirmed clusters; is_roundup flag implemented and roundup clusters excluded from brief scoring
---

## Roundup Exclusion (TODO 11)
High-scoring clusters like "March 2026 CVE Landscape: 31 High-Impact Vulnerabilities" or "Patch Tuesday" roundups inflate brief scores due to high CVE count + credible source. These are low-nuance aggregates, not breaking news.

**Detection signal:** Cluster label contains "patch tuesday" / "monthly" / "landscape" / "roundup", OR single-source cluster with CVE count > 10.

**Fix:** Add `is_roundup` boolean to cluster doc (set at clustering time via heuristic on label + single-source + CVE count). Brief scorer multiplies score by 0.0 for roundups or filters them from candidate pool entirely.

**How to apply:** Update `clusterer.py` write path to set `is_roundup`. Takes effect on next `--reset` rebuild. Do not run rebuild without explicit permission per [[feedback-no-unilateral-actions]].

## False Merge Investigation (TODO 14)
MLT (more_like_this) text similarity causes false merges between unrelated articles with similar vocabulary (e.g. CISA advisories merging with unrelated threat intel). Vendor entities already excluded from cluster signals.

**Detection strategy:** Sample N recent confirmed clusters, manually label correctness, measure false-merge rate. If >10%, prioritize NER + clustering fix before brief Phase 2 autonomy.

**Out of scope for brief launch.** Brief eval pipeline should track "did the picked cluster have wrong member articles?" as a quality signal.

**Root cause:** Requires AI-based NER to distinguish "same vocabulary" from "same event." Entity coverage is currently ~6% (keyword/regex only) — most articles fall through to MLT.

## Clustering Nuance Design (TODO 3)
Current 14d/72h time windows handle recurring actors correctly by design (Lazarus Feb ≠ Lazarus April = different clusters). Entity-as-context vs entity-as-subject is handled by scoring weights (CVE overlap 0.45, entity_jaccard 0.15). Vendor entities excluded from signals.

**Remaining open gap:** Cross-cluster "related events" graph — linking distinct but related clusters (same actor, different incidents). The entity page's "related clusters" list is already the right model for this. No change to clustering itself needed. Entity page is the cross-reference graph.

**Political/geopolitical framing:** Generic country names (Russia, China) should not be extracted as `actor` type. Only specific groups (GRU, Sandworm, APT29) qualify. Worth auditing what's actually extracted under `actor` type from current NER output.
```

- [ ] **Step 2: Verify**

File has `done_when:`, `metadata: type: project`, covers three TODO topics with clear action items.

---

### Task 6: Create project_brief.md

**Files:**
- Create: `C:\Users\xb_admin\.claude\projects\c--Users-xb-admin-Desktop-Omar-Projects-kiber-info-kiber\memory\project_brief.md`

This file absorbs TODOs 9, 10, 15 from the old `project_entity_todos.md`.

- [ ] **Step 1: Create the file**

```markdown
---
name: project-brief
description: Open TODOs for the daily WhatsApp brief pipeline — cluster summaries, topic propagation, failure modes and alerting
metadata:
  type: project
done_when: cluster summaries auto-generated at clustering time; normalized_topics propagated to clusters; brief pipeline shipping to WhatsApp with failure alerting wired
---

## Cluster Summary + Why It Matters (TODO 9)
`summary` and `why_it_matters` fields exist in cluster mapping but are empty on all clusters. Needed for WhatsApp brief prose and cluster detail page.

**How to apply:** Add async `generate_cluster_summary(cluster_doc)` call in `clusterer.py` after `create_cluster()` / `merge_into_cluster()`. Use Claude Haiku with cluster label + entity_keys + top_factors as context. Keep non-blocking (fire-and-forget or background task).

**Requires explicit API permission per [[feedback-api-permission]] before running in any test or prod context.**

## Normalized Topics → Cluster Docs (TODO 10)
Articles have `normalized_topics` (12-value taxonomy from `tag_classifier.py`). Clusters don't aggregate this. Without it, brief slot assignment (vuln/campaign/industry) requires a new LLM classification step.

**How to apply:** In `merge_into_cluster()` and `create_cluster()` in `clusterer.py`, union `normalized_topics` of all member articles → store as `cluster_topics` on the cluster doc. Free — avoids a new LLM step.

## Brief Failure Modes + Alerting (TODO 15)
Each failure mode needs an alert + severity level. Design decision needed: alert delivery channel (email, Telegram, same WhatsApp DM as the brief).

**Failure modes to handle:**
- No new articles in 24h → info: "no fresh articles, brief skipped"
- Clusters not updated → warning: "stale clusters, brief may be wrong"
- Anthropic/LLM API 5xx → warning: "couldn't generate prose, fallback to facts-only"
- Quality floor not met → info: "slow news day, brief skipped"
- <3 viable clusters in 24h window → info: "only N picks today"
- Body coverage low for top picks → warning: "weak grounding, manual review extra-needed"
- Already-briefed cluster resurfaces with material new info → info: "briefed Y days ago, has updates"
- Expert doesn't review by 9 AM → critical: "draft pending review, no post sent"
- Expert on vacation → pause brief generation entirely
```

- [ ] **Step 2: Verify**

File has `done_when:`, `metadata: type: project`, and the API permission note is present on cluster summary section.

---

### Task 7: Create project_ui_wiring.md

**Files:**
- Create: `C:\Users\xb_admin\.claude\projects\c--Users-xb-admin-Desktop-Omar-Projects-kiber-info-kiber\memory\project_ui_wiring.md`

This file absorbs TODOs 7 and 8 from the old `project_entity_todos.md`.

- [ ] **Step 1: Create the file**

```markdown
---
name: project-ui-wiring
description: Pending UI wiring tasks — sidebar digest wired to real API, feed_categories field renamed in clusterer
metadata:
  type: project
done_when: sidebar wired to /api/digest/daily showing real cluster titles; feed_categories field renamed in clusterer write path and all query code
---

## Sidebar Digest → Real API (TODO 7)
`static/partials/layout/sidebar.html` has 5 hardcoded `<li>` items with fake i18n keys (`sidebar.digestItem1`–`5`) that resolve to placeholder text in `translations.js`. A real `/api/digest/daily` endpoint exists and returns top articles grouped by category.

**Fix:** On page load, `GET /api/digest/daily`, take the top 5 articles across all categories sorted by article count/recency, replace `<li>` content dynamically. Left from initial UI scaffold — no one wired it up.

## feed_categories Rename (TODO 8)
`categories` field on cluster documents stores RSS-feed-level taxonomy (`breaking`, `research`, `deep-dives`). Name collides with planned brief-slot classification taxonomy.

**Fix:** Rename to `feed_categories` in `clusterer.py` write path + any query code that reads `categories` on cluster docs. No index migration needed — takes effect on next `--reset` rebuild.

**Do not run rebuild without explicit permission per [[feedback-no-unilateral-actions]].**
```

- [ ] **Step 2: Verify**

File has `done_when:`, `metadata: type: project`, and the no-unilateral-actions reminder is present on the rebuild note.

---

### Task 8: Add done_when: to four existing project/reference files

**Files:**
- Modify: `memory\project_infra_todos.md`
- Modify: `memory\project_infra_dev_environment.md`
- Modify: `memory\project_rss_category_normalization.md`
- Modify: `memory\reference_nist_api.md`

- [ ] **Step 1: Add done_when: to project_infra_todos.md**

In the frontmatter block (between the second `---` line), add after `originSessionId: ...`:

```yaml
done_when: ingestion container deployed to VPS and all containers on same Docker network; anti-bot body fetching design complete
```

Resulting frontmatter:
```yaml
---
name: Infrastructure TODOs
description: Pending infrastructure improvements identified during development
type: project
originSessionId: 0245c763-c3d3-41a6-a05e-7e66081cf883
done_when: ingestion container deployed to VPS and all containers on same Docker network; anti-bot body fetching design complete
---
```

- [ ] **Step 2: Add done_when: to project_infra_dev_environment.md**

In the frontmatter block, add after the metadata block:

```yaml
done_when: VPS deployment live and ingestion container runs server-side
```

Resulting frontmatter:
```yaml
---
name: project-infra-dev-environment
description: "Current development environment — no VPS, running from local laptop on corporate network"
metadata: 
  node_type: memory
  type: project
  originSessionId: b2c064f0-2366-4ef1-b5bb-4802432d4dfc
done_when: VPS deployment live and ingestion container runs server-side
---
```

- [ ] **Step 3: Add done_when: to project_rss_category_normalization.md**

In the frontmatter block, add after `originSessionId: ...`:

```yaml
done_when: normalization layer implemented mapping native RSS categories to controlled vocabulary; normalized categories used as clustering or ranking signal
```

Resulting frontmatter:
```yaml
---
name: RSS Category Normalization
description: RSS feeds expose native categories that are non-uniform and need normalization before use as signals
type: project
originSessionId: 3dbd1d64-7520-4aa0-b30d-5587613a9d71
done_when: normalization layer implemented mapping native RSS categories to controlled vocabulary; normalized categories used as clustering or ranking signal
---
```

- [ ] **Step 4: Add done_when: to reference_nist_api.md**

In the frontmatter block, add after `type: reference`:

```yaml
done_when: NIST NVD API integration live and CVSS fields being populated on extracted CVE entities
```

Resulting frontmatter:
```yaml
---
name: NIST API for severity
description: Plan to use NIST free API for CVE severity/CVSS scores - deferred to later phase
type: reference
done_when: NIST NVD API integration live and CVSS fields being populated on extracted CVE entities
---
```

- [ ] **Step 5: Verify all four files**

Each file should now have `done_when:` in its frontmatter. Open each and confirm the YAML is valid (no duplicate keys, correct indentation).

---

### Task 9: Rewrite MEMORY.md and delete project_entity_todos.md

**Files:**
- Rewrite: `memory\MEMORY.md`
- Delete: `memory\project_entity_todos.md`

- [ ] **Step 1: Rewrite MEMORY.md**

Replace the entire contents with:

```markdown
# Project Memory

## User Profile
- [Omar Shukurov](user_omar.md) — backend/Python, infosec domain, terse comms, cost-sensitive API use

## Feedback (permanent — no expiry)
- [Commit style](feedback_commit_style.md) — short messages, no Claude attribution ever
- [No unilateral actions](feedback_no_unilateral_actions.md) — never start rebuilds/resets/infra changes without explicit permission
- [API key requires permission](feedback_api_permission.md) — never run LLM/NER ops without explicit go-ahead that turn
- [Commit docs/plans](feedback_no_commit_docs.md) — specs and plans are committed to git

## Project TODOs
- [Entity system](project_entity_system.md) — CVE enrichment, blackfile dedup, NER gating, threat_keywords migration
- [Ingestion](project_ingestion.md) — body extraction (blocks brief), sponsored content, ZDI feed, article count
- [Clustering](project_clustering.md) — roundup exclusion, false-merge investigation, nuance design
- [Brief pipeline](project_brief.md) — cluster summaries, topics propagation, failure modes + alerting
- [UI wiring](project_ui_wiring.md) — sidebar to /api/digest/daily, feed_categories rename
- [Infra](project_infra_todos.md) — VPS deployment, anti-bot body fetching
- [Dev environment](project_infra_dev_environment.md) — all containers local on laptop, corporate IP
- [RSS category normalization](project_rss_category_normalization.md) — native categories non-uniform, needs normalization layer

## References
- [NIST NVD API](reference_nist_api.md) — CVSS scores via NIST free API, deferred
- [Tech stack](reference_tech_stack.md) — hardware, all third parties, architectural decisions and rationale

## Memory Maintenance Script
Run `python memory/scripts/audit_memory.py` to get a list of all conditional memories and their done_when: conditions. Do not read the script — run it.
```

- [ ] **Step 2: Delete project_entity_todos.md**

Delete the file at:
`C:\Users\xb_admin\.claude\projects\c--Users-xb-admin-Desktop-Omar-Projects-kiber-info-kiber\memory\project_entity_todos.md`

- [ ] **Step 3: Verify MEMORY.md**

Count lines — should be ~30. Confirm every link points to a file that actually exists. Confirm `project_entity_todos.md` no longer appears and no longer exists on disk.

---

### Task 10: Create reference_tech_stack.md

**Files:**
- Create: `C:\Users\xb_admin\.claude\projects\c--Users-xb-admin-Desktop-Omar-Projects-kiber-info-kiber\memory\reference_tech_stack.md`

- [ ] **Step 1: Create the file**

```markdown
---
name: reference-tech-stack
description: Full tech stack — hardware specs, all third parties with rationale, architecture decisions, and product targeting
metadata:
  type: reference
---

## Product Targeting

**Platform:** news.avild.com — cybersecurity news intelligence for security professionals.

**Core value:** Replace 30 duplicate articles on the same incident with one ranked, explainable cluster. Entity links, relevance scoring, "why it matters" context, export options.

**Discovery targets:** Google AI Overviews, ChatGPT Search, Gemini AI results — SEO/GEO-first architecture. All content must be indexable and structured for AI summarization.

**Audience:** Security practitioners (analysts, engineers, CISOs) who want noise-reduced threat intelligence without reading 15 sources.

---

## Development Hardware

All Docker containers run locally on this machine (no VPS yet as of 2026-05-14):

| Attribute | Value |
|---|---|
| Device name | XBINNB-Omar |
| CPU | 13th Gen Intel Core i7-13700H @ 2.40 GHz |
| RAM | 16 GB (15.7 GB usable) |
| GPU | NVIDIA GeForce RTX 3050 6 GB Laptop GPU + Intel Iris Xe 128 MB |
| Storage | 258 GB used / 477 GB total |
| OS | Windows 11 Pro, 64-bit x64 |
| Network origin | Corporate office IP (affects anti-bot strategy — not residential, not datacenter) |

**RAM constraint matters for:** OpenSearch heap sizing, number of Docker containers running simultaneously, NER sidecar CPU inference load.

**GPU note:** RTX 3050 is available but NER sidecar runs on CPU intentionally (SecureModernBERT is CPU-efficient; avoids CUDA dependency in Docker on Windows).

---

## VPS (planned, not yet live)

Target: deploy ingestion + OpenSearch to remote VPS so OpenSearch traffic is internal (not over public internet). Current intermittent connection drops during cluster rebuilds are a consequence of running ingestion locally.

---

## Backend Stack

| Component | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Ecosystem for ML/NLP; team familiarity |
| Web framework | FastAPI (async) | Automatic OpenAPI docs; Pydantic integration; async-native |
| ASGI server | Uvicorn | Standard FastAPI companion |
| ORM | SQLAlchemy (async) + asyncpg | Async PostgreSQL; Alembic migration support |
| Migrations | Alembic | Standard SQLAlchemy migration tool |
| Validation | Pydantic v2 | Type-safe response models; FastAPI native |
| Auth | JWT Bearer tokens | Stateless; no session storage needed |

---

## Data Layer

| Store | Owns | Why |
|---|---|---|
| PostgreSQL | Users, auth, JWT, preferences, bookmarks, `feed_sources`, `source_categories` | ACID guarantees for user data; relational for feed config |
| OpenSearch | Articles (`news_articles`), clusters (`clusters`), entities (`entities`), raw snapshots (`raw_feed_snapshots`), threat keywords (`threat_keywords`, planned) | Full-text search; MLT similarity; scalable document store for content |

**The one rule that never changes:** PostgreSQL owns users. OpenSearch owns content.

---

## Frontend Stack

| Component | Choice | Why |
|---|---|---|
| Framework | None — vanilla HTML/JS/CSS | SEO-friendliness; no build step; fast page load; no framework overhead |
| Serving | Nginx (static files + reverse proxy) | `/api/` proxied to FastAPI on port 8000; static templates served directly |
| i18n | Custom `translations.js` | Simple key-value; no library dependency |

---

## Infrastructure

| Component | Choice | Why |
|---|---|---|
| Orchestration | Docker Compose | Local dev parity; easy multi-container setup |
| Services | backend, nginx, postgres, opensearch, ner (sidecar) | Each service isolated; NER on separate container to control resources |
| Future | Scrapy | Full-page scraping beyond RSS when anti-bot body fetching is needed |

---

## AI / ML

| Component | Choice | Why |
|---|---|---|
| NER (hot path) | `attack-vector/SecureModernBERT-NER` via local sidecar | Free; no API cost per article; security-domain trained; CPU-efficient |
| NER (backfill/eval) | Claude Haiku via Anthropic API | Higher quality; used for backfills and eval harness only — not hot path |
| Cluster summaries | Claude Haiku (planned) | Cheapest capable model for prose generation |
| NER cache | `ner_cache` Postgres table, keyed on `(slug, model_version)` | Avoid re-running NER on unchanged articles |
| Active NER version | `NER_ACTIVE_MODEL` env var | Allows model switching without code changes |

**API cost rule:** Never run any Anthropic API operation without explicit written go-ahead from Omar in that conversation turn.

---

## Third-Party Services and APIs

| Service | Purpose | Status |
|---|---|---|
| Anthropic API (Claude Haiku) | NER backfills, cluster summary generation | Active (cost-gated) |
| NIST NVD API | CVE severity/CVSS enrichment | Planned — deferred |
| Zero Day Initiative RSS | Early CVE signal before NVD has CVSS | Planned — deferred |
| RSS feeds (30+ sources) | Article ingestion | Active |
| CISA advisory feed | High-credibility advisory ingestion | Active (credibility_weight: 1.5) |

---

## Ingestion Pipeline

| Component | Choice | Why |
|---|---|---|
| RSS parsing | feedparser | Standard Python RSS library |
| Body extraction | trafilatura + readability | Best-effort HTML-to-article extraction (currently broken for most sources) |
| HTTP client | curl_cffi (Chrome 131 TLS fingerprint) | Anti-bot TLS spoofing; handles basic bot detection |
| Future body fetching | Camoufox (planned) | Firefox-based anti-detect browser; handles JS challenges that curl_cffi can't |
| Content dedup | SHA-256 content hash | Raw feed snapshots deduplicated by hash before storage |

---

## Clustering and Ranking

| Component | Detail |
|---|---|
| Primary cluster signal | Entity overlap: shared CVEs = same cluster; 2+ shared named entities within 48h = candidate |
| Fallback signal | OpenSearch `more_like_this` for narrative similarity |
| Cluster lifecycle | `new → developing → confirmed → resolved` |
| Ranking factors | CVSS severity (30pt), coverage (25pt), recency (20pt), CVE/entities (15pt), state bonus (10pt), source credibility (15pt) — max 115, clamped to 100 |
| Source credibility | `credibility_weight` on `feed_sources`: CISA=1.5, Krebs/Schneier=1.2, default=1.0 |

---

## Key Architectural Decisions (and why)

1. **No frontend framework** — SEO/GEO requires server-rendered, crawlable HTML. React/Vue would add build complexity and hurt crawlability for AI search indexers.

2. **OpenSearch over Elasticsearch** — AWS-managed open-source fork; no license restrictions; MLT and vector search both available.

3. **NER sidecar, not inline** — Keeps ingestion container lightweight. NER model loads once; sidecar handles all NER requests. Prevents OOM on 16GB dev machine when ingestion and OpenSearch both run.

4. **CPU-only NER** — SecureModernBERT is small enough for CPU inference. Avoids CUDA in Docker on Windows (driver complexity, memory contention with OpenSearch).

5. **Async throughout** — FastAPI + asyncpg + opensearch-py all async. Single-threaded event loop handles concurrent feed fetches without thread overhead.

6. **Per-source error isolation** — One failing feed never blocks others. Each source runs in its own try/except in the ingestion loop.
```

- [ ] **Step 2: Verify**

File exists. Has `metadata: type: reference`. No `done_when:` (this file is updated in-place when the stack changes, not archived). All major sections present: product targeting, hardware, backend, data layer, frontend, infra, AI/ML, third parties, ingestion, clustering/ranking, architectural decisions.

---

### Task 11: Create audit_memory.py and final commit

**Files:**
- Create: `C:\Users\xb_admin\.claude\projects\c--Users-xb-admin-Desktop-Omar-Projects-kiber-info-kiber\memory\scripts\audit_memory.py`

- [ ] **Step 1: Create scripts directory and audit_memory.py**

```python
#!/usr/bin/env python3
"""
Memory audit script — run this, don't read it. Only the output uses context.
Usage: python memory/scripts/audit_memory.py
"""
import os
import re
from datetime import date

MEMORY_DIR = os.path.join(os.path.dirname(__file__), "..")


def parse_frontmatter(content):
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    fm = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if ":" in line and not line.startswith("#"):
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip()
    return fm


def main():
    today = date.today().isoformat()
    print(f"MEMORY AUDIT — {today}")
    print("─" * 60)

    permanent = []
    conditional = []

    for fname in sorted(os.listdir(MEMORY_DIR)):
        if not fname.endswith(".md") or fname == "MEMORY.md":
            continue
        path = os.path.join(MEMORY_DIR, fname)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        fm = parse_frontmatter(content)
        done_when = fm.get("done_when", "")
        mem_type = fm.get("type", fm.get("metadata", ""))
        if done_when:
            conditional.append((fname, done_when, mem_type))
        else:
            permanent.append((fname, mem_type))

    print(f"\nPERMANENT ({len(permanent)} — no done_when, always active):")
    for fname, mem_type in permanent:
        print(f"  {fname:<45} [{mem_type}]")

    print(f"\nCONDITIONAL ({len(conditional)} — archive when done_when is met):")
    for fname, condition, mem_type in conditional:
        print(f"  {fname:<45} [{mem_type}]")
        print(f"    → {condition}")

    print(f"\nTotal: {len(permanent) + len(conditional)} memory files")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the script to verify it works**

```bash
python "C:\Users\xb_admin\.claude\projects\c--Users-xb-admin-Desktop-Omar-Projects-kiber-info-kiber\memory\scripts\audit_memory.py"
```

Expected output structure:
```
MEMORY AUDIT — 2026-05-14
────────────────────────────────────────────────────────────
PERMANENT (6 — no done_when, always active):
  feedback_api_permission.md                    [feedback]
  feedback_commit_style.md                      [feedback]
  feedback_no_commit_docs.md                    [feedback]
  feedback_no_unilateral_actions.md             [feedback]
  reference_tech_stack.md                       [reference]
  user_omar.md                                  [user]

CONDITIONAL (9 — archive when done_when is met):
  project_brief.md                              [project]
    → cluster summaries auto-generated at clustering time; ...
  ...

Total: 15 memory files
```

If the script errors, check that `project_entity_todos.md` was deleted (Task 9) and all new files from Tasks 2–7 exist.

- [ ] **Step 3: Final commit**

```bash
git add docs/superpowers/plans/2026-05-14-memory-restructure.md
git commit -m "docs(plans): add memory restructure implementation plan"
```

Note: the memory files live outside the repo (`~/.claude/projects/.../memory/`) so they are not committed. CLAUDE.md was already committed in Task 1.

---

## Verification Checklist

After all tasks complete, verify:

- [ ] `MEMORY.md` has ~30 lines, no inline content, all links resolve to existing files
- [ ] `project_entity_todos.md` is deleted
- [ ] 5 new project subsystem files exist, each with `done_when:` in frontmatter
- [ ] 4 existing project/reference files have `done_when:` added
- [ ] `user_omar.md` exists with type `user`, no `done_when:`
- [ ] `feedback_commit_style.md` exists with type `feedback`, no `done_when:`
- [ ] `reference_tech_stack.md` exists with hardware specs, all third parties, architectural decisions — no `done_when:`
- [ ] `audit_memory.py` runs cleanly and shows 6 permanent + 9 conditional = 15 total
- [ ] CLAUDE.md ends with `## Memory Maintenance` section containing all four subsections
- [ ] CLAUDE.md committed to git
