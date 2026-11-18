"""Shared lightweight text normalization for lexical similarity."""

from __future__ import annotations

import re

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "were",
        "with",
        "using",
        "use",
        "based",
        "via",
        "new",
        "study",
        "studies",
        "analysis",
        "approach",
        "approaches",
        "method",
        "methods",
        "model",
        "models",
        "system",
        "systems",
        "data",
        "research",
        "results",
        "effect",
        "effects",
        "case",
        "application",
        "applications",
        "toward",
        "towards",
        "under",
        "between",
        "among",
        "within",
        "high",
        "low",
        "large",
        "small",
        "two",
        "one",
        "three",
        "role",
        "impact",
        "during",
        "over",
        "can",
        "not",
        "but",
        "also",
        "more",
        "most",
        "than",
        "which",
    ]
)


def terms(text: str) -> list[str]:
    """Bag-of-words terms: lowercase, >=3 chars, stopwords removed, naive plural strip."""
    out = []
    for w in re.findall(r"[a-z][a-z0-9\-]{2,}", text.lower()):
        if w in STOPWORDS:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out
