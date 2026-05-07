"""Body extraction + quality classification.

Pure compute layer. No I/O. Wraps Trafilatura.
"""
from typing import Optional
import math

import trafilatura


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
            no_fallback=False,        # let trafilatura try its readability fallback
        )
        return result
    except Exception:
        return None
