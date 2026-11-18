"""Step 4: load the faculty directory CSV into Postgres (parse, merge duplicates, clean keywords).

Offline and idempotent (upsert by email; people no longer in the CSV are removed).

Usage: uv run python -m pipeline.load_faculty
Report: data/processed/reports/load_faculty.json
"""

from __future__ import annotations

import json
import logging
import sys
from collections import Counter
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import Faculty
from app.db.session import get_sessionmaker
from pipeline.db_bulk import copy_upsert
from pipeline.faculty.keywords import clean_keywords, normalize_keyword
from pipeline.faculty.names import first_last
from pipeline.faculty.parse import FacultyRecord, normalize_name, read_faculty_csv
from pipeline.faculty.position import classify_position
from pipeline.paths import FACULTY_CSV, REPORTS_DIR

log = logging.getLogger("pipeline.load_faculty")


def person_name_set(records: Sequence[FacultyRecord]) -> frozenset[str]:
    names = (first_last(n) for r in records for n in [r.name, *r.name_variants])
    return frozenset(n for n in names if n)


def faculty_rows(records: Sequence[FacultyRecord]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    people = person_name_set(records)
    rows: list[dict[str, Any]] = []
    raw_mentions = kept_mentions = 0
    dropped: Counter[str] = Counter()
    for r in records:
        clean = clean_keywords(r.keywords_raw, people)
        clean_set = set(clean)
        raw_mentions += len(r.keywords_raw)
        kept_mentions += len(clean)
        for k in r.keywords_raw:
            nk = normalize_keyword(k)
            if nk not in clean_set:
                dropped[nk] += 1
        pos = classify_position(r.position)
        rows.append(
            {
                "email": r.email,
                "name": r.name,
                "name_norm": normalize_name(r.name),
                "name_variants": r.name_variants or None,
                "position": r.position,
                "rank": pos.rank,
                "appointment": pos.appointment,
                "is_emeritus": pos.is_emeritus,
                "photo_url": r.photo_url,
                "keywords_raw": r.keywords_raw,
                "keywords_clean": clean,
            }
        )
    report = {
        "faculty": len(rows),
        "merged_duplicate_emails": [
            {"email": r.email, "name": r.name, "variants": r.name_variants}
            for r in records
            if r.name_variants
        ],
        "ranks": dict(Counter(r["rank"] for r in rows).most_common()),
        "appointments": dict(Counter(r["appointment"] for r in rows).most_common()),
        "emeritus": sum(1 for r in rows if r["is_emeritus"]),
        "missing_photo": sum(1 for r in records if not r.photo_url),
        "missing_position": sum(1 for r in records if not r.position),
        "shared_names": sorted(
            n for n, c in Counter(normalize_name(r.name) for r in records).items() if c > 1
        ),
        "keywords": {
            "raw_mentions": raw_mentions,
            "kept_mentions": kept_mentions,
            "dropped_or_deduped_mentions": raw_mentions - kept_mentions,
            "kept_per_person_min": min((len(r["keywords_clean"]) for r in rows), default=0),
            "kept_per_person_max": max((len(r["keywords_clean"]) for r in rows), default=0),
            "top_dropped": dropped.most_common(40),
        },
    }
    return rows, report


def load_faculty(session: Session, rows: Sequence[dict[str, Any]]) -> None:
    copy_upsert(session, Faculty, rows, key=["email"])
    session.execute(
        text("DELETE FROM faculty WHERE NOT (email = ANY(:emails))"),
        {"emails": [r["email"] for r in rows]},
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    records = read_faculty_csv(FACULTY_CSV)
    rows, report = faculty_rows(records)
    with get_sessionmaker()() as session, session.begin():
        load_faculty(session, rows)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / "load_faculty.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(
        "Loaded %d faculty (%d merged duplicates); keywords kept %d/%d. Report: %s",
        report["faculty"],
        len(report["merged_duplicate_emails"]),
        report["keywords"]["kept_mentions"],
        report["keywords"]["raw_mentions"],
        out,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
