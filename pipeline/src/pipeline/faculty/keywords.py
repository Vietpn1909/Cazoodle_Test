"""Cleaning the noisy faculty keyword lists.

Observed noise (see `.claude/docs/faculty-data.md`): markup fragments (``mi mathvariant=``,
``\\cal l``), chemistry fragments (``o 4``, ``h 2``), institution / venue names
(``simon fraser university``, ``siam international conference``), people's names
(``chengxiang zhai university``, ``ben grosser``), and empty phrases (``real world``,
``such information networks``). Many look-alikes are legitimate topics and must survive
(``civil society``, ``journalism``, ``ieee 802.11``, ``university student retention``).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet

from pipeline.faculty.text import terms

DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
QUOTES = dict.fromkeys(map(ord, "‘’‛′"), "'") | dict.fromkeys(map(ord, "“”"), '"')

SHORT_OK = frozenset({"ai", "ar", "vr", "hiv", "dna", "rna", "art", "law", "gis", "iot", "nlp"})
EMPTY_PHRASES = frozenset(
    {
        "real world",
        "category names",
        "journal paper",
        "leading journals",
        "conference presentation",
        "workshop findings",
        "journal citation report",
        # venue / track names extracted as topics (ACL "System Demonstrations", EMNLP)
        "empirical methods",
        "system demonstrations",
        "annual meeting compendium",
        "extended abstract",
        "tutorial abstracts",
        "volume 1",
    }
)
_MARKUP = re.compile(r"[=\\<>{}|]")
# Formula split by the extractor: "co 2" -> "co2", "ch 4" -> "ch4", "l 1" -> "l1"
_SPLIT_FORMULA = re.compile(r"^([a-z]{1,2}) (\d{1,3})$")
_LONG_NUMBER = re.compile(r"\d{4,}")  # zip codes, catalogue numbers ("il 61801")
# Determiners left over from phrase extraction: "such peptides" -> "peptides"
_LEADING_FILLER = re.compile(r"^(?:(?:such|the|a|an|this|these|those|other|various|several)\s+)+")
_VENUE_OR_ORG_SUFFIX = re.compile(
    r"\b(university|universities|institute|conference|symposium|workshop|press|"
    r"department|journals?|proceedings)$"
)
_VENUE_PREFIX = re.compile(r"^(ieee|acm|siam|nsf)\b(?!\s*[\d.])")


def normalize_keyword(raw: str) -> str:
    t = unicodedata.normalize("NFKC", raw).translate(DASHES).translate(QUOTES).lower()
    t = re.sub(r"\s+", " ", t).strip(" '\"-.,;:")
    t = _LEADING_FILLER.sub("", t)
    return _SPLIT_FORMULA.sub(r"\1\2", t)


def is_junk(kw: str, person_names: frozenset[str] = frozenset()) -> bool:
    """``kw`` must already be normalized."""
    if not kw or not re.search(r"[a-z]", kw):
        return True
    if any(unicodedata.category(ch) == "Co" for ch in kw):  # private-use glyphs
        return True
    if _MARKUP.search(kw) or _LONG_NUMBER.search(kw):
        return True
    is_formula = bool(re.fullmatch(r"[a-z]{1,2}\d{1,3}", kw))
    if len(kw) < 3 and kw not in SHORT_OK and not is_formula:
        return True
    if kw in EMPTY_PHRASES:
        return True
    if "international conference" in kw or _VENUE_PREFIX.match(kw):
        return True
    if _VENUE_OR_ORG_SUFFIX.search(kw) and len(kw.split()) >= 2:
        return True
    return any(name in kw for name in person_names)


def rank_keywords(
    keywords: Sequence[str],
    *,
    doc_freq: Mapping[str, int],
    n_docs: int,
    evidence_terms: AbstractSet[str] = frozenset(),
    top_k: int = 15,
) -> list[str]:
    """Pick the keywords to *display* for a faculty member.

    score = 2 * support + specificity
    - support: share of the keyword's terms found in the person's matched publications
      (OpenAlex topics + UIUC works) -> keywords their papers actually back up;
    - specificity: normalized IDF across the directory (``applied mathematics`` is listed
      for 266 people and says little); keywords unique to one person are damped because
      many are extraction noise.
    Ties keep the original order.
    """
    log_n = math.log(max(n_docs, 2))
    scored: list[tuple[float, int, str]] = []
    for i, kw in enumerate(keywords):
        kt = set(terms(kw))
        support = len(kt & evidence_terms) / len(kt) if kt and evidence_terms else 0.0
        df = max(doc_freq.get(kw, 1), 1)
        specificity = math.log(max(n_docs, 1) / df) / log_n
        if df == 1:
            specificity *= 0.5
        scored.append((2 * support + specificity, -i, kw))
    scored.sort(reverse=True)
    return [kw for _, _, kw in scored[:top_k]]


def clean_keywords(raw: Iterable[str], person_names: frozenset[str] = frozenset()) -> list[str]:
    """Normalize, drop junk, dedupe (order preserved).

    ``person_names``: normalized "first last" strings of directory members; keywords that
    contain a person's name are extraction artifacts (e.g. ``chengxiang zhai university``).
    """
    out: list[str] = []
    seen: set[str] = set()
    for r in raw:
        kw = normalize_keyword(r)
        if kw in seen or is_junk(kw, person_names):
            continue
        seen.add(kw)
        out.append(kw)
    return out
