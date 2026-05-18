"""SecureModernBERT-NER wrapper with serialized inference.

Loads the model once at startup. Inference is serialized via an asyncio.Lock
because multiple concurrent ingester coroutines may call /extract.
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import torch
from transformers import AutoModelForTokenClassification, AutoTokenizer

logger = logging.getLogger(__name__)

# Map model labels to internal entity types. Labels not in this dict are dropped.
LABEL_MAP: dict[str, str] = {
    "PRODUCT": "product",
    "MALWARE": "malware",
    "THREAT-ACTOR": "actor",
    "TOOL": "tool",
    "CAMPAIGN": "campaign",
    "CVE": "cve",
}

# Per-type confidence thresholds. Rare/high-variance classes (malware, actor,
# campaign) need a higher bar to cut false positives on research/technique articles.
# Product/tool/cve are common and easier for the model; 0.5 is fine there.
_CONFIDENCE_THRESHOLDS: dict[str, float] = {
    "PRODUCT": 0.5,
    "TOOL": 0.5,
    "CVE": 0.5,
    "MALWARE": 0.75,
    "THREAT-ACTOR": 0.75,
    "CAMPAIGN": 0.75,
}

MODEL_ID = os.getenv("NER_MODEL_PATH", os.getenv("NER_MODEL_ID", "attack-vector/SecureModernBERT-NER"))
MODEL_REVISION = os.getenv("NER_MODEL_REVISION", "main")
MAX_TOKENS = int(os.getenv("NER_MAX_TOKENS", "4096"))
CONFIDENCE_THRESHOLD = float(os.getenv("NER_CONFIDENCE_THRESHOLD", "0.5"))  # fallback
MODEL_VERSION = os.getenv("NER_MODEL_VERSION", "securebert-v1")


@dataclass
class ExtractedEntity:
    type: str
    name: str
    score: float
    char_offset: int
    mentions: int = field(default=1)


class NerModel:
    """Singleton model wrapper. Use NerModel.get() after load()."""

    _instance: Optional["NerModel"] = None

    def __init__(self) -> None:
        self.tokenizer = None
        self.model = None
        self._lock = asyncio.Lock()
        self._device = torch.device("cpu")  # CPU-only per spec
        self._id2label: dict[int, str] = {}

    @classmethod
    async def load(cls) -> "NerModel":
        if cls._instance is not None:
            return cls._instance
        inst = cls()
        start = time.perf_counter()
        logger.info("Loading NER model %s on CPU", MODEL_ID)
        local = os.path.isdir(MODEL_ID)
        kwargs = {} if local else {"revision": MODEL_REVISION}
        inst.tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, **kwargs)
        inst.model = AutoModelForTokenClassification.from_pretrained(MODEL_ID, **kwargs)
        inst.model.to(inst._device)
        inst.model.eval()
        inst._id2label = inst.model.config.id2label
        logger.info("Loaded NER model in %.2fs", time.perf_counter() - start)
        cls._instance = inst
        return inst

    @classmethod
    def get(cls) -> "NerModel":
        if cls._instance is None:
            raise RuntimeError("NerModel.load() not called before get()")
        return cls._instance

    async def extract(self, text: str) -> list[ExtractedEntity]:
        """Run NER over text and return entities mapped to internal types.

        Serialized via self._lock so concurrent callers do not interleave through
        the same torch model object.
        """
        if not text or not text.strip():
            return []

        async with self._lock:
            return await asyncio.get_running_loop().run_in_executor(
                None, self._extract_sync, text
            )

    def _extract_sync(self, text: str) -> list[ExtractedEntity]:
        enc = self.tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=MAX_TOKENS,
            return_offsets_mapping=True,
        )
        # word_ids() must be called before popping offset_mapping
        word_ids = enc.word_ids()
        offsets = enc.pop("offset_mapping")[0].tolist()

        with torch.no_grad():
            logits = self.model(**enc).logits[0]
        probs = torch.softmax(logits, dim=-1)
        scores, label_ids = probs.max(dim=-1)
        scores = scores.tolist()
        label_ids = label_ids.tolist()
        labels = [self._id2label[i] for i in label_ids]

        # Aggregate subword tokens → one entry per word, then BIO-merge at word level.
        # This eliminates fragments caused by single mis-tagged subword tokens.
        word_labels, word_scores, word_offsets = self._aggregate_words(
            labels, scores, offsets, word_ids
        )
        raw = self._merge_bio(word_labels, word_scores, word_offsets, text)
        # Safety net: merge same-type adjacent words where the model emitted B
        # instead of I for a continuation word (gap=1 = one whitespace char).
        raw = self._post_merge(raw, text)
        return self._dedup(raw)

    def _aggregate_words(
        self,
        labels: list[str],
        scores: list[float],
        offsets: list[list[int]],
        word_ids: list[Optional[int]],
    ) -> tuple[list[str], list[float], list[list[int]]]:
        """Collapse subword tokens to one entry per word (max-score strategy).

        Tokens sharing a word_id belong to the same word. The subword with the
        highest score determines the label; the char span covers the full word
        from first to last subword offset.
        """
        result_labels: list[str] = []
        result_scores: list[float] = []
        result_offsets: list[list[int]] = []

        current_wid: Optional[int] = None
        best_label: Optional[str] = None
        best_score = -1.0
        char_start = char_end = 0

        def _flush() -> None:
            nonlocal current_wid, best_label, best_score
            if current_wid is not None:
                result_labels.append(best_label)
                result_scores.append(best_score)
                result_offsets.append([char_start, char_end])
            current_wid = None
            best_label = None
            best_score = -1.0

        for label, score, (start, end), wid in zip(labels, scores, offsets, word_ids):
            if wid is None:  # CLS / SEP / PAD
                _flush()
                continue
            if wid != current_wid:
                _flush()
                current_wid = wid
                best_label = label
                best_score = score
                char_start = start
                char_end = end
            else:
                # same word — take max-score subword's label
                if score > best_score:
                    best_label = label
                    best_score = score
                char_end = end  # extend word span to last subword

        _flush()
        return result_labels, result_scores, result_offsets

    def _dedup(self, entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
        """Dedup by (type, lowercase name). Keep max score, count total mentions."""
        seen: dict[tuple[str, str], ExtractedEntity] = {}
        for ent in entities:
            key = (ent.type, ent.name.lower().strip())
            if key in seen:
                seen[key].mentions += 1
                if ent.score > seen[key].score:
                    seen[key].score = ent.score
                    seen[key].char_offset = ent.char_offset
            else:
                seen[key] = ExtractedEntity(
                    type=ent.type,
                    name=ent.name,
                    score=ent.score,
                    char_offset=ent.char_offset,
                    mentions=1,
                )
        return list(seen.values())

    def _post_merge(
        self,
        entities: list[ExtractedEntity],
        text: str,
        gap: int = 1,
    ) -> list[ExtractedEntity]:
        """Merge same-type adjacent entities separated by at most `gap` chars.

        Safety net for cases where the model emits B- instead of I- on a
        continuation word. Gap=1 covers the single whitespace between words.
        Sentence-boundary punctuation (. ! ?) always blocks merging.
        """
        if len(entities) <= 1:
            return entities
        entities = sorted(entities, key=lambda e: e.char_offset)
        merged = [entities[0]]
        for ent in entities[1:]:
            prev = merged[-1]
            prev_end = prev.char_offset + len(prev.name)
            gap_text = text[prev_end:ent.char_offset]
            has_boundary = any(c in gap_text for c in ".!?")
            if (
                prev.type == ent.type
                and (ent.char_offset - prev_end) <= gap
                and not has_boundary
            ):
                new_end = ent.char_offset + len(ent.name)
                merged[-1] = ExtractedEntity(
                    type=prev.type,
                    name=text[prev.char_offset:new_end].strip(),
                    score=(prev.score + ent.score) / 2,
                    char_offset=prev.char_offset,
                    mentions=prev.mentions,
                )
            else:
                merged.append(ent)
        return merged

    def _merge_bio(
        self,
        labels: list[str],
        scores: list[float],
        offsets: list[list[int]],
        text: str,
    ) -> list[ExtractedEntity]:
        """Merge BIO-tagged words into entity spans with per-type confidence gating."""
        out: list[ExtractedEntity] = []
        cur_type: Optional[str] = None
        cur_start: Optional[int] = None
        cur_end: int = 0
        cur_scores: list[float] = []

        def flush() -> None:
            nonlocal cur_type, cur_start, cur_end, cur_scores
            if cur_type is None or cur_start is None:
                cur_type = cur_start = None
                cur_scores = []
                return
            avg_score = sum(cur_scores) / len(cur_scores)
            threshold = _CONFIDENCE_THRESHOLDS.get(cur_type, CONFIDENCE_THRESHOLD)
            if avg_score >= threshold:
                raw_span = text[cur_start:cur_end]
                span_text = raw_span.strip()
                if span_text:
                    # char_offset must point at name[0]: leading whitespace is
                    # stripped from name (byte-BPE prepends a space to non-initial
                    # words), so advance the offset to keep the two consistent.
                    lead = len(raw_span) - len(raw_span.lstrip())
                    out.append(ExtractedEntity(
                        type=LABEL_MAP[cur_type],
                        name=span_text,
                        score=avg_score,
                        char_offset=cur_start + lead,
                    ))
            cur_type = cur_start = None
            cur_scores = []

        for label, score, (start, end) in zip(labels, scores, offsets):
            if start == 0 and end == 0:
                flush()
                continue

            if label == "O" or label is None:
                flush()
                continue

            tag, _, raw_type = label.partition("-")
            if not raw_type:
                raw_type = tag
                tag = "B"

            if raw_type not in LABEL_MAP:
                flush()
                continue

            if tag == "B" or cur_type != raw_type:
                flush()
                cur_type = raw_type
                cur_start = start
                cur_end = end
                cur_scores = [score]
            else:
                cur_end = end
                cur_scores.append(score)

        flush()
        return out
