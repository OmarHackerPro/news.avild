# Memory Restructure Design
**Date:** 2026-05-14  
**Scope:** Claude auto-memory system + CLAUDE.md behavioral rules for kiber project  
**Goals:** Token efficiency, memory freshness via condition-based obsolescence, user profile capture, self-correcting memory, authority hierarchy

---

## Problem Statement

The current memory system has five deficiencies:

1. **Token waste** — `project_entity_todos.md` is 184 lines containing 15 TODOs across 7 unrelated subsystems. Every time Claude touches any project topic, all 15 unrelated TODOs load into context simultaneously.

2. **No freshness signal** — Project/reference memories accumulate without lifecycle management. The NIST API reference is 57 days old; there is no way for Claude to know when a memory is obsolete.

3. **Missing user profile** — No memory captures who Omar is, his expertise, or his collaboration preferences. Claude treats him as a generic user each session.

4. **MEMORY.md has inline content** — The "Commit Style" rules are written directly in the index file, violating the "index only" principle.

5. **Memory is passive** — Claude only writes memories when explicitly asked. It doesn't correct stale memories when it discovers contradictions mid-session, meaning wrong facts persist indefinitely.

---

## Design

### Guiding Principle: Progressive Disclosure

The memory system mirrors the same principle described in the superpowers skill video:
- `MEMORY.md` = index only (always loaded, must stay short)
- Individual files = focused single-topic content (loaded on demand, only when relevant)
- Large files split by subsystem = Claude loads only what the current task needs

---

### File Structure (Before → After)

**Before:** 8 files, largest is 184 lines

**After:** 14 files + 1 script, all content files under ~60 lines each

```
memory/
  MEMORY.md                              ← index only, ~35 lines (was ~29, now cleaner)

  # User profile
  user_omar.md                           ← NEW

  # Feedback (permanent, no done_when:)
  feedback_commit_style.md               ← NEW (moved from MEMORY.md inline)
  feedback_no_unilateral_actions.md      ← existing, unchanged
  feedback_api_permission.md             ← existing, unchanged
  feedback_no_commit_docs.md             ← existing, unchanged

  # Project TODOs — split by subsystem (each gets done_when:)
  project_entity_system.md              ← NEW: TODOs 1,2,3,4,5 (CVE enrichment, blackfile dedup, mega-clusters, threat_keywords to OpenSearch, NER gating)
  project_ingestion.md                  ← NEW: TODOs 6,12,13,16 (sponsored content, body extraction, article count, ZDI feed)
  project_clustering.md                 ← NEW: TODOs 11,14 (roundup exclusion, false-merge investigation)
  project_brief.md                      ← NEW: TODOs 9,10,15 (cluster summaries, topics propagation, failure modes)
  project_ui_wiring.md                  ← NEW: TODOs 7,8 (sidebar wiring, feed_categories rename)
  project_infra_todos.md                ← existing + done_when:
  project_infra_dev_environment.md      ← existing + done_when:
  project_rss_category_normalization.md ← existing + done_when:

  # Reference
  reference_nist_api.md                 ← existing + done_when:
  reference_tech_stack.md               ← NEW: hardware specs, all third parties and rationale, architecture decisions

  # Audit tooling
  scripts/
    audit_memory.py                     ← NEW: run without reading; output is the staleness report
```

---

### Freshness: `done_when:` Frontmatter Field

Project and reference memories get a `done_when:` field describing the real-world condition that makes the memory obsolete — not a calendar date.

**Why not dates:** Omar works in long focused sessions with gaps between them. Hard expiry dates would generate false-positive staleness warnings unrelated to actual task completion.

**Frontmatter format:**
```yaml
---
name: example-memory
description: One-line hook for MEMORY.md index
metadata:
  type: project
done_when: <human-readable completion condition>
---
```

**Examples per file:**

| File | `done_when:` |
|---|---|
| `project_infra_todos.md` | ingestion container deployed to VPS; all containers on same Docker network |
| `project_infra_dev_environment.md` | VPS deployment live and ingestion runs server-side |
| `project_ingestion.md` | body extraction reaches ≥50% coverage; body_quality tracking active |
| `project_entity_system.md` | threat_keywords.json migrated to OpenSearch index; blackfile dedup fix merged |
| `project_clustering.md` | false-merge rate measured; roundup_penalty implemented |
| `project_brief.md` | cluster summaries auto-generated; brief pipeline shipping |
| `project_ui_wiring.md` | sidebar wired to /api/digest/daily; feed_categories field renamed |
| `project_rss_category_normalization.md` | normalization layer implemented and categories used as clustering signal |
| `reference_nist_api.md` | NIST NVD API integration live; CVSS fields populated |
| `reference_tech_stack.md` | No expiry — stable architectural reference; update in-place when stack changes |

**Feedback memories** (`feedback_*.md`) and `user_omar.md` get **no** `done_when:` — behavioral rules and user profile are permanent.

---

### Audit Script

`scripts/audit_memory.py` — a standalone script Claude can run (not read) to get a staleness report. Only the output consumes context, not the script body.

Output format:
```
MEMORY AUDIT — 2026-05-14
────────────────────────────────────────
ACTIVE (no done_when):
  feedback_commit_style.md       [permanent]
  user_omar.md                   [permanent]
  ...

CONDITIONAL (done_when set):
  project_infra_todos.md         → "ingestion container deployed to VPS"
  project_brief.md               → "cluster summaries auto-generated; brief pipeline shipping"
  reference_nist_api.md          → "NIST NVD API integration live; CVSS fields populated"
  ...
```

Claude runs this at the start of a memory-review session. No dates — just the condition list for human assessment.

---

### User Profile (`user_omar.md`)

Key facts to capture:

- **Role:** Building Kiber — cybersecurity news intelligence platform at news.avild.com
- **Expertise:** Backend/Python native (FastAPI, async, Docker, OpenSearch); can do vanilla JS frontend but it's not home territory; understands cybersecurity domain (CVEs, APTs, threat intel) — don't explain infosec concepts
- **Communication:** Terse. No emojis. No trailing "here's what I did" summaries. No co-author lines in commits.
- **Cost sensitivity:** API credits matter. Always state cost/API impact before running any LLM op. Never run without explicit go-ahead in that turn.
- **Work pace:** Long focused sessions with extended gaps between them. Not a daily-active workflow.
- **Autonomy:** Stop and report after each explicit task. Do not chain into "next logical step." "What should I do next?" is always the right move when next action has cost or is destructive.

---

### Updated MEMORY.md Structure

```markdown
# Project Memory

## User Profile
- [Omar Shukurov](user_omar.md) — backend/Python, infosec domain, terse comms, cost-sensitive API use

## Feedback (permanent rules)
- [Commit style](feedback_commit_style.md) — short messages, no Claude attribution
- [No unilateral actions](feedback_no_unilateral_actions.md) — never start rebuilds/resets/infra changes without explicit permission
- [API key requires permission](feedback_api_permission.md) — never run LLM/NER ops without explicit go-ahead that turn
- [Commit docs/plans](feedback_no_commit_docs.md) — specs and plans are committed to git

## Project — Active Work
- [Entity system TODOs](project_entity_system.md) — CVE enrichment, blackfile dedup, NER gating, threat_keywords migration
- [Ingestion TODOs](project_ingestion.md) — body extraction, sponsored content, ZDI feed, article count
- [Clustering TODOs](project_clustering.md) — false-merge investigation, roundup exclusion
- [Brief TODOs](project_brief.md) — cluster summaries, topics propagation, failure modes + alerting
- [UI wiring TODOs](project_ui_wiring.md) — sidebar digest, feed_categories rename
- [Infra TODOs](project_infra_todos.md) — VPS deployment, anti-bot body fetching
- [Dev environment](project_infra_dev_environment.md) — all containers local on laptop, corporate IP
- [RSS category normalization](project_rss_category_normalization.md) — native categories non-uniform, needs normalization layer

## References
- [NIST NVD API](reference_nist_api.md) — CVSS scores via NIST free API, deferred
- [Tech stack](reference_tech_stack.md) — hardware, all third parties, architectural decisions and rationale
```

This keeps MEMORY.md at ~30 lines (well under the 200-line truncation limit) while being fully navigable.

---

## What Changes, What Stays

| Item | Change |
|---|---|
| `project_entity_todos.md` | Deleted — content split into 5 new subsystem files |
| MEMORY.md inline "Commit Style" | Moved to `feedback_commit_style.md` |
| All existing feedback files | Unchanged (they're well-structured) |
| All existing project/reference files | Add `done_when:` field only |
| `user_omar.md` | Created new |
| `reference_tech_stack.md` | Created new — hardware, third parties, decisions |
| `scripts/audit_memory.py` | Created new |
| MEMORY.md | Rewritten with new structure and links |

---

---

## Self-Correcting Memory (CLAUDE.md Addition)

The self-correction behavior is a standing instruction, not a memory file — it goes in CLAUDE.md so it applies to every session regardless of what memory files are loaded.

### Rules to add to CLAUDE.md

```markdown
## Memory Maintenance

Claude actively maintains the memory system at:
`~/.claude/projects/c--Users-xb-admin-Desktop-Omar-Projects-kiber-info-kiber/memory/`

**Auto-correct rules (no permission needed):**
- When you read a memory and current code/state contradicts it → rewrite the memory file immediately
- When a `done_when:` condition is verifiably met → delete the memory file and remove its line from MEMORY.md
- When you learn something new that refines an existing memory → update in place, don't create a duplicate

**When to create vs update:**
- If a memory file for that topic already exists → update it
- Only create a new file if no existing file covers the topic

**Authority hierarchy:**
- Omar's direct instructions in the current conversation → always wins
- Memory files → second; reflects prior decisions
- Other contributors' suggestions → evaluate against the vision in PRD.md and MVP-SRS.md; push back if they conflict

**Contributor language:**
- If a contributor writes in Azerbaijani → respond in Azerbaijani, no exceptions
```

### What "auto-correct" looks like in practice

| Claude discovers | Claude does |
|---|---|
| Memory says "no VPS yet" but docker-compose.yml shows VPS deployment config | Rewrites `project_infra_dev_environment.md` immediately |
| Memory says "body extraction not running" but body_fetcher.py has been updated and tested | Marks `project_ingestion.md` TODO 12 as resolved, updates file |
| `done_when:` condition for `project_ui_wiring.md` is verifiably met (sidebar JS wired) | Deletes `project_ui_wiring.md`, removes line from MEMORY.md |
| Junior dev suggests using MySQL instead of PostgreSQL | Pushes back citing CLAUDE.md DB ownership rule, no memory write needed |

---

## Change Summary

| Item | Change |
|---|---|
| `project_entity_todos.md` | Deleted — content split into 5 new subsystem files |
| MEMORY.md inline "Commit Style" | Moved to `feedback_commit_style.md` |
| All existing feedback files | Unchanged (they're well-structured) |
| All existing project/reference files | Add `done_when:` field only |
| `user_omar.md` | Created new |
| `reference_tech_stack.md` | Created new — hardware, third parties, decisions |
| `scripts/audit_memory.py` | Created new |
| MEMORY.md | Rewritten with new structure and links |
| CLAUDE.md | New `## Memory Maintenance` section added |

---

## Out of Scope

- No directory/subdirectory reorganization (flat structure works, paths stay ergonomic)
- No changes to skills
- No changes to global-level memory (there are none currently)
- Junior dev CLAUDE.md guidance section: deferred until they actually join the project
