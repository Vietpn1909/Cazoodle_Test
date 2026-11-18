"""Step 1: download all UIUC works in the snapshot window from OpenAlex (raw, cached, resumable).

Slices = calendar months (each ~1-2.3k works, far below the 10k ``page=`` cap); sort
``publication_date:asc`` (ties ordered by id -> stable pages). See ``openalex/sliced.py``.

Usage:
    uv run python -m pipeline.fetch_works            # fetch or resume
    uv run python -m pipeline.fetch_works --restart  # discard progress and start over
    uv run python -m pipeline.fetch_works --max-pages 2

Output: ``data/raw/openalex/works/<YYYY-MM-DD>_p001.jsonl.gz`` ... plus ``_state.json``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from pipeline.openalex.client import OpenAlexClient
from pipeline.openalex.sliced import Slice, fetch_sliced, iter_raw, verify
from pipeline.paths import WORKS_RAW_DIR

log = logging.getLogger("pipeline.fetch_works")

SORT = "publication_date:asc"
WORK_FIELDS = (
    "id",
    "doi",
    "title",
    "publication_date",
    "publication_year",
    "type",
    "language",
    "primary_location",
    "open_access",
    "authorships",
    "is_authors_truncated",
    "cited_by_count",
    "fwci",
    "citation_normalized_percentile",
    "primary_topic",
    "topics",
    "keywords",
    "sustainable_development_goals",
    "abstract_inverted_index",
    "is_retracted",
    "is_paratext",
    "institutions_distinct_count",
    "countries_distinct_count",
)


def month_slices(start: date, end: date) -> list[tuple[date, date]]:
    """Split [start, end] (inclusive) into calendar-month slices."""
    slices = []
    cur = start
    while cur <= end:
        next_month = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        slice_end = min(next_month - timedelta(days=1), end)
        slices.append((cur, slice_end))
        cur = next_month
    return slices


def work_slices(institution_id: str, start: date, end: date) -> list[Slice]:
    return [
        Slice(
            key=s.isoformat(),
            filter=(
                f"institutions.id:{institution_id},"
                f"from_publication_date:{s.isoformat()},to_publication_date:{e.isoformat()}"
            ),
        )
        for s, e in month_slices(start, end)
    ]


def iter_raw_works(out_dir: Path = WORKS_RAW_DIR) -> Iterator[dict[str, Any]]:
    """Yield every cached raw work record (used by transform steps)."""
    return iter_raw(out_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch UIUC works from OpenAlex")
    parser.add_argument("--restart", action="store_true", help="discard progress, start over")
    parser.add_argument("--max-pages", type=int, default=None, help="stop after N pages")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    api_key = settings.openalex_api_key.get_secret_value() if settings.openalex_api_key else None
    log.info("OpenAlex key: %s", "set" if api_key else "not set (shared $0.10/day budget)")

    slices = work_slices(
        settings.openalex_institution_id, settings.window_start, settings.snapshot_date
    )
    with OpenAlexClient(api_key) as client:
        state = fetch_sliced(
            client,
            WORKS_RAW_DIR,
            endpoint="/works",
            slices=slices,
            select=",".join(WORK_FIELDS),
            sort=SORT,
            max_pages=args.max_pages,
            restart=args.restart,
        )
        log.info(
            "done=%s | %d/%d records | calls=%d spent=$%.4f (total $%.4f) left=$%s",
            state.done,
            state.records,
            state.expected,
            client.calls,
            client.spent_usd,
            state.spent_usd,
            client.last_budget.remaining_usd,
        )
    if state.done:
        log.info("verify: %s", verify(WORKS_RAW_DIR, state))
    return 0 if state.done else 2


if __name__ == "__main__":
    sys.exit(main())
