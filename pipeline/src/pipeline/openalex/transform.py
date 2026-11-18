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

# Raw affiliation text that plausibly refers to UIUC or one of its units (used to catch
# mis-attributed works). Unit names matter: raw strings often name only the unit
# ("National Center for Supercomputing Applications", "Anthony J. Leggett Institute ...").
# Deliberately NOT included: "Chan Zuckerberg Biohub Chicago" (lineage child of UIUC in
# OpenAlex, but its authors are Northwestern / UChicago researchers).
UIUC_AFFILIATION_RE = re.compile(
    r"illinois|urbana|champaign|uiuc|beckman institute|ncsa|"
    r"national center for supercomputing|leggett institute|prairie research institute|"
    r"geological survey|carle illinois",
    re.IGNORECASE,
)
# Stricter, campus-specific text: decides whether an authorship tagged only with the parent
# "University of Illinois System" belongs to Urbana-Champaign (not Chicago / Springfield).
ILLINOIS_SYSTEM_ID = "I2801919071"
UIUC_CAMPUS_RE = re.compile(
    r"urbana|champaign|uiuc|beckman institute|national center for supercomputing|ncsa",
    re.IGNORECASE,
)
OTHER_CAMPUS_RE = re.compile(r"chicago|springfield|peoria|rockford", re.IGNORECASE)

# Titles that are front/back matter even when OpenAlex does not flag is_paratext.
GENERIC_TITLE_RE = re.compile(
    r"^(preface|foreword|introduction|conclusions?|index|epilogue|prologue|contributors|"
    r"front matter|back matter|issue information|editorial board|table of contents|"
    r"acknowledge?ments?|about the authors?)$"
    r"|^(cover (picture|image|feature|art)|frontispiece|inside (front|back) cover)\b"
    r"|^(thank you|thanks) to (our |the )?.*reviewers\b|^(list of )?reviewers? (for|of) \d{4}",
    re.IGNORECASE,
)
ABSTRACT_LABEL_RE = re.compile(
    r"^\s*(abstract|summary)\b\s*(?:[:.\-]|\N{EN DASH}|\N{EM DASH})?\s*", re.IGNORECASE
)

# Authorships kept per work: the first N (display) plus every UIUC authorship.
MAX_STORED_AUTHORSHIPS = 100

# Version preference when the same work appears several times (preprint + published).
TYPE_PREFERENCE = (
    "article",
    "review",
    "conference-paper",
    "book-chapter",
    "book",
    "report",
    "dissertation",
    "preprint",
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


def clean_abstract(text: str | None) -> str | None:
    """Strip markup tags and a leading "Abstract"/"Summary" label."""
    if not text:
        return None
    t = re.sub(r"<[^>]+>", " ", text)
    t = ABSTRACT_LABEL_RE.sub("", t, count=1)
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


def normalize_title(title: str) -> str:
    """Comparison key. Punctuation (incl. unicode non-breaking hyphens, "CARE-PD") becomes a space
    *before* accent folding so tokens are not glued together."""
    t = "".join(ch if unicodedata.category(ch)[0] in "LN" else " " for ch in title)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _family_names(nw: NormalizedWork, limit: int = 30) -> set[str]:
    """Name tokens (not initials) of the first authors. Author ids differ between versions
    of a work (OpenAlex assigns new ids to new records) and name order may flip between
    versions ("Haoqin Hong" / "Hong Haoqin"), so all name tokens are compared."""
    out: set[str] = set()
    for a in nw.authorships[:limit]:
        out.update(t for t in normalize_title(a["author_name"]).split() if len(t) > 1)
    return out


def _strip_tags(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = re.sub(r"<[^>]+>", "", text).strip()
    return cleaned or None


# --------------------------------------------------------------------------- quality


def is_uiuc_institution(inst: dict[str, Any], institution_id: str) -> bool:
    lineage = [short_id(x) for x in inst.get("lineage") or []]
    return short_id(inst.get("id")) == institution_id or institution_id in lineage


def is_uiuc_authorship(a: dict[str, Any], institution_id: str) -> bool:
    """UIUC (or a UIUC sub-unit) — or the parent Illinois System when the raw affiliation
    text clearly names the Urbana-Champaign campus."""
    insts = a.get("institutions") or []
    if any(is_uiuc_institution(i, institution_id) for i in insts):
        return True
    if any(short_id(i.get("id")) == ILLINOIS_SYSTEM_ID for i in insts):
        raw = " ".join(a.get("raw_affiliation_strings") or [])
        return bool(UIUC_CAMPUS_RE.search(raw)) and not OTHER_CAMPUS_RE.search(raw)
    return False


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
    if GENERIC_TITLE_RE.search(raw["title"].strip()):
        return "generic_title"
    if raw.get("type") not in ALLOWED_TYPES:
        return "type_not_allowed"
    if raw.get("language") not in ALLOWED_LANGUAGES:
        return "non_english"
    # With complete author lists (see fetch_full_authorships) every UIUC work must have a
    # UIUC authorship; this also rejects "Illinois System" works from other campuses.
    if not raw.get("is_authors_truncated") and not any(
        is_uiuc_authorship(a, institution_id) for a in raw.get("authorships") or []
    ):
        return "no_uiuc_authorship"
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
        if is_uiuc_authorship(a, institution_id):
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
    field_id: str | None = None  # primary topic's field (for field-normalized ranking)
    # Institutions listed on the *same authorship* as UIUC (an author's second affiliation).
    # Not persisted; statistics use it to tell UIUC-internal units (Woese Institute, Prairie
    # Research Institute, ... almost always co-listed with UIUC) from external partners.
    co_uiuc_institution_ids: set[str] = field(default_factory=set)


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

    pub_date = date.fromisoformat(raw["publication_date"])
    all_authorships = raw.get("authorships") or []
    work = {
        "id": wid,
        "doi": normalize_doi(raw.get("doi")),
        "title": _strip_tags(raw.get("title")) or "",
        "abstract": clean_abstract(rebuild_abstract(raw.get("abstract_inverted_index"))),
        "publication_date": pub_date,
        # OpenAlex uses Jan 1 when only the year is known (7.7% of the corpus): treat as
        # year precision so "most recent" ordering/display does not over-trust it.
        "date_precision": "year" if (pub_date.month, pub_date.day) == (1, 1) else "day",
        "publication_year": raw.get("publication_year"),
        "type": raw.get("type"),
        "language": raw.get("language"),
        "cited_by_count": raw.get("cited_by_count") or 0,
        "fwci": raw.get("fwci"),
        "citation_percentile": cnp.get("value"),
        "is_top_1pct": bool(cnp.get("is_in_top_1_percent")),
        "is_top_10pct": bool(cnp.get("is_in_top_10_percent")),
        "is_oa": bool(oa.get("is_oa")),
        "oa_status": oa.get("oa_status"),
        "oa_url": oa.get("oa_url"),
        "landing_page_url": loc.get("landing_page_url"),
        "venue_name": source.get("display_name"),
        "venue_type": source.get("type"),
        "venue_id": short_id(source.get("id")),
        "primary_topic_id": short_id(primary_topic["id"]) if primary_topic else None,
        "keywords": [k["display_name"] for k in raw.get("keywords") or []] or None,
        "sdgs": [
            {"id": short_id(s.get("id")), "name": s.get("display_name"), "score": s.get("score")}
            for s in raw.get("sustainable_development_goals") or []
        ]
        or None,
        "funders": [
            {"id": short_id(f.get("id")), "name": f.get("display_name")}
            for f in raw.get("funders") or []
            if f.get("display_name")
        ]
        or None,
        "authors_count": len(all_authorships),
        "institutions_count": raw.get("institutions_distinct_count"),
        "countries_count": raw.get("countries_distinct_count"),
        "version_ids": None,  # filled by dedupe() when versions are merged
    }
    nw = NormalizedWork(
        work=work,
        citation_percentile=cnp.get("value"),
        field_id=short_id(((primary_topic or {}).get("field") or {}).get("id")),
    )

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

    by_author: dict[str, dict[str, Any]] = {}  # identified author -> their stored authorship
    seen_insts: set[str] = set()
    for order, a in enumerate(all_authorships):
        author = a.get("author") or {}
        aid = short_id(author.get("id"))  # None for unresolved authors (~7.5% in OpenAlex)
        insts = a.get("institutions") or []
        is_uiuc = is_uiuc_authorship(a, institution_id)
        raw_aff = " | ".join(a.get("raw_affiliation_strings") or []) or None
        for inst in insts:  # collaboration stats use the complete list
            iid = short_id(inst.get("id"))
            if iid and is_uiuc and not is_uiuc_institution(inst, institution_id):
                nw.co_uiuc_institution_ids.add(iid)
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
        if aid and aid in by_author:
            # Same person listed twice (OpenAlex duplicate): merge into the first entry so
            # a UIUC affiliation on the second listing is not lost.
            prev = by_author[aid]
            prev["is_uiuc"] = prev["is_uiuc"] or is_uiuc
            if raw_aff and raw_aff not in (prev["raw_affiliation"] or ""):
                prev["raw_affiliation"] = " | ".join(
                    filter(None, [prev["raw_affiliation"], raw_aff])
                )
            continue
        # Mega-collaborations list thousands of authors: store the first N (for display)
        # plus every UIUC authorship (for faculty linking).
        if order >= MAX_STORED_AUTHORSHIPS and not is_uiuc:
            continue
        name = author.get("display_name") or a.get("raw_author_name") or ""
        if aid:
            nw.authors.append(
                {
                    "id": aid,
                    "display_name": name,
                    "orcid": (author.get("orcid") or "").removeprefix("https://orcid.org/") or None,
                }
            )
        row = {
            "work_id": wid,
            "author_order": order,
            "author_id": aid,
            "author_name": name,
            "author_position": a.get("author_position"),
            "is_corresponding": bool(a.get("is_corresponding")),
            "is_uiuc": is_uiuc,
            "raw_affiliation": raw_aff,
        }
        if aid:
            by_author[aid] = row
        nw.authorships.append(row)
    return nw


# --------------------------------------------------------------------------- dedupe & rank


MIN_TITLE_WORDS_FOR_MATCH = 5  # shorter titles ("Introduction", "Osteosarcoma") are not unique


def _version_rank(w: NormalizedWork) -> tuple[int, int, int, str]:
    """Sort key: preferred version first (published type, non-repository venue, most cited)."""
    t = w.work["type"]
    type_rank = TYPE_PREFERENCE.index(t) if t in TYPE_PREFERENCE else len(TYPE_PREFERENCE)
    in_repository = 1 if w.work.get("venue_type") == "repository" else 0
    return (type_rank, in_repository, -int(w.work["cited_by_count"]), w.work["id"])


def dedupe(works: Sequence[NormalizedWork]) -> tuple[list[NormalizedWork], int]:
    """Merge multiple versions of the same work (e.g. arXiv preprint + journal/conference).

    Same version group when: same DOI, or same normalized title (>= 5 words, any year in
    the window) *and* at least one shared author family name. The canonical record is the preferred
    published version; it carries the highest citation count of the group and the other
    ids in ``version_ids``.
    """
    parent = list(range(len(works)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        parent[find(i)] = find(j)

    by_doi: dict[str, int] = {}
    by_title: dict[str, list[int]] = {}
    authors = [_family_names(w) for w in works]
    for i, w in enumerate(works):
        doi = w.work["doi"]
        if doi:
            if doi in by_doi:
                union(i, by_doi[doi])
            else:
                by_doi[doi] = i
        title = normalize_title(w.work["title"])
        if len(title.split()) >= MIN_TITLE_WORDS_FOR_MATCH:
            for j in by_title.get(title, []):
                if authors[i] & authors[j]:
                    union(i, j)
            by_title.setdefault(title, []).append(i)

    groups: dict[int, list[NormalizedWork]] = {}
    for i, w in enumerate(works):
        groups.setdefault(find(i), []).append(w)
    kept: list[NormalizedWork] = []
    for members in groups.values():
        members.sort(key=_version_rank)
        canonical = members[0]
        if len(members) > 1:
            canonical.work["cited_by_count"] = max(int(m.work["cited_by_count"]) for m in members)
            canonical.work["version_ids"] = [m.work["id"] for m in members[1:]]
        kept.append(canonical)
    return kept, len(works) - len(kept)


def percentile_ranks(values: Sequence[float | None], missing: float = 0.0) -> list[float]:
    """Percentile rank in [0, 1] (ties share the average rank); None -> ``missing``."""
    present = sorted(v for v in values if v is not None)
    n = len(present)
    if n == 0:
        return [missing for _ in values]
    first: dict[float, int] = {}
    last: dict[float, int] = {}
    for i, pv in enumerate(present):
        first.setdefault(pv, i)
        last[pv] = i
    out: list[float] = []
    for v in values:
        if v is None:
            out.append(missing)
        else:
            avg = (first[v] + last[v]) / 2
            out.append(avg / (n - 1) if n > 1 else 1.0)
    return out


@dataclass(frozen=True)
class RankWeights:
    normalized_citations: float = 0.4  # OpenAlex citation_normalized_percentile (shrunk)
    fwci: float = 0.2  # percentile of field-weighted citation impact (shrunk)
    raw_citations: float = 0.2  # percentile of cited_by_count within the work's field
    recency: float = 0.2  # linear 0..1 across the window
    # Pseudo-count for shrinkage: with c citations, normalized metrics get weight c/(c+k).
    prior_citations: float = 5.0


MIN_FIELD_GROUP = 30  # smaller fields fall back to the global ranking


def field_percentiles(works: Sequence[NormalizedWork], cites: Sequence[int]) -> list[float]:
    """Percentile of citation counts within each work's primary-topic field."""
    global_pct = percentile_ranks([float(c) for c in cites])
    groups: dict[str | None, list[int]] = {}
    for i, w in enumerate(works):
        groups.setdefault(w.field_id, []).append(i)
    out = list(global_pct)
    for fid, idx in groups.items():
        if fid is None or len(idx) < MIN_FIELD_GROUP:
            continue
        for i, p in zip(idx, percentile_ranks([float(cites[i]) for i in idx]), strict=True):
            out[i] = p
    return out


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
    """Composite "notability" score in [0, 1] (weights documented in RankWeights).

    Fairness across disciplines: raw citations are ranked *within the work's field*
    (citation rates differ by field; a global rank cut 26% of Economics vs 9% of Physics),
    and a missing FWCI (19% of works) is neutral (0.5), not zero.
    """
    cites = [int(w.work["cited_by_count"]) for w in works]
    raw_pct = field_percentiles(works, cites)
    fwci_pct = percentile_ranks([w.work["fwci"] for w in works], missing=0.5)
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
