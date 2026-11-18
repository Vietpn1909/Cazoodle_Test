"""Step 2: cached raw works -> quality filter -> dedupe -> rank -> load top N into Postgres.

Offline: reads only ``data/raw/openalex/works`` (no API calls), so it is free to re-run.

Usage:
    uv run python -m pipeline.load_works [--top-n 20000]

Writes ``data/processed/reports/load_works.json`` with counts dropped per rule.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    Author,
    Authorship,
    FacultyAuthorMap,
    Meta,
    Topic,
    Work,
    WorkInstitution,
    WorkTopic,
)
from app.db.session import get_sessionmaker
from pipeline.db_bulk import copy_upsert
from pipeline.fetch_full_authorships import load_full_authorships
from pipeline.fetch_works import iter_raw_works
from pipeline.openalex.transform import (
    NormalizedWork,
    compute_rank_scores,
    count_by,
    dedupe,
    drop_reason,
    normalize_work,
    short_id,
)
from pipeline.paths import REPORTS_DIR

log = logging.getLogger("pipeline.load_works")

DEFAULT_TOP_N = 20_000


@dataclass
class Corpus:
    works: list[NormalizedWork]
    report: dict[str, Any]


def build_corpus(
    raw_works: Iterable[dict[str, Any]],
    *,
    institution_id: str,
    window_start: date,
    window_end: date,
    top_n: int,
    full_authorships: Mapping[str, list[dict[str, Any]]] | None = None,
) -> Corpus:
    """``full_authorships``: complete author lists for works truncated at 100 authors."""
    full_authorships = full_authorships or {}
    total = 0
    completed = 0
    drops: Counter[str] = Counter()
    seen: set[str] = set()
    candidates: list[NormalizedWork] = []
    raw_types: Counter[str] = Counter()

    for raw in raw_works:
        total += 1
        raw_types[str(raw.get("type"))] += 1
        if raw["id"] in seen:
            drops["duplicate_id"] += 1
            continue
        seen.add(raw["id"])
        full = full_authorships.get(str(short_id(raw["id"])))
        if raw.get("is_authors_truncated") and full:
            raw = {**raw, "authorships": full, "is_authors_truncated": False}
            completed += 1
        reason = drop_reason(raw, institution_id)
        if reason:
            drops[reason] += 1
            continue
        candidates.append(normalize_work(raw, institution_id))

    unique, n_dupes = dedupe(candidates)
    drops["merged_versions"] = n_dupes

    scores = compute_rank_scores(unique, window_start, window_end)
    for w, s in zip(unique, scores, strict=True):
        w.work["rank_score"] = s
    unique.sort(key=lambda w: w.work["rank_score"], reverse=True)
    selected = unique[:top_n]

    report = {
        "raw_records": total,
        "truncated_authorships_completed": completed,
        "dropped": dict(drops.most_common()),
        "eligible_after_filters": len(unique),
        "top_n": top_n,
        "selected": len(selected),
        "cut_by_rank": len(unique) - len(selected),
        "min_selected_rank_score": selected[-1].work["rank_score"] if selected else None,
        "raw_types": dict(raw_types.most_common()),
        "selected_types": count_by(w.work["type"] for w in selected),
        "selected_languages": count_by(w.work["language"] for w in selected),
        "selected_by_year": count_by(w.work["publication_year"] for w in selected),
        "selected_with_abstract": sum(1 for w in selected if w.work["abstract"]),
        "selected_year_precision_dates": sum(
            1 for w in selected if w.work["date_precision"] == "year"
        ),
        "selected_with_merged_versions": sum(1 for w in selected if w.work["version_ids"]),
        "selected_without_uiuc_authorship": sum(
            1 for w in selected if not any(a["is_uiuc"] for a in w.authorships)
        ),
        "selected_by_field": count_by(w.field_id for w in selected),
    }
    return Corpus(works=selected, report=report)


def _unique(rows: Iterable[dict[str, Any]], key: str = "id") -> list[dict[str, Any]]:
    out: dict[Any, dict[str, Any]] = {}
    for r in rows:
        out.setdefault(r[key], r)
    return list(out.values())


def load_corpus(session: Session, works: Sequence[NormalizedWork], meta: dict[str, str]) -> None:
    """Replace the works corpus in one transaction (embeddings of unchanged works are kept)."""
    topics = _unique(t for w in works for t in w.topics)
    authors = _unique(a for w in works for a in w.authors)
    work_rows = [w.work for w in works]
    keep_ids = [w["id"] for w in work_rows]

    copy_upsert(session, Topic, topics, key=["id"])
    copy_upsert(session, Author, authors, key=["id"])
    copy_upsert(session, Work, work_rows, key=["id"])  # embedding untouched

    # Works that fell out of the corpus (cascade removes their child rows).
    session.execute(text("DELETE FROM works WHERE NOT (id = ANY(:ids))"), {"ids": keep_ids})
    # Child tables are fully derived from the current corpus -> rebuild.
    session.execute(text("TRUNCATE authorships, work_topics, work_institutions"))
    children: list[tuple[Any, list[str], list[dict[str, Any]]]] = [
        (Authorship, ["work_id", "author_order"], [a for w in works for a in w.authorships]),
        (WorkTopic, ["work_id", "topic_id"], [t for w in works for t in w.work_topics]),
        (
            WorkInstitution,
            ["work_id", "institution_id"],
            [i for w in works for i in w.work_institutions],
        ),
    ]
    for model, key, rows in children:
        copy_upsert(session, model, rows, key=key, on_conflict="nothing")

    # Authors no longer referenced anywhere. The IS NOT NULL filter matters: authorship
    # author_id is nullable and `x NOT IN (... NULL ...)` is never true in SQL.
    session.execute(
        delete(Author)
        .where(
            ~Author.id.in_(select(Authorship.author_id).where(Authorship.author_id.is_not(None)))
        )
        .where(~Author.id.in_(select(FacultyAuthorMap.author_id))),
        execution_options={"synchronize_session": False},
    )
    copy_upsert(session, Meta, [{"key": k, "value": v} for k, v in meta.items()], key=["key"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load cached OpenAlex works into Postgres")
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = get_settings()
    t0 = time.perf_counter()
    corpus = build_corpus(
        iter_raw_works(),
        institution_id=settings.openalex_institution_id,
        window_start=settings.window_start,
        window_end=settings.snapshot_date,
        top_n=args.top_n,
        full_authorships=load_full_authorships(),
    )
    if not corpus.works:
        log.error("No works found in data/raw - run `python -m pipeline.fetch_works` first.")
        return 1
    log.info(
        "Built corpus in %.1fs: %s", time.perf_counter() - t0, json.dumps(corpus.report["dropped"])
    )

    meta = {
        "snapshot_date": settings.snapshot_date.isoformat(),
        "window_start": settings.window_start.isoformat(),
        "institution_id": settings.openalex_institution_id,
        "works_top_n": str(args.top_n),
    }
    t1 = time.perf_counter()
    with get_sessionmaker()() as session, session.begin():
        load_corpus(session, corpus.works, meta)
    with get_sessionmaker()() as session:
        counts = {
            m.__tablename__: session.scalar(select(func.count()).select_from(m))
            for m in (Work, Author, Authorship, Topic, WorkTopic, WorkInstitution)
        }
    corpus.report["db_counts"] = counts
    log.info("Loaded in %.1fs: %s", time.perf_counter() - t1, counts)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / "load_works.json"
    out.write_text(json.dumps(corpus.report, indent=2, default=str), encoding="utf-8")
    log.info("Report: %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
