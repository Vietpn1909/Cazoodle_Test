"""Texts that get embedded (and full-text indexed), plus content hashes for staleness checks.

Pure functions: the embedding step compares ``text_hash`` with the hash stored next to each
vector, so a vector is recomputed only when its text actually changed.

Design notes
- Work text leads with the title, then the OpenAlex topic hierarchy and keywords (a compact,
  high-signal summary that survives truncation), then the abstract. bge-small reads at most
  512 tokens; ~1.5k abstract characters keep the whole text under that.
- Faculty text describes *expertise* only (no name, no position): cleaned keywords ranked by
  what their publications support, plus the topics of their own UIUC works. Queries such as
  "computer vision in healthcare" then match people, not titles.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence

WORK_ABSTRACT_CHARS = 1500
MAX_WORK_KEYWORDS = 8
MAX_FACULTY_KEYWORDS = 40
MAX_FACULTY_TOPICS = 10


def text_hash(text: str) -> str:
    """Short, stable content hash (16 hex chars)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = it.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(it.strip())
    return out


def _truncate_at_word(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.6 else cut).rstrip(" ,;:.") + "..."


def work_text(
    title: str,
    abstract: str | None,
    *,
    topic: str | None = None,
    subfield: str | None = None,
    field: str | None = None,
    keywords: Sequence[str] | None = None,
) -> str:
    """Text embedded for a work: ``Title. Topic (Subfield; Field). Keywords: ... Abstract``."""
    parts = [title.strip().rstrip(".")]
    hierarchy = "; ".join(p for p in (subfield, field) if p)
    if topic:
        parts.append(f"{topic} ({hierarchy})" if hierarchy else topic)
    elif hierarchy:
        parts.append(hierarchy)
    kws = _dedupe(keywords or [])[:MAX_WORK_KEYWORDS]
    if kws:
        parts.append("Keywords: " + ", ".join(kws))
    text = ". ".join(parts) + "."
    if abstract and abstract.strip():
        text += " " + _truncate_at_word(abstract.strip(), WORK_ABSTRACT_CHARS)
    return text


def faculty_text(
    keywords_top: Sequence[str] | None,
    keywords_clean: Sequence[str] | None,
    topics: Sequence[str] | None,
) -> str:
    """Text embedded for a faculty member; empty string when there is nothing to describe."""
    kws = _dedupe([*(keywords_top or []), *(keywords_clean or [])])[:MAX_FACULTY_KEYWORDS]
    tps = _dedupe(topics or [])[:MAX_FACULTY_TOPICS]
    parts = []
    if kws:
        parts.append("Research interests: " + ", ".join(kws) + ".")
    if tps:
        parts.append("Publication topics: " + "; ".join(tps) + ".")
    return " ".join(parts)
