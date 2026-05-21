"""Body extraction + quality classification.

Pure compute layer. No I/O. Wraps Trafilatura.
"""
from typing import Optional
import math
import re

import trafilatura


# Trailing "Related: <link>" paragraphs are part of the article <div> on some
# sources (e.g. SecurityWeek), so Trafilatura includes them as main content.
# They leak unrelated entities/keywords into clustering — strip them.
_RELATED_FOOTER_LINE = re.compile(r"^\s*Related:\s", re.IGNORECASE)


def _strip_related_footer(text: str) -> str:
    """Drop a trailing block of 'Related: ...' link lines from extracted text."""
    lines = text.split("\n")
    end = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        if not line.strip():
            continue  # tolerate blank lines between related links
        if _RELATED_FOOTER_LINE.match(line):
            end = i
        else:
            break
    return "\n".join(lines[:end]).rstrip()


def classify_length(length: int, threshold: int) -> str:
    """Classify body length into a quality tier.

    - ok:    length >= threshold
    - weak:  ceil(threshold/3) <= length < threshold
    - empty: length < ceil(threshold/3)

    The weak/empty boundary scales with the per-source threshold so a 200-char
    threshold for NVD doesn't auto-classify everything below 500 as empty.
    """
    weak_floor = max(math.ceil(threshold / 3), 1)
    if length >= threshold:
        return "ok"
    if length >= weak_floor:
        return "weak"
    return "empty"


def extract_text(html: Optional[str]) -> Optional[str]:
    """Run Trafilatura on HTML, return clean main-content text.

    Returns None if input is empty or extraction fails.
    """
    if not html:
        return None
    try:
        result = trafilatura.extract(
            html,
            favor_recall=True,        # tolerate sites with thin metadata
            include_comments=False,
            include_tables=False,
            no_fallback=False,        # False = "use fallback" — keep readability-lxml active
        )
        if not result:
            return None
        return _strip_related_footer(result) or None
    except Exception:
        return None
