# NER Quality Improvement — Design Spec
_2026-05-17_

## Problem

The entity extraction pipeline (SecureBERT sidecar + regex) produces three categories of noise:

| Failure | Example | Root cause |
|---|---|---|
| Model artifact fragments | `cobalt-strik` alongside `cobalt-strike` | Model assigns O to final subword at some positions; char-span fix partially applied but edge cases remain |
| Generic word FPs | `expand` (243 articles), `route`, `devices` | Model correctly identifies Unix/common words as tools; these are real tools but irrelevant to security clustering |
| Abbreviation/alias splits | `burp` vs `burp-suite` | Same entity at different mention forms; not a model artifact — genuine abbreviation |

CVE extraction is regex-only and excluded from NER quality scope.

## Goals

- Best F1 per entity type, prioritising recall over precision (missing `lazarus-group` is worse than keeping `expand`)
- Exception: tool and product types — these are the noisiest; acceptable to be stricter
- Measurable: every change must have a before/after F1 number against a human-labeled ground truth

## Non-goals

- Hardcoded patches for specific fragment strings (e.g. adding `cobalt-strik → cobalt-strike` to a dict)
- Source-level confidence weighting (deferred — verify Approach B doesn't fix the problem first)
- Fine-tuning the SecureBERT model

---

## Architecture

### Extraction Pipeline (updated order)

```
sidecar output
  → 1. synonym map          (resolve abbreviations: burp → burp-suite)
  → 2. edit-distance dedup  (merge model artifacts: cobalt-strik → cobalt-strike)
  → 3. mentions filter      (drop low-frequency generics: expand, route, devices)
  → merge with regex
  → store
```

All three stages run inside `extract_entities()` in `app/ingestion/entity_extractor.py`, after the sidecar call and before the regex merge. They operate in memory on a single article's entity list.

---

## Components

### 1. Labeling pass + F1 measurement (`scripts/label_ner.py`)

**Purpose:** Establish a human-labeled ground truth dataset so every subsequent change has a measured F1 delta.

**What gets labeled:**
- `only-local` rows from `ner_eval_judgments` (sidecar found, Haiku didn't — adjudicate TP vs FP)
- `only-haiku` rows (Haiku found, sidecar didn't — adjudicate TP vs FP, i.e. genuine FN vs Haiku noise)

**Labeling UX:** CLI script presents entity + article snippet showing where the entity appears in context. User inputs `t` (TP), `f` (FP), or `s` (skip). Writes verdict to `ner_eval_judgments.verdict`.

**F1 computation:**
- TP = `both` (agreed) + `only-local` labeled TP
- FP = `only-local` labeled FP
- FN = `only-haiku` labeled TP (sidecar missed a real entity)
- Computed per entity type: malware, actor, campaign, tool, product, vuln_alias

**Target corpus:** ~50 articles selected to cover diverse sources (CISA, Securelist, Unit 42, Krebs, PortSwigger) and content types (news, threat_advisory, ics_advisory). Selection: pull the articles with the most `only-local` entity judgments from `ner_eval_judgments` — these are the highest-signal rows to adjudicate first.

---

### 2. Synonym map (Stage 1)

**Location:** `app/ingestion/entity_extractor.py`, applied to sidecar output before dedup.

**Purpose:** Resolve genuine abbreviations and aliases — cases where the model extracts a valid shorthand that should map to a canonical full name.

**Structure:**
```python
_SIDECAR_SYNONYMS: dict[str, str] = {
    "burp": "burp-suite",
    # add as discovered during labeling pass
}
```

**Rules:**
- Keyed on `normalized_key` (lowercase, hyphenated)
- If the canonical target already exists in the entity list for this article, increment its mentions rather than creating a duplicate
- Only covers abbreviations, NOT model artifacts (edit-distance dedup handles those)

---

### 3. Edit-distance dedup (Stage 2)

**Location:** `app/ingestion/entity_extractor.py`, after synonym map, before mentions filter.

**Purpose:** Merge model artifact fragments into their complete form without maintaining per-entity patches.

**Algorithm:**
For each pair of entities (A, B) of the same type within a single article's sidecar output:
- If `edit_distance(A.normalized_key, B.normalized_key) <= 1` → merge shorter into longer, sum mentions
- If one is a strict prefix of the other and `len(prefix) >= 6` → merge shorter into longer, sum mentions
- Longer form wins (more complete extraction)

**Constraints:**
- Only same-type pairs (don't merge a tool fragment into a malware name)
- Minimum prefix length 6 to avoid merging genuinely distinct short entities
- Edit distance 1 catches single dropped/transposed character artifacts

**Why this is the root-cause fix:** Any fragment the model produces — for any entity, in any article — gets absorbed automatically. No per-entity maintenance.

---

### 4. Mentions filter (Stage 3)

**Location:** `app/ingestion/entity_extractor.py`, after dedup, before regex merge.

**Purpose:** Drop entities that appear only once in the article body. Generic words (`expand`, `route`, `devices`) tend to appear once; real security tools and actors tend to appear multiple times.

**Thresholds (tunable per type):**
```python
_MIN_MENTIONS: dict[str, int] = {
    "tool":       2,
    "product":    2,
    "malware":    1,  # single mention of lazarus-group is worth keeping
    "actor":      1,
    "campaign":   1,
    "vuln_alias": 1,
}
```

**Source of `mentions`:** Already computed by the sidecar in `model.py` (`_dedup()` accumulates mention count). Available on each entity dict from the sidecar.

**Regex entities are exempt** — regex patterns already encode specificity (CVE format, known vendor names); no mentions threshold needed there.

---

## Threshold tuning (after labeling)

Not a separate code component — it's a process step:

1. Run labeling pass → compute baseline F1 per type
2. Apply synonym map + edit-distance dedup + mentions filter
3. Re-run NER backfill with `--force` on the labeled corpus
4. Recompute F1 → measure delta
5. If any type shows F1 regression, adjust `_MIN_MENTIONS` or `_CONFIDENCE_THRESHOLDS` in `model.py`

Thresholds currently in `model.py`:
```python
_CONFIDENCE_THRESHOLDS = {
    "malware": 0.75, "actor": 0.75, "campaign": 0.75,
    "product": 0.50, "tool": 0.50, "vuln_alias": 0.50,
}
```

---

## Implementation sequence

1. `scripts/label_ner.py` — labeling CLI + F1 computation
2. Baseline F1 measurement (label ~50 articles)
3. Stage 1: synonym map in `extract_entities()`
4. Stage 2: edit-distance dedup in `extract_entities()`
5. Stage 3: mentions filter in `extract_entities()`
6. Re-run NER backfill on labeled corpus, recompute F1
7. Threshold tuning if any type regresses
8. Full backfill (`backfill_ner_sidecar.py --force`) to regenerate clean entities index

---

## Files touched

| File | Change |
|---|---|
| `scripts/label_ner.py` | New — labeling CLI |
| `app/ingestion/entity_extractor.py` | Add synonym map, edit-distance dedup, mentions filter to `extract_entities()` |
| `app/services/ner_sidecar/model.py` | Threshold tuning (values only, no structural change) |
