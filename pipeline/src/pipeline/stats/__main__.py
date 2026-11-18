"""Step 7: compute portal statistics and store them in ``stats_kv`` (offline, idempotent).

Reads the raw works cache (same quality rules as ``load_works``, *without* the top-N cut) and
the database (indexed-works and faculty counts); writes one JSON document per key to
``stats_kv`` so the API only reads precomputed rows.

Usage: uv run python -m pipeline.stats
Output: stats_kv rows + data/processed/stats.json (human-readable copy)
"""

from __future__ import annotations

import json
import logging
import sys
import time

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from pipeline.fetch_full_authorships import load_full_authorships
from pipeline.fetch_works import iter_raw_works
from pipeline.load_works import build_corpus
from pipeline.openalex.transform import ILLINOIS_SYSTEM_ID, short_id
from pipeline.paths import INSTITUTION_CHILDREN_FILE, PROCESSED_DIR
from pipeline.stats.compute import compute_stats
from pipeline.stats.store import store_stats

log = logging.getLogger("pipeline.stats")


def uiuc_unit_ids(institution_id: str) -> set[str]:
    """UIUC, its parent system, and every lineage unit (NCSA, State Geological Survey, ...)."""
    ids = {institution_id, ILLINOIS_SYSTEM_ID}
    if INSTITUTION_CHILDREN_FILE.exists():
        children = json.loads(INSTITUTION_CHILDREN_FILE.read_text(encoding="utf-8"))
        ids |= {i for c in children if (i := short_id(c.get("id")))}
    else:
        log.warning(
            "%s missing - run `python -m pipeline.fetch_institution --refresh`",
            INSTITUTION_CHILDREN_FILE,
        )
    return ids


def db_counts() -> tuple[int, dict[str, int]]:
    with get_sessionmaker()() as s:
        indexed = int(s.scalar(text("SELECT count(*) FROM works")) or 0)
        faculty = {
            "directory": int(s.scalar(text("SELECT count(*) FROM faculty")) or 0),
            "matched": int(
                s.scalar(text("SELECT count(DISTINCT faculty_id) FROM faculty_author_map")) or 0
            ),
            "with_works": int(
                s.scalar(text("SELECT count(DISTINCT faculty_id) FROM faculty_works")) or 0
            ),
        }
    return indexed, faculty


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    t0 = time.perf_counter()
    corpus = build_corpus(
        iter_raw_works(),
        institution_id=settings.openalex_institution_id,
        window_start=settings.window_start,
        window_end=settings.snapshot_date,
        top_n=10**9,  # no cut: statistics describe all eligible works
        full_authorships=load_full_authorships(),
    )
    works = corpus.works
    if not works:
        log.error("No works in data/raw - run `python -m pipeline.fetch_works` first.")
        return 1
    indexed, faculty = db_counts()
    stats = compute_stats(
        works,
        start=settings.window_start,
        end=settings.snapshot_date,
        uiuc_unit_ids=uiuc_unit_ids(settings.openalex_institution_id),
        indexed_works=indexed,
        faculty=faculty,
    )
    stats["overview"]["snapshot_date"] = settings.snapshot_date.isoformat()

    with get_sessionmaker()() as session, session.begin():
        n_keys = store_stats(session, stats)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out = PROCESSED_DIR / "stats.json"
    out.write_text(json.dumps(stats, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    o = stats["overview"]
    log.info(
        "Stats for %d eligible works (%d indexed) in %.1fs: citations=%d, OA=%.1f%%, "
        "median FWCI=%s, UIUC authors=%d -> %d keys (%s)",
        o["works"],
        indexed,
        time.perf_counter() - t0,
        o["citations_total"],
        100 * o["open_access_share"],
        o["fwci_median"],
        o["uiuc_authors"],
        n_keys,
        out,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
