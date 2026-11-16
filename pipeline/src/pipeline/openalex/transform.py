"""Pure functions turning raw OpenAlex work JSON into rows for our schema.

No I/O here: everything is unit-testable with small fixtures.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

OPENALEX_PREFIX = "https://openalex.org/"

# Work types considered scholarly output for the portal (OpenAlex `type` vocabulary).
ALLOWED_TYPES = frozenset(
    {
        "article",
        "preprint",
        "review",
        "book-chapter",
        "book",
        "dissertation",
        "conference-paper",
        "report",
    }
)

# Portal + assistant are English-only. Non-English records are also where OpenAlex
# metadata errors concentrate (e.g. a 2025 Russian article with 8k+ citations that
# raw-affiliates "University of Illinois"). None = language unknown -> keep.
ALLOWED_LANGUAGES = frozenset({"en", None})

# Raw affiliation text that plausibly refers to UIUC (used to catch mis-attributed works).
UIUC_AFFILIATION_RE = re.compile(
    r"illinois|urbana|champaign|uiuc|beckman institute|ncsa", re.IGNORECASE
)


def short_id(url: str | None) -> str | None:
    """``https://openalex.org/W123`` -> ``W123``; ``.../fields/32`` -> ``32``."""
    if not url:
        return None
    return url.removeprefix(OPENALEX_PREFIX).rsplit("/", 1)[-1]


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi.strip(), flags=re.IGNORECASE).lower()


def rebuild_abstract(inverted: dict[str, list[int]] | None) -> str | None:
    """Rebuild abstract text from OpenAlex's ``abstract_inverted_index``."""
    if not inverted:
        return None
    positions = {pos: word for word, plist in inverted.items() for pos in plist}
    text = " ".join(positions[i] for i in sorted(positions)).strip()
    return text or None


def normalize_title(title: str) -> str:
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _strip_tags(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = re.sub(r"<[^>]+>", "", text).strip()
    return cleaned or None


# --------------------------------------------------------------------------- quality


def is_uiuc_institution(inst: dict[str, Any], institution_id: str) -> bool:
    lineage = [short_id(x) for x in inst.get("lineage") or []]
    return short_id(inst.get("id")) == institution_id or institution_id in lineage


def drop_reason(raw: dict[str, Any], institution_id: str) -> str | None:
    """Return why a raw work should be excluded, or None to keep it."""
    if raw.get("is_paratext"):
        return "paratext"
    if raw.get("is_retracted"):
        return "retracted"
    if not (raw.get("title") or "").strip():
        return "missing_title"
    if not raw.get("publication_date"):
        return "missing_date"
    if raw.get("type") not in ALLOWED_TYPES:
        return "type_not_allowed"
    if raw.get("language") not in ALLOWED_LANGUAGES:
        return "non_english"
    if is_suspected_misaffiliation(raw, institution_id):
        return "suspected_misaffiliation"
    return None


def is_suspected_misaffiliation(raw: dict[str, Any], institution_id: str) -> bool:
    """True when no UIUC-tagged author has raw affiliation text that mentions UIUC.

    OpenAlex sometimes attaches UIUC to works whose raw affiliation strings point
    elsewhere (e.g. a Russian-language paper in the window). If the raw strings of all
    UIUC-tagged authorships are empty we cannot verify, so we keep the work.
    """
    raw_strings: list[str] = []
    for a in raw.get("authorships") or []:
        if any(is_uiuc_institution(i, institution_id) for i in a.get("institutions") or []):
            raw_strings.extend(a.get("raw_affiliation_strings") or [])
    if not raw_strings:
        return False
    return not any(UIUC_AFFILIATION_RE.search(s) for s in raw_strings)


# --------------------------------------------------------------------------- normalization


@dataclass
class NormalizedWork:
    work: dict[str, Any]
    authors: list[dict[str, Any]] = field(default_factory=list)
    authorships: list[dict[str, Any]] = field(default_factory=list)
    topics: list[dict[str, Any]] = field(default_factory=list)
    work_topics: list[dict[str, Any]] = field(default_factory=list)
    work_institutions: list[dict[str, Any]] = field(default_factory=list)
    citation_percentile: float | None = None


def _topic_row(t: dict[str, Any]) -> dict[str, Any]:
    sub, fld, dom = t.get("subfield") or {}, t.get("field") or {}, t.get("domain") or {}
    return {
        "id": short_id(t["id"]),
        "name": t.get("display_name") or "",
        "subfield_id": short_id(sub.get("id")),
        "subfield": sub.get("display_name"),
        "field_id": short_id(fld.get("id")),
        "field": fld.get("display_name"),
        "domain_id": short_id(dom.get("id")),
        "domain": dom.get("display_name"),
    }


def normalize_work(raw: dict[str, Any], institution_id: str) -> NormalizedWork:
    wid = short_id(raw["id"])
    assert wid is not None
    loc = raw.get("primary_location") or {}
    source = loc.get("source") or {}
    oa = raw.get("open_access") or {}
    primary_topic = raw.get("primary_topic")
    cnp = raw.get("citation_normalized_percentile") or {}

    work = {
        "id": wid,
        "doi": normalize_doi(raw.get("doi")),
        "title": _strip_tags(raw.get("title")) or "",
        "abstract": rebuild_abstract(raw.get("abstract_inverted_index")),
        "publication_date": date.fromisoformat(raw["publication_date"]),
        "publication_year": raw.get("publication_year"),
        "type": raw.get("type"),
        "language": raw.get("language"),
        "cited_by_count": raw.get("cited_by_count") or 0,
        "fwci": raw.get("fwci"),
        "is_oa": bool(oa.get("is_oa")),
        "oa_url": oa.get("oa_url"),
        "venue_name": source.get("display_name"),
        "primary_topic_id": short_id(primary_topic["id"]) if primary_topic else None,
        "keywords": [k["display_name"] for k in raw.get("keywords") or []] or None,
        "sdgs": [
            {"id": short_id(s.get("id")), "name": s.get("display_name"), "score": s.get("score")}
            for s in raw.get("sustainable_development_goals") or []
        ]
        or None,
    }
    nw = NormalizedWork(work=work, citation_percentile=cnp.get("value"))

    raw_topics: list[dict[str, Any]] = list(raw.get("topics") or [])
    if primary_topic and all(t.get("id") != primary_topic.get("id") for t in raw_topics):
        raw_topics.append(primary_topic)
    topic_ids: set[str] = set()
    for t in raw_topics:
        row = _topic_row(t)
        if row["id"] in topic_ids:
            continue
        topic_ids.add(row["id"])
        nw.topics.append(row)
        nw.work_topics.append({"work_id": wid, "topic_id": row["id"], "score": t.get("score")})

    seen_authors: set[str] = set()
    seen_insts: set[str] = set()
    for order, a in enumerate(raw.get("authorships") or []):
        author = a.get("author") or {}
        aid = short_id(author.get("id"))
        insts = a.get("institutions") or []
        for inst in insts:
            iid = short_id(inst.get("id"))
            if iid and iid not in seen_insts:
                seen_insts.add(iid)
                nw.work_institutions.append(
                    {
                        "work_id": wid,
                        "institution_id": iid,
                        "name": inst.get("display_name"),
                        "country_code": inst.get("country_code"),
                        "type": inst.get("type"),
                    }
                )
        if not aid or aid in seen_authors:
            continue
        seen_authors.add(aid)
        nw.authors.append(
            {
                "id": aid,
                "display_name": author.get("display_name") or a.get("raw_author_name") or "",
                "orcid": (author.get("orcid") or "").removeprefix("https://orcid.org/") or None,
            }
        )
        raw_aff = " | ".join(a.get("raw_affiliation_strings") or []) or None
        nw.authorships.append(
            {
                "work_id": wid,
                "author_id": aid,
                "author_order": order,
                "author_position": a.get("author_position"),
                "is_corresponding": bool(a.get("is_corresponding")),
                "is_uiuc": any(is_uiuc_institution(i, institution_id) for i in insts),
                "raw_affiliation": raw_aff,
            }
        )
    return nw


# --------------------------------------------------------------------------- dedupe & rank


def dedupe(works: Sequence[NormalizedWork]) -> tuple[list[NormalizedWork], int]:
    """Drop duplicates by DOI, then by normalized title+year; keep the most cited copy."""
    ordered = sorted(works, key=lambda w: -int(w.work["cited_by_count"]))
    kept: list[NormalizedWork] = []
    seen_doi: set[str] = set()
    seen_title: set[tuple[str, int]] = set()
    for w in ordered:
        doi = w.work["doi"]
        tkey = (normalize_title(w.work["title"]), int(w.work["publication_year"] or 0))
        if (doi and doi in seen_doi) or (tkey[0] and tkey in seen_title):
            continue
        if doi:
            seen_doi.add(doi)
        if tkey[0]:
            seen_title.add(tkey)
        kept.append(w)
    return kept, len(works) - len(kept)


def percentile_ranks(values: Sequence[float | None]) -> list[float]:
    """Percentile rank in [0, 1] (ties share the average rank); None -> 0."""
    present = sorted(v for v in values if v is not None)
    n = len(present)
    if n == 0:
        return [0.0 for _ in values]
    first: dict[float, int] = {}
    last: dict[float, int] = {}
    for i, pv in enumerate(present):
        first.setdefault(pv, i)
        last[pv] = i
    out: list[float] = []
    for v in values:
        if v is None:
            out.append(0.0)
        else:
            avg = (first[v] + last[v]) / 2
            out.append(avg / (n - 1) if n > 1 else 1.0)
    return out


@dataclass(frozen=True)
class RankWeights:
    normalized_citations: float = 0.4  # OpenAlex citation_normalized_percentile (shrunk)
    fwci: float = 0.2  # percentile of field-weighted citation impact (shrunk)
    raw_citations: float = 0.2  # percentile of cited_by_count within the corpus
    recency: float = 0.2  # linear 0..1 across the window
    # Pseudo-count for shrinkage: with c citations, normalized metrics get weight c/(c+k).
    prior_citations: float = 5.0


def shrink(value: float, citations: int, prior_citations: float, prior: float = 0.5) -> float:
    """Pull a normalized metric toward ``prior`` when it rests on few citations.

    A 3-day-old paper with 2 citations can have FWCI 66 / percentile 1.0; that is noise,
    not notability. Weight = c / (c + k) grows with evidence.
    """
    w = citations / (citations + prior_citations)
    return w * value + (1 - w) * prior


def compute_rank_scores(
    works: Sequence[NormalizedWork],
    window_start: date,
    window_end: date,
    weights: RankWeights = RankWeights(),
) -> list[float]:
    """Composite "notability" score in [0, 1] (weights documented in RankWeights)."""
    cites = [int(w.work["cited_by_count"]) for w in works]
    raw_pct = percentile_ranks([float(c) for c in cites])
    fwci_pct = percentile_ranks([w.work["fwci"] for w in works])
    span = max((window_end - window_start).days, 1)
    k = weights.prior_citations
    scores = []
    for w, c, raw, fw in zip(works, cites, raw_pct, fwci_pct, strict=True):
        norm = w.citation_percentile if w.citation_percentile is not None else raw
        days = (w.work["publication_date"] - window_start).days
        recency = min(max(days / span, 0.0), 1.0)
        score = (
            weights.normalized_citations * shrink(norm, c, k)
            + weights.fwci * shrink(fw, c, k)
            + weights.raw_citations * raw
            + weights.recency * recency
        )
        scores.append(round(score, 6))
    return scores


def count_by(items: Iterable[Any]) -> dict[str, int]:
    return dict(Counter(str(i) for i in items).most_common())
