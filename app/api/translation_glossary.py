"""Per-language glossary applied after machine translation.

Google Translate leaves cybersecurity acronyms in English ("APT", "AI", ...),
but readers expect the localized form. This module post-processes translated
text by performing word-boundary substitutions of known terms.

To add a new mapping: extend ACRONYM_GLOSSARY[<lang>] with {english: localized}.
"""

import re

# lang -> {English term: localized form}.
# Keys are matched case-sensitively with word boundaries; the English term
# is also matched against the *original* English source text in case Google
# already translated it to a different word.
ACRONYM_GLOSSARY: dict[str, dict[str, str]] = {
    "ru": {
        "APT": "АПТ",
    },
}


def _compile(term: str) -> re.Pattern:
    return re.compile(r"\b" + re.escape(term) + r"\b")


_COMPILED: dict[str, list[tuple[re.Pattern, str]]] = {
    lang: [(_compile(en), localized) for en, localized in mapping.items()]
    for lang, mapping in ACRONYM_GLOSSARY.items()
}


def apply_glossary(translated: str, lang: str) -> str:
    """Replace English acronyms in `translated` with the localized form for `lang`."""
    rules = _COMPILED.get(lang)
    if not rules:
        return translated
    out = translated
    for pattern, localized in rules:
        out = pattern.sub(localized, out)
    return out
