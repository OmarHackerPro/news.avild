# Branch Reconciliation Plan — `main` ↔ `wip-body-extraction-frontend-followup`

- **Date:** 2026-05-22
- **Status:** Plan only — execute nothing until reviewed and approved.
- **Goal:** Make `main` the single canonical branch containing everything, then retire
  `wip-body-extraction-frontend-followup`. No force-push, no history rewrite.

---

## Situation

A concurrent session rewrote git history. As a result `main` and
`wip-body-extraction-frontend-followup` *appear* diverged 45/47, but that count is
almost entirely the **same content with different SHAs** from the rewrite.

`git cherry -v main wip-body-extraction-frontend-followup` was run to find what is
*genuinely* unique by content (patch-id). Result:

**`main` already contains, by content, all of:** the EPSS feature, the frontend
prod-readiness work, the body-extraction entity work, the `cve_topics` /
`os_write_once` infrastructure, and the unified_scorer fix.

**Genuinely unique to `wip` (NOT in `main`):**

| Commit | What |
|---|---|
| `eb454f7` | `wip: save body extraction and frontend follow-up work` |
| `4b732bf` | `fix: null-guard cve_topic upsert script for enricher-created docs` |
| `403dc77` | `fix: raise embedder client timeouts for single-threaded CPU encoder` |
| (uncommitted) | `scripts/backfill_body_extraction.py`, `scripts/enrich_cve_nvd.py`, `scripts/rebuild_all.py`, `scripts/refresh_epss.py`, `tests/test_rebuild_all.py` (concurrent agent's progress-bar work) + `.gitignore` (rebuild-log ignore rule) |

**Deliberately skipped:** wip commit `9afecfd` (`test(entity_extractor): add tests
for product pattern rebuild and vendor warning`) — `main` already has the
equivalent test work (commits `d8c7d08` / `4081756`). It only shows as "unique"
because of CRLF/LF line-ending differences. Do not cherry-pick it.

**Out of scope:** other branches and worktrees (`feature/body-extraction`,
`claude/wizardly-wescoff`, `feat/clustering-*`, `front-improvement`, etc.). This
plan covers only `main` ↔ `wip-body-extraction-frontend-followup`. If
`feature/body-extraction` holds unmerged work, reconcile it separately.

## Decisions baked into this plan

- **Embedder timeout (`403dc77`)** — KEEP it. The embedder is single-threaded by
  design (CPU ML model); client concurrency cannot change throughput, so a
  realistic timeout is the correct fix. `403dc77` is cherry-picked as-is.
- **Strategy** — cherry-pick onto `main`. It is additive: a normal `git push`,
  no force-push, no rewrite of any shared branch. `origin/wip-...` stays intact
  as a safety net until the final step.

## Pre-flight (decide before executing)

1. **Concurrent agents.** Confirm no other agent is mid-task on this repo.
   Reconciling while another session commits/switches branches is unsafe.
2. **The 6 uncommitted files.** 5 are the concurrent agent's progress-bar work;
   `.gitignore` is the rebuild-log ignore rule. They must be committed before
   reconciliation or they will be left behind. **Decision needed:** commit them
   on `wip` as one `chore:` commit (this plan assumes yes — Step 1), or have the
   other agent finish/commit them first.
3. **Working tree clean otherwise?** `git status` should show only those 6 files
   before starting.

## Steps

> Run from the repo root. Stop at the first unexpected error.

- [ ] **Step 1 — Commit the uncommitted work on `wip`.**
  Currently on `wip-body-extraction-frontend-followup`.
  ```
  git add .gitignore scripts/backfill_body_extraction.py scripts/enrich_cve_nvd.py \
          scripts/rebuild_all.py scripts/refresh_epss.py tests/test_rebuild_all.py
  git commit -m "chore: non-TTY progress output for rebuild scripts; ignore rebuild logs"
  ```
  Record the new SHA — call it `<WIP_PROGRESS>`. `git status` must now be clean.

- [ ] **Step 2 — Switch to `main` and confirm it is current.**
  ```
  git checkout main
  git status            # must be clean
  git log --oneline -1  # expect 70c09b3 (or newer if main moved — if so, STOP and re-plan)
  ```

- [ ] **Step 3 — Cherry-pick the body/frontend follow-up work.**
  ```
  git cherry-pick eb454f7
  ```
  Conflicts are most likely here (`eb454f7` is a large squashed commit whose
  parent was the pre-rewrite EPSS tip). See "Conflict resolution" below.

- [ ] **Step 4 — Cherry-pick the cve_topic fix.**
  ```
  git cherry-pick 4b732bf
  ```
  Expected clean — touches only `app/ingestion/cve_topic_manager.py` and
  `tests/test_cve_topic_manager.py`.

- [ ] **Step 5 — Cherry-pick the embedder timeout fix.**
  ```
  git cherry-pick 403dc77
  ```
  Expected clean — touches only `app/ingestion/embedding_client.py`.

- [ ] **Step 6 — Cherry-pick the progress-bar / gitignore commit.**
  ```
  git cherry-pick <WIP_PROGRESS>
  ```
  Possible conflicts on `scripts/rebuild_all.py` / `refresh_epss.py` /
  `enrich_cve_nvd.py` if `main`'s versions differ.

- [ ] **Step 7 — Verify.**
  ```
  python -m pytest tests/ -q
  ```
  Expect only the ~19 pre-existing failures already documented on `main`
  (tag_classifier, cluster_cache, unified_scorer, reparse_snapshots,
  briefing/sender). No NEW failures. If new failures appear, a cherry-pick
  resolution was wrong — fix before pushing.

- [ ] **Step 8 — Push `main` (normal push, no force).**
  ```
  git push origin main
  ```

- [ ] **Step 9 — Retire `wip` (only after Step 8 succeeds and `main` is verified).**
  ```
  git branch -D wip-body-extraction-frontend-followup
  git push origin --delete wip-body-extraction-frontend-followup
  ```

## Conflict resolution

- `eb454f7` (Step 3) is the real risk. Likely conflict files: anything where the
  body/frontend follow-up overlaps EPSS-touched files (`app/api/routes/clusters.py`,
  `app/models/cluster.py`, templates, static JS/CSS).
- For each conflict: `main`'s side already has the rewritten EPSS content; take
  `main`'s version of EPSS-specific lines and `eb454f7`'s version of
  body/frontend-specific lines. When unsure, inspect both with
  `git show eb454f7 -- <file>` and `git show HEAD -- <file>`.
- After resolving: `git add <files>` then `git cherry-pick --continue`.
- To abort cleanly at any point: `git cherry-pick --abort`.

## Rollback

- Until Step 9, `origin/wip-body-extraction-frontend-followup` is untouched — it
  is the full backup. If anything goes wrong, `git checkout
  wip-body-extraction-frontend-followup` returns to the known-good state.
- `main` before reconciliation is `70c09b3`; `git reflog` and `git reset --hard
  70c09b3` restore it (do this only before Step 8 push).
- Do not run Step 9 until `main` is pushed and confirmed correct.

## Risks

- **Cherry-pick conflicts on `eb454f7`** — squashed commit, pre-rewrite parent.
  Budget time for manual resolution.
- **Concurrent agent** — if another session touches `main` or `wip` during this,
  abort and re-plan.
- **`eb454f7` lands as one opaque "wip: save..." commit** on `main`. Acceptable;
  optionally squash/reword later. Not worth blocking on.
- **The 6 uncommitted files are another agent's in-flight work** — confirm that
  agent is done with them before Step 1 commits them.
