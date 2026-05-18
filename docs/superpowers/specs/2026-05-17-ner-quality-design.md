# NER Quality Improvement — Design Spec

_2026-05-17 — revised 2026-05-19_

## Problem

The entity extraction pipeline (SecureBERT sidecar + regex) produced three categories
of noise. Two have since been resolved at the root; one remains.

| Failure | Example | Status |
|---|---|---|
| Model artifact fragments | `cobalt-strik` alongside `cobalt-strike` | **RESOLVED 2026-05-19** — off-by-one bug fixed in `model.py` (see below) |
| Abbreviation/alias splits | `burp` vs `burp-suite` | **Already handled** by `_resolve_aliases()` + `entity_intel` (trusted-entity-tier spec) |
| Generic word FPs | `expand` (243 articles), `route`, `devices` | **Open** — the only remaining NER quality work |

CVE extraction is regex-only and excluded from NER quality scope.

---

## Resolved: model artifact fragments (off-by-one bug)

The fragmentation was never a model-quality, tokenization, or confidence issue. It was
a single off-by-one bug in `app/services/ner_sidecar/model.py`.

**Mechanism:** the model emits `B-` (not `I-`) on continuation words, so `_merge_bio()`
flushes each word as a separate entity and `_post_merge()` rejoins same-type adjacent
ones. Byte-BPE tokenization prepends a leading space to non-initial words, so a word
unit's char span includes that space. `_merge_bio()` set `char_offset` to the span
start (with the space) but set `name` to the `.strip()`-ed text (space removed). The
two were inconsistent by one character. `_post_merge()` then reconstructs spans as
`char_offset + len(name)` — short by one — so every merged multi-word entity lost its
final character: `Cobalt Strike` → `Cobalt Strik`, `Breeze Cache` → `Breeze Cach`.

**Fix:** in `_merge_bio()`'s `flush()`, advance `char_offset` past the stripped leading
whitespace so it always points at `name[0]`. Verified live: `Cobalt Strike`,
`Breeze Cache`, `Microsoft SharePoint Server` all extract whole.

**Lesson learned:** the original spec proposed a synonym map (Stage 1) and an
edit-distance/prefix dedup pass (Stage 2) to *clean up fragments after the fact*. Both
were the wrong layer — downstream fuzzy-merge cannot distinguish a genuine fragment
(`mistral-a` → `mistral-ai`) from two distinct entities sharing a stem
(`libssh`/`libssh2`, `firewall`/`firewalld`). Stage 1 was also dead code:
`_resolve_aliases()` already does DB-backed alias resolution. Both stages are deleted.
Fix the lever, not the symptom.

---

## Goals

- Best F1 per entity type, prioritising recall over precision (missing `lazarus-group`
  is worse than keeping `expand`).
- Exception: tool and product types — the noisiest; acceptable to be stricter.
- Measurable: every change must have a before/after F1 number against a human-labeled
  ground truth.

## Non-goals

- Hardcoded patches for specific strings.
- Fine-tuning the SecureBERT model.
- Source-level confidence weighting (deferred).

---

## Architecture

### Two tiers: trusted enumeration + NER discovery

- **Trusted tier — enumeration.** Regex (CVE/CWE/TTP) and seed-list lookup
  (`VENDOR_KEYWORDS`, `PRODUCT_KEYWORDS`, `threat_keywords.json`). Precision ~100%;
  recall capped by the lists. Kept as-is, no quality filtering.
- **Discovery tier — NER.** The SecureBERT sidecar finds entities not in any list —
  novel campaigns, malware families, products whose names collide with English words.
  Unverified; needs quality filtering.

The NER quality stages apply only to the discovery tier.

### Extraction pipeline (current code)

```
TRUSTED TIER (always kept, no filtering)
  article text → CVE/CWE/TTP regex
               → VENDOR_KEYWORDS / PRODUCT_KEYWORDS lookup
               → threat_keywords.json (known malware/actors)

DISCOVERY TIER (sidecar output, inside extract_entities())
  → _resolve_aliases()            (DB-backed alias resolution — already in code, line 560)
  → [Phase 2] mentions filter     (drop low-frequency generics — gated, see below)
  → [Phase 2] trusted/discovery split + per-type policy
  → merge into trusted tier
  → store
```

`_resolve_aliases()` already covers the abbreviation case (`burp` → `burp-suite`) via
rows in the `entity_intel` table — add rows as discovered during labeling, no code
change. The fragment-fix lives upstream in the sidecar, not in `extract_entities()`.

---

## Component 1 — Labeling pass + F1 measurement (`scripts/label_ner.py`)

**Purpose:** establish a human-labeled ground truth so every subsequent change has a
measured F1 delta.

**What gets labeled:**
- `only-local` rows from `ner_eval_judgments` (sidecar found, Haiku didn't) — TP vs FP.
- `only-haiku` rows (Haiku found, sidecar didn't) — genuine FN vs Haiku noise.

**Truncation-miss bucket.** Haiku only ever saw `title + summary[:500]`; the sidecar
sees the full body up to 4096 tokens. An `only-haiku` entity is only a real FN if it
falls inside the sidecar's input window. The eval/labeler must record whether the
entity's mention is within the sidecar's processed text — entities outside it are
classified `truncation-miss`, not FN, so threshold tuning is not driven by phantom
misses.

**Labeling UX:** CLI presents entity + article snippet showing the entity in context
(full body, not the truncated summary). User inputs `t` (TP), `f` (FP), or `s` (skip).
Writes verdict to `ner_eval_judgments.verdict`.

**F1 computation (per entity type):**
- TP = `both` (agreed) + `only-local` labeled TP
- FP = `only-local` labeled FP
- FN = `only-haiku` labeled TP **and** inside the sidecar input window
- (excluded: `only-haiku` entities outside the window → `truncation-miss`)

**Corpus selection (stratified, ~50 articles):** recall is priority #1, so do not pull
only the most FP-dense articles. Split ~25 `only-local`-heavy (FP signal) + ~25 random
(unbiased recall signal). Cover diverse sources (CISA, Securelist, Unit 42, Krebs,
PortSwigger) and content types.

---

## Component 2 — Threshold tuning (generic-word FPs)

The only remaining model-quality problem: the model genuinely reads common words
(`expand`, `route`, `devices`) as tools/products. There is no clean root-cause fix
short of fine-tuning (a non-goal). The honest framing: this is a precision/recall
tuning task, and the correct lever is the per-type confidence thresholds in
`model.py`:

```python
_CONFIDENCE_THRESHOLDS = {
    "PRODUCT": 0.5, "TOOL": 0.5, "CVE": 0.5,
    "MALWARE": 0.75, "THREAT-ACTOR": 0.75, "CAMPAIGN": 0.75,
}
```

Process:
1. Run the labeling pass → baseline F1 per type.
2. Adjust `_CONFIDENCE_THRESHOLDS` (tool/product are the noisy types — candidates for
   a higher bar).
3. Re-run NER backfill with `--force` on the labeled corpus, recompute F1.
4. Keep changes only where F1 improves; recall regressions on malware/actor/campaign
   are disqualifying.

---

## Phase 2 (gated) — mentions filter + trusted/discovery split

These are deferred until Component 1+2 land and produce baseline F1 numbers. They are
the only stages that *delete* discovery entities, so they carry recall risk and must
be measured in isolation.

### Mentions filter

Drop discovery entities that appear only once in the body. Generic words tend to
appear once; real tools/actors appear repeatedly. Gate on **mentions combined with
confidence** — a single-mention high-confidence CISA product must survive — not raw
count alone. `mentions` is already computed by `_dedup()` in `model.py`.

Justification: this is primarily entity-page / UI / SEO cleanliness. IDF weighting in
the clustering-quality spec already damps common entities in cluster scoring, so the
clustering benefit is modest. Ship it as its own change with its own F1 delta.

### Trusted/discovery split + per-type policy

For each surviving NER entity, check whether its `normalized_key` matches a
trusted-tier entity for the article. Match → drop the NER copy (trusted entity wins).
No match → apply per-type policy: `vendor` drop unless mentions ≥ 3; `product`,
`malware`, `actor`, `campaign` keep; `tool` strictest. See the original revision
history for the full rationale table.

---

## Implementation sequence

1. `scripts/label_ner.py` — labeling CLI + F1 computation (with truncation-miss bucket).
2. Baseline F1 measurement (label ~50 stratified articles).
3. Component 2: tune `_CONFIDENCE_THRESHOLDS`, re-measure F1.
4. Full NER backfill (`backfill_ner_sidecar.py --force`) — regenerates the entities
   index with the fragment fix applied and clears all historical fragments.
5. Phase 2 (gated on the above F1 numbers): mentions filter, then trusted/discovery
   split — each as a separate measured change.

---

## Files touched

| File | Change | Status |
|---|---|---|
| `app/services/ner_sidecar/model.py` | Off-by-one `char_offset` fix in `_merge_bio()` | **Done 2026-05-19** |
| `scripts/label_ner.py` | New — labeling CLI + F1 with truncation-miss bucket | Pending |
| `app/services/ner_sidecar/model.py` | `_CONFIDENCE_THRESHOLDS` tuning (values only) | Pending |
| `app/ingestion/entity_extractor.py` | Phase 2 — mentions filter, trusted/discovery split | Gated |
