"""Step 5: match directory faculty to OpenAlex authors; rank display keywords.

Inputs (all offline): ``faculty`` table (load_faculty), cached UIUC author directory
(fetch_authors), UIUC authorships in our works corpus (load_works).
Writes ``faculty_author_map`` (replaced), author profile metrics, ``faculty.keywords_top``.

Usage: uv run python -m pipeline.match_faculty
Report: data/processed/reports/match_faculty.json
"""

from __future__ import annotations

import json
import logging
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.db.models import Author, Faculty, FacultyAuthorMap, FacultyWork
from app.db.session import get_sessionmaker
from pipeline.db_bulk import copy_update, copy_upsert
from pipeline.faculty.keywords import rank_keywords
from pipeline.faculty.matching import (
    AuthorProfile,
    Candidate,
    FacultyInput,
    MatchConfig,
    UnresolvedAuthorship,
    add_corpus_evidence,
    attribute_unresolved,
    match_all,
    profiles_from_directory,
)
from pipeline.faculty.text import terms
from pipeline.fetch_authors import iter_raw_authors
from pipeline.paths import AUTHORS_RAW_DIR, REPORTS_DIR

log = logging.getLogger("pipeline.match_faculty")

CORPUS_SQL = text(
    """
    SELECT a.author_id, au.display_name, au.orcid, w.title, w.keywords,
           coalesce(t.name, '') || ' ' || coalesce(t.subfield, '') AS topic
    FROM authorships a
    JOIN authors au ON au.id = a.author_id
    JOIN works w ON w.id = a.work_id
    LEFT JOIN topics t ON t.id = w.primary_topic_id
    WHERE a.is_uiuc
    """
)


UNRESOLVED_SQL = text(
    """
    SELECT a.work_id, a.author_name,
           w.title || ' ' || coalesce(array_to_string(w.keywords, ' '), '') || ' ' ||
           coalesce(t.name, '') || ' ' || coalesce(t.subfield, '') AS text
    FROM authorships a
    JOIN works w ON w.id = a.work_id
    LEFT JOIN topics t ON t.id = w.primary_topic_id
    WHERE a.is_uiuc AND a.author_id IS NULL
    """
)


def load_inputs(session: Session) -> tuple[list[FacultyInput], dict[str, AuthorProfile]]:
    faculty = [
        FacultyInput(
            id=f.id, names=[f.name, *(f.name_variants or [])], keywords=f.keywords_clean or []
        )
        for f in session.scalars(select(Faculty).order_by(Faculty.id))
    ]
    profiles = profiles_from_directory(iter_raw_authors())
    add_corpus_evidence(profiles, (dict(r._mapping) for r in session.execute(CORPUS_SQL)))
    return faculty, profiles


def compute_keywords_top(
    faculty: Sequence[FacultyInput],
    accepted: Iterable[Candidate],
    profiles: Mapping[str, AuthorProfile],
) -> list[dict[str, Any]]:
    doc_freq: Counter[str] = Counter()
    for f in faculty:
        doc_freq.update(set(f.keywords))
    evidence: dict[int, set[str]] = {}
    for c in accepted:
        evidence.setdefault(c.faculty_id, set()).update(terms(" ".join(profiles[c.author_id].text)))
    return [
        {
            "id": f.id,
            "keywords_top": rank_keywords(
                f.keywords,
                doc_freq=doc_freq,
                n_docs=len(faculty),
                evidence_terms=evidence.get(f.id, set()),
            ),
        }
        for f in faculty
    ]


def build_report(
    faculty: Sequence[FacultyInput],
    accepted: Sequence[Candidate],
    scored: Mapping[int, Sequence[Candidate]],
    profiles: Mapping[str, AuthorProfile],
    cfg: MatchConfig,
) -> dict[str, Any]:
    by_fac: dict[int, list[Candidate]] = {}
    for c in accepted:
        by_fac.setdefault(c.faculty_id, []).append(c)
    status: Counter[str] = Counter()
    unmatched = []
    for f in faculty:
        if f.id in by_fac:
            status["matched"] += 1
            continue
        best = scored[f.id][0] if scored[f.id] else None
        reason = best.method if best else "no_candidate"
        status[f"unmatched:{reason}"] += 1
        unmatched.append(
            {
                "faculty": f.names[0],
                "reason": reason,
                "best_candidate": None
                if best is None
                else {
                    "author_id": best.author_id,
                    "names": best.evidence["author_names"][:3],
                    "name": best.name,
                    "topic": best.topic,
                    "confidence": best.confidence,
                },
            }
        )
    conf_hist = Counter(f"{int(c.confidence * 10) / 10:.1f}" for c in accepted)
    return {
        "config": cfg.__dict__,
        "faculty": len(faculty),
        "matched_faculty": len(by_fac),
        "match_rate": round(len(by_fac) / max(len(faculty), 1), 4),
        "matched_with_corpus_works": sum(
            1 for v in by_fac.values() if any(profiles[c.author_id].corpus_uiuc_works for c in v)
        ),
        "links": len(accepted),
        "links_per_matched_faculty": dict(Counter(min(len(v), 5) for v in by_fac.values())),
        "links_by_method": dict(Counter(c.method for c in accepted)),
        "confidence_histogram": dict(sorted(conf_hist.items())),
        "status": dict(status.most_common()),
        "lowest_confidence_links": [
            {
                "faculty": next(f.names[0] for f in faculty if f.id == c.faculty_id),
                "author_id": c.author_id,
                "names": c.evidence["author_names"][:3],
                "confidence": c.confidence,
                "method": c.method,
            }
            for c in sorted(accepted, key=lambda c: c.confidence)[:20]
        ],
        "unmatched": unmatched,
    }


def write_results(
    session: Session,
    accepted: Sequence[Candidate],
    profiles: Mapping[str, AuthorProfile],
    keywords_top: Sequence[dict[str, Any]],
    unresolved_links: Sequence[tuple[int, str]] = (),
) -> None:
    # 1) author rows: linked directory authors (may be absent from the works corpus) and
    #    profile metrics for every directory author we already store.
    existing = set(session.scalars(select(Author.id)))
    linked = {c.author_id for c in accepted}
    author_rows = [
        {
            "id": p.id,
            "display_name": p.names[0],
            "orcid": p.orcid,
            "works_count": p.works_count,
            "cited_by_count": p.cited_by_count,
            "h_index": p.h_index,
        }
        for p in profiles.values()
        if p.in_directory and (p.id in linked or p.id in existing)
    ]
    copy_upsert(session, Author, author_rows, key=["id"])

    # 2) links (fully derived -> replace)
    session.execute(delete(FacultyAuthorMap))
    copy_upsert(
        session,
        FacultyAuthorMap,
        [
            {
                "faculty_id": c.faculty_id,
                "author_id": c.author_id,
                "confidence": c.confidence,
                "method": c.method,
                "evidence": {"name": c.name, "topic": c.topic, **c.evidence},
            }
            for c in accepted
        ],
        key=["faculty_id", "author_id"],
    )
    # 3) display keywords
    copy_update(session, Faculty, keywords_top, key=["id"])

    # 4) faculty -> works: every authorship of a linked author id, plus unresolved UIUC
    #    authorships attributed by name (see matching.attribute_unresolved)
    session.execute(delete(FacultyWork))
    session.execute(
        text(
            """
            INSERT INTO faculty_works (faculty_id, work_id, via)
            SELECT DISTINCT m.faculty_id, a.work_id, 'author_id'
            FROM faculty_author_map m JOIN authorships a ON a.author_id = m.author_id
            ON CONFLICT DO NOTHING
            """
        )
    )
    copy_upsert(
        session,
        FacultyWork,
        [{"faculty_id": f, "work_id": w, "via": "unresolved_name"} for f, w in unresolved_links],
        key=["faculty_id", "work_id"],
        on_conflict="nothing",
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not any(AUTHORS_RAW_DIR.glob("*.jsonl.gz")):
        log.error("No cached authors - run `python -m pipeline.fetch_authors` first.")
        return 1
    cfg = MatchConfig()
    with get_sessionmaker()() as session:
        faculty, profiles = load_inputs(session)
    if not faculty:
        log.error("No faculty in DB - run `python -m pipeline.load_faculty` first.")
        return 1

    accepted, scored = match_all(faculty, profiles, cfg)
    keywords_top = compute_keywords_top(faculty, accepted, profiles)
    with get_sessionmaker()() as session:
        unresolved = [
            UnresolvedAuthorship(work_id=r.work_id, author_name=r.author_name, text=r.text)
            for r in session.execute(UNRESOLVED_SQL)
        ]
    unresolved_links = attribute_unresolved(faculty, unresolved, cfg)
    with get_sessionmaker()() as session, session.begin():
        write_results(session, accepted, profiles, keywords_top, unresolved_links)

    report = build_report(faculty, accepted, scored, profiles, cfg)
    with get_sessionmaker()() as session:
        via: dict[str, int] = dict(
            session.execute(text("SELECT via, count(*) FROM faculty_works GROUP BY via")).all()
        )
        report["faculty_works"] = {
            "by_via": via,
            "unresolved_uiuc_authorships": len(unresolved),
            "faculty_with_works": session.scalar(
                text("SELECT count(DISTINCT faculty_id) FROM faculty_works")
            ),
        }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / "match_faculty.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(
        "Matched %d/%d faculty (%.1f%%), %d links, status=%s. Report: %s",
        report["matched_faculty"],
        report["faculty"],
        100 * report["match_rate"],
        report["links"],
        report["status"],
        out,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
