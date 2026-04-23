"""LLM-backed category classification for RSS feed categories.

Classifies RSS <category> labels into ingest decisions (allow/block) and
priority modifiers (-0.5 to +0.5) for a cybersecurity news platform.

LLM backend: Ollama (default model: llama3). Override with OLLAMA_MODEL env var.
Requires Ollama running at OLLAMA_URL (default: http://localhost:11434).
"""
import json
import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

_SYSTEM_PROMPT = """You are a content classifier for a cybersecurity news intelligence platform.
Given a source name and a list of RSS category labels, decide for each label:
1. ingest (true/false): should articles with this category label be ingested?
   - true: label is relevant to cybersecurity news, analysis, or research
   - false: label is irrelevant (sponsored content, lifestyle, job postings, weekly digests not specific to security)
2. priority_modifier (float, -0.5 to +0.5): scoring adjustment for this category
   - +0.2 to +0.5: high-signal categories (ransomware, zero-day, critical vulnerability, threat actor)
   - 0.0: neutral/standard categories (news, updates, security)
   - -0.2 to -0.5: low-signal categories (opinion, generic tech, marketing)
3. notes: one-sentence explanation

Respond ONLY with a JSON array matching this schema:
[{"label": "...", "ingest": true/false, "priority_modifier": 0.0, "notes": "..."}]
"""


@dataclass
class CategoryDecision:
    label: str
    ingest: bool
    priority_modifier: float
    notes: str


async def _call_llm(source_name: str, labels: list[str]) -> list[dict]:
    """Call Ollama and return parsed JSON list."""
    user_msg = f"Source: {source_name}\nCategories: {json.dumps(labels)}"
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "format": "json",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        raw = data["message"]["content"]
        return json.loads(raw)


async def classify_categories(
    source_name: str,
    category_labels: list[str],
) -> list[CategoryDecision]:
    """Classify RSS category labels via LLM. Returns one decision per label.

    Falls back to ingest=True, modifier=0.0 for all labels if LLM fails.
    """
    if not category_labels:
        return []

    try:
        raw_decisions = await _call_llm(source_name, category_labels)
        decisions = []
        label_set = set(category_labels)
        for item in raw_decisions:
            label = item.get("label", "")
            if label not in label_set:
                continue
            decisions.append(CategoryDecision(
                label=label,
                ingest=bool(item.get("ingest", True)),
                priority_modifier=float(item.get("priority_modifier", 0.0)),
                notes=str(item.get("notes", "")),
            ))
        # For any label the LLM didn't return, default to allow
        returned_labels = {d.label for d in decisions}
        for label in category_labels:
            if label not in returned_labels:
                logger.warning("LLM did not return decision for label '%s' — defaulting to allow", label)
                decisions.append(CategoryDecision(label=label, ingest=True, priority_modifier=0.0, notes="default"))
        return decisions
    except Exception:
        logger.exception("LLM classification failed for source '%s' — defaulting all to allow", source_name)
        return [
            CategoryDecision(label=lbl, ingest=True, priority_modifier=0.0, notes="llm_error")
            for lbl in category_labels
        ]
