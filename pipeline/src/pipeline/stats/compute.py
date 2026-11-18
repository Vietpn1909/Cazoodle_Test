"""Portal statistics computed from the *eligible* works (pure functions, no I/O).

Why the eligible set (all works that pass the quality filters, ~23.7k) and not the 20k works
loaded for the assistant: the top-N cut is ranked by notability + recency, so the loaded corpus
keeps 100% of 2026 but only 58-70% of late-2024/2025 works. Charts built from it would show
growth that does not exist. The portal states both numbers ("indexed for the assistant").

Conventions
- Monthly series use day-precision dates only. OpenAlex stores year-only dates as Jan 1
  (2,137 works) which would create fake January spikes; those are reported separately.
- "Rising topics" compare *shares* between the two halves of the window (not raw counts), so
  the overall growth of the index does not make every topic look like it is rising.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Sequence
from datetime import date, timedelta
from typing import Any

from pipeline.openalex.transform import NormalizedWork

UNCLASSIFIED = "Unclassified"

TYPE_BUCKETS: dict[str, str] = {
    "article": "Journal articles",
    "review": "Journal articles",
    "preprint": "Preprints",
    "conference-paper": "Conference papers",
}
OTHER_BUCKET = "Other"
BUCKET_ORDER = ["Journal articles", "Preprints", "Conference papers", OTHER_BUCKET]

MIN_TOPIC_WORKS = 40  # topics below this (both halves combined) are too noisy to rank
MIN_SUBFIELD_WORKS = 80
RISING_LIMIT = 15
PLATFORM_VENUE_TYPES = frozenset({"ebook platform", "metadata"})
MIN_COAFFIL_WORKS = (
    15  # an institution needs this many works before it can be inferred as a UIUC unit
)
COAFFIL_RATIO = 0.9


def _r(x: float, nd: int = 4) -> float:
    return round(x, nd)


def bucket(work_type: str) -> str:
    return TYPE_BUCKETS.get(work_type, OTHER_BUCKET)


def month_keys(start: date, end: date) -> list[str]:
    out, cur = [], start.replace(day=1)
    while cur <= end:
        out.append(cur.strftime("%Y-%m"))
        cur = (cur + timedelta(days=32)).replace(day=1)
    return out


def _primary_topic(w: NormalizedWork) -> dict[str, Any] | None:
    pid = w.work.get("primary_topic_id")
    return next((t for t in w.topics if t["id"] == pid), None) if pid else None


# --------------------------------------------------------------------------- output


def output_stats(works: Sequence[NormalizedWork], start: date, end: date) -> dict[str, Any]:
    months = month_keys(start, end)
    by_month: dict[str, Counter[str]] = {m: Counter() for m in months}
    year_only: Counter[int] = Counter()
    years: Counter[int] = Counter()
    for w in works:
        d, y = w.work["publication_date"], w.work["publication_year"]
        years[y] += 1
        if w.work["date_precision"] == "year":
            year_only[y] += 1
        else:
            by_month[d.strftime("%Y-%m")][bucket(w.work["type"])] += 1
    series = []
    for m in months:
        first = date.fromisoformat(m + "-01")
        last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        series.append(
            {
                "month": m,
                "works": sum(by_month[m].values()),
                "by_type": {b: by_month[m].get(b, 0) for b in BUCKET_ORDER},
                # first/last month cover only part of the window
                "partial": first < start or last > end,
            }
        )
    types = Counter(w.work["type"] for w in works)
    return {
        "scope": "eligible",
        "bucket_order": BUCKET_ORDER,
        "by_month": series,
        "by_year": {str(y): n for y, n in sorted(years.items())},
        "year_only_dates_by_year": {str(y): n for y, n in sorted(year_only.items())},
        "year_only_dates_total": sum(year_only.values()),
        "by_type": dict(types.most_common()),
        "by_type_bucket": {
            b: sum(n for t, n in types.items() if bucket(t) == b) for b in BUCKET_ORDER
        },
    }


# --------------------------------------------------------------------------- focus areas


def fields_stats(works: Sequence[NormalizedWork]) -> dict[str, Any]:
    """Domain -> field -> subfield tree by primary topic (works, citations, top topics)."""
    leaf: dict[tuple[str, str, str], dict[str, Any]] = {}
    topic_counts: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    for w in works:
        t = _primary_topic(w)
        key = (
            (
                t.get("domain") or UNCLASSIFIED,
                t.get("field") or UNCLASSIFIED,
                t.get("subfield") or UNCLASSIFIED,
            )
            if t
            else (UNCLASSIFIED, UNCLASSIFIED, UNCLASSIFIED)
        )
        node = leaf.setdefault(key, {"works": 0, "citations": 0})
        node["works"] += 1
        node["citations"] += w.work["cited_by_count"]
        if t:
            topic_counts[key][t["name"]] += 1

    tree: dict[str, dict[str, dict[str, Any]]] = {}
    for (domain, field, subfield), v in leaf.items():
        tree.setdefault(domain, {}).setdefault(field, {})[subfield] = {
            "name": subfield,
            **v,
            "top_topics": [
                {"name": n, "works": c}
                for n, c in topic_counts[(domain, field, subfield)].most_common(3)
            ],
        }

    def agg(children: Iterable[dict[str, Any]]) -> dict[str, int]:
        kids = list(children)
        return {
            "works": sum(k["works"] for k in kids),
            "citations": sum(k["citations"] for k in kids),
        }

    domains: list[dict[str, Any]] = []
    for dname, fields in tree.items():
        field_nodes: list[dict[str, Any]] = []
        for fname, subs in fields.items():
            subfields = sorted(subs.values(), key=lambda s: -s["works"])
            field_nodes.append({"name": fname, **agg(subfields), "children": subfields})
        field_nodes.sort(key=lambda f: -f["works"])
        domains.append({"name": dname, **agg(field_nodes), "children": field_nodes})
    domains.sort(key=lambda d: -d["works"])

    topics: Counter[str] = Counter()
    topic_field: dict[str, str] = {}
    for w in works:
        t = _primary_topic(w)
        if t:
            topics[t["name"]] += 1
            topic_field[t["name"]] = t.get("field") or UNCLASSIFIED
    classified = sum(topics.values())
    return {
        "scope": "eligible",
        "works": len(works),
        "classified_works": classified,
        "domains": domains,
        "top_topics": [
            {"name": n, "field": topic_field[n], "works": c} for n, c in topics.most_common(30)
        ],
        "distinct_topics": len(topics),
    }


def _rising(
    counts_a: Counter[str],
    counts_b: Counter[str],
    n_a: int,
    n_b: int,
    labels: dict[str, str],
    min_total: int,
) -> list[dict[str, Any]]:
    rows = []
    for name in set(counts_a) | set(counts_b):
        a, b = counts_a[name], counts_b[name]
        if a + b < min_total:
            continue
        # additive smoothing keeps tiny denominators from producing absurd ratios
        share_a, share_b = (a + 2) / (n_a + 2), (b + 2) / (n_b + 2)
        rows.append(
            {
                "name": name,
                "field": labels.get(name),
                "recent": a,
                "previous": b,
                "share_recent": _r(a / n_a, 5) if n_a else 0.0,
                "share_previous": _r(b / n_b, 5) if n_b else 0.0,
                "growth": _r(share_a / share_b),
                "log2_growth": _r(math.log2(share_a / share_b)),
            }
        )
    return rows


def rising_stats(works: Sequence[NormalizedWork], start: date, end: date) -> dict[str, Any]:
    mid = start + (end - start) / 2
    topic_a: Counter[str] = Counter()
    topic_b: Counter[str] = Counter()
    sub_a: Counter[str] = Counter()
    sub_b: Counter[str] = Counter()
    labels_topic: dict[str, str] = {}
    labels_sub: dict[str, str] = {}
    n_a = n_b = 0
    for w in works:
        t = _primary_topic(w)
        # Works without an identified venue include bulk auto-generated records (e.g. ~90
        # taxonomic species descriptions) that would dominate "rising" with no real trend.
        if w.work["date_precision"] != "day" or t is None or not w.work.get("venue_name"):
            continue
        recent = w.work["publication_date"] >= mid
        n_a, n_b = n_a + recent, n_b + (not recent)
        (topic_a if recent else topic_b)[t["name"]] += 1
        (sub_a if recent else sub_b)[t.get("subfield") or UNCLASSIFIED] += 1
        labels_topic[t["name"]] = t.get("field") or UNCLASSIFIED
        labels_sub[t.get("subfield") or UNCLASSIFIED] = t.get("field") or UNCLASSIFIED

    def top(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        rows.sort(key=lambda r: (-r["log2_growth"], -r["recent"], r["name"]))
        rising = [r for r in rows if r["log2_growth"] > 0][:RISING_LIMIT]
        declining = sorted(rows, key=lambda r: (r["log2_growth"], -r["previous"], r["name"]))
        return {"rising": rising, "declining": [r for r in declining if r["log2_growth"] < 0][:10]}

    return {
        "scope": "eligible works with day-precision dates and an identified venue",
        "period_recent": [mid.isoformat(), end.isoformat()],
        "period_previous": [start.isoformat(), (mid - timedelta(days=1)).isoformat()],
        "works_recent": n_a,
        "works_previous": n_b,
        "min_works_topic": MIN_TOPIC_WORKS,
        "min_works_subfield": MIN_SUBFIELD_WORKS,
        "topics": top(_rising(topic_a, topic_b, n_a, n_b, labels_topic, MIN_TOPIC_WORKS)),
        "subfields": top(_rising(sub_a, sub_b, n_a, n_b, labels_sub, MIN_SUBFIELD_WORKS)),
    }


# --------------------------------------------------------------------------- venues, SDGs, funders


def clean_venue_name(name: str) -> str:
    """OpenAlex sometimes doubles a name: ``"Physical review. D/Physical review. D."``."""
    parts = [p.strip() for p in name.split("/")]
    if len(parts) == 2 and parts[0].lower().rstrip(".") == parts[1].lower().rstrip("."):
        return parts[0]
    return name.strip()


def venue_stats(works: Sequence[NormalizedWork]) -> dict[str, Any]:
    agg: dict[str, dict[str, Any]] = {}
    for w in works:
        name = w.work.get("venue_name")
        if not name:
            continue
        name = clean_venue_name(name)
        key = w.work.get("venue_id") or name
        v = agg.setdefault(
            key, {"name": name, "type": w.work.get("venue_type"), "works": 0, "citations": 0}
        )
        v["works"] += 1
        v["citations"] += w.work["cited_by_count"]

    def top(rows: Iterable[dict[str, Any]], n: int) -> list[dict[str, Any]]:
        return sorted(rows, key=lambda r: (-r["works"], -r["citations"], r["name"]))[:n]

    # Platforms ("Springer eBooks") say nothing about where a work was published.
    published = [v for v in agg.values() if v["type"] not in {"repository", *PLATFORM_VENUE_TYPES}]
    repos = [v for v in agg.values() if v["type"] == "repository"]
    return {
        "scope": "eligible",
        "works_with_venue": sum(v["works"] for v in agg.values()),
        "works_on_platforms": sum(
            v["works"] for v in agg.values() if v["type"] in PLATFORM_VENUE_TYPES
        ),
        "distinct_venues": len(agg),
        "top_published": top(published, 20),  # journals, conferences, book series, ...
        "top_repositories": top(repos, 10),  # arXiv, SSRN, Zenodo, ...
    }


def sdg_stats(works: Sequence[NormalizedWork]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    names: dict[str, str] = {}
    with_sdg = 0
    for w in works:
        sdgs = w.work.get("sdgs") or []
        if sdgs:
            with_sdg += 1
        for s in sdgs:
            counts[s["id"]] += 1
            names[s["id"]] = s["name"]
    return {
        "scope": "eligible",
        "works_with_sdg": with_sdg,
        "share_with_sdg": _r(with_sdg / len(works)) if works else 0.0,
        "goals": [
            {"id": int(i), "name": names[i], "works": counts[i]}
            for i in sorted(counts, key=lambda x: int(x))
        ],
    }


def funder_stats(works: Sequence[NormalizedWork]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    with_funder = 0
    for w in works:
        funders = w.work.get("funders") or []
        with_funder += bool(funders)
        for name in {f["name"] for f in funders}:
            counts[name] += 1
    return {
        "scope": "eligible",
        "works_with_funder": with_funder,
        "share_with_funder": _r(with_funder / len(works)) if works else 0.0,
        "top": [{"name": n, "works": c} for n, c in counts.most_common(20)],
    }


# --------------------------------------------------------------------------- collaboration


def infer_uiuc_units(
    works: Sequence[NormalizedWork],
    known_unit_ids: Collection[str],
    *,
    min_works: int = MIN_COAFFIL_WORKS,
    ratio: float = COAFFIL_RATIO,
) -> tuple[set[str], list[dict[str, Any]]]:
    """UIUC-internal units that OpenAlex does not list under UIUC's lineage.

    An institution that is (almost) always listed on the same authorship as UIUC is a unit of
    UIUC (Woese Institute 1.00, Prairie Research Institute 1.00, Illinois Natural History
    Survey 0.98, ...); a partner university is rarely so (Michigan 0.03, Argonne 0.24). A
    genuine joint institute stays below the threshold (Zhejiang University 0.73, ZJU-UIUC).
    Small units / partners with < ``min_works`` works are never inferred. Returns the full
    exclusion set and a transparent report of what was excluded.
    """
    total: Counter[str] = Counter()
    co: Counter[str] = Counter()
    meta: dict[str, dict[str, Any]] = {}
    for w in works:
        for i in w.work_institutions:
            total[i["institution_id"]] += 1
            meta.setdefault(i["institution_id"], i)
        for iid in w.co_uiuc_institution_ids:
            co[iid] += 1
    inferred = {
        iid
        for iid, n in total.items()
        if iid not in known_unit_ids and n >= min_works and co[iid] / n >= ratio
    }
    report = [
        {
            "id": iid,
            "name": meta[iid]["name"],
            "works": total[iid],
            "co_affiliation_ratio": _r(co[iid] / total[iid], 2),
            "reason": "inferred" if iid in inferred else "lineage",
        }
        for iid in total
        if iid in inferred or iid in known_unit_ids
    ]
    report.sort(key=lambda r: (-r["works"], r["name"] or ""))
    return set(known_unit_ids) | inferred, report


def collaboration_stats(
    works: Sequence[NormalizedWork], uiuc_unit_ids: Collection[str]
) -> dict[str, Any]:
    excluded, excluded_report = infer_uiuc_units(works, uiuc_unit_ids)
    inst: dict[str, dict[str, Any]] = {}
    countries: Counter[str] = Counter()
    external_works = international = 0
    for w in works:
        ext = {
            i["institution_id"]: i
            for i in w.work_institutions
            if i["institution_id"] not in excluded
        }
        for iid, i in ext.items():
            row = inst.setdefault(
                iid,
                {
                    "id": iid,
                    "name": i["name"],
                    "country": i["country_code"],
                    "type": i["type"],
                    "works": 0,
                },
            )
            row["works"] += 1
        external_works += bool(ext)
        codes = {i["country_code"] for i in w.work_institutions if i["country_code"]}
        for c in codes:
            countries[c] += 1
        international += any(c != "US" for c in codes)
    n = len(works)
    return {
        "scope": "eligible",
        "works_with_external_institution": external_works,
        "share_with_external_institution": _r(external_works / n) if n else 0.0,
        "works_with_foreign_institution": international,
        "share_international": _r(international / n) if n else 0.0,
        "distinct_institutions": len(inst),
        "distinct_countries": len(countries),
        "excluded_uiuc_units": excluded_report[:25],
        "institutions": sorted(inst.values(), key=lambda r: (-r["works"], r["name"] or ""))[:30],
        "countries": [{"code": c, "works": k} for c, k in countries.most_common(30)],
    }


# --------------------------------------------------------------------------- overview


def overview_stats(
    works: Sequence[NormalizedWork],
    *,
    start: date,
    end: date,
    indexed_works: int,
    faculty: dict[str, int],
) -> dict[str, Any]:
    n = len(works)
    cites = [w.work["cited_by_count"] for w in works]
    fwci = sorted(w.work["fwci"] for w in works if w.work["fwci"] is not None)
    oa = Counter(w.work.get("oa_status") or "unknown" for w in works)
    uiuc_authors = {
        a["author_id"] for w in works for a in w.authorships if a["is_uiuc"] and a["author_id"]
    }
    return {
        "scope": "eligible",
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "works": n,
        "indexed_works": indexed_works,
        "citations_total": sum(cites),
        "citations_mean": _r(statistics.fmean(cites), 2) if cites else 0.0,
        "citations_median": statistics.median(cites) if cites else 0,
        "works_cited": sum(1 for c in cites if c > 0),
        "open_access_works": sum(1 for w in works if w.work["is_oa"]),
        "open_access_share": _r(sum(1 for w in works if w.work["is_oa"]) / n) if n else 0.0,
        "oa_status": dict(oa.most_common()),
        "fwci_median": _r(statistics.median(fwci), 3) if fwci else None,
        "fwci_p90": _r(fwci[int(0.9 * (len(fwci) - 1))], 3) if fwci else None,
        "fwci_available_share": _r(len(fwci) / n) if n else 0.0,
        "top_1_percent_works": sum(1 for w in works if w.work["is_top_1pct"]),
        "top_10_percent_works": sum(1 for w in works if w.work["is_top_10pct"]),
        "uiuc_authors": len(uiuc_authors),
        "faculty_directory": faculty.get("directory", 0),
        "faculty_matched": faculty.get("matched", 0),
        "faculty_with_works": faculty.get("with_works", 0),
    }


def compute_stats(
    works: Sequence[NormalizedWork],
    *,
    start: date,
    end: date,
    uiuc_unit_ids: Collection[str],
    indexed_works: int,
    faculty: dict[str, int],
) -> dict[str, dict[str, Any]]:
    """All portal statistics, keyed by ``stats_kv`` key."""
    return {
        "overview": overview_stats(
            works, start=start, end=end, indexed_works=indexed_works, faculty=faculty
        ),
        "output": output_stats(works, start, end),
        "fields": fields_stats(works),
        "rising_topics": rising_stats(works, start, end),
        "venues": venue_stats(works),
        "sdgs": sdg_stats(works),
        "funders": funder_stats(works),
        "collaborators": collaboration_stats(works, uiuc_unit_ids),
    }
