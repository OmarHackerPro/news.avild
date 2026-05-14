"""SecureModernBERT-NER wrapper with serialized inference.

Loads the model once at startup. Inference is serialized via an asyncio.Lock
because multiple concurrent ingester coroutines may call /extract.
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass
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

MODEL_ID = os.getenv("NER_MODEL_PATH", os.getenv("NER_MODEL_ID", "attack-vector/SecureModernBERT-NER"))
MODEL_REVISION = os.getenv("NER_MODEL_REVISION", "main")
MAX_TOKENS = int(os.getenv("NER_MAX_TOKENS", "4096"))
CONFIDENCE_THRESHOLD = float(os.getenv("NER_CONFIDENCE_THRESHOLD", "0.5"))
MODEL_VERSION = os.getenv("NER_MODEL_VERSION", "securebert-v1")


@dataclass
class ExtractedEntity:
    type: str
    name: str
    score: float
    char_offset: int  # start position in original input text


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
        offsets = enc.pop("offset_mapping")[0].tolist()
        with torch.no_grad():
            logits = self.model(**enc).logits[0]
        probs = torch.softmax(logits, dim=-1)
        scores, label_ids = probs.max(dim=-1)
        scores = scores.tolist()
        label_ids = label_ids.tolist()
        labels = [self._id2label[i] for i in label_ids]

        raw = self._merge_bio(labels, scores, offsets, text)
        return self._post_merge(raw, text)

    def _post_merge(
        self,
        entities: list[ExtractedEntity],
        text: str,
        gap: int = 1,
    ) -> list[ExtractedEntity]:
        """Merge same-type entities whose spans are within `gap` chars.

        ByteLevel BPE splits words into subword tokens; a single O-tagged
        subword (e.g. the 'I' in 'Ivanti' or '-' in 'CVE-2021-44228') causes
        _merge_bio to prematurely flush and produce fragments. Merging spans
        within 2 chars reunites those fragments without risk of joining
        genuinely separate entities (which need ≥4 chars of separation).
        """
        if len(entities) <= 1:
            return entities
        entities = sorted(entities, key=lambda e: e.char_offset)
        merged = [entities[0]]
        for ent in entities[1:]:
            prev = merged[-1]
            prev_end = prev.char_offset + len(prev.name)
            if prev.type == ent.type and (ent.char_offset - prev_end) <= gap:
                new_end = ent.char_offset + len(ent.name)
                merged[-1] = ExtractedEntity(
                    type=prev.type,
                    name=text[prev.char_offset:new_end].strip(),
                    score=(prev.score + ent.score) / 2,
                    char_offset=prev.char_offset,
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
        """Merge BIO-tagged tokens into entities, applying threshold and label map."""
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
            if avg_score >= CONFIDENCE_THRESHOLD:
                span_text = text[cur_start:cur_end].strip()
                if span_text:
                    out.append(ExtractedEntity(
                        type=LABEL_MAP[cur_type],
                        name=span_text,
                        score=avg_score,
                        char_offset=cur_start,
                    ))
            cur_type = cur_start = None
            cur_scores = []

        for label, score, (start, end) in zip(labels, scores, offsets):
            if start == 0 and end == 0:
                # special tokens (CLS, SEP, PAD)
                flush()
                continue

            if label == "O" or label is None:
                flush()
                continue

            # Expect labels like "B-MALWARE", "I-MALWARE"; tolerate plain "MALWARE"
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
            else:  # tag == "I" and cur_type == raw_type
                cur_end = end
                cur_scores.append(score)

        flush()
        return out
