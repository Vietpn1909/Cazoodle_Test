"""Step 3: download the UIUC author directory from OpenAlex (raw, cached, resumable).

Authors whose ``last_known_institutions`` is UIUC and who have >= 5 works (~18k), split
into ``works_count`` ranges so every slice stays below the 10k ``page=`` cap (key-free).
Gives name variants (``display_name_alternatives``), ORCID, topics and h-index — the
evidence used to match the faculty directory to OpenAlex authors.

Usage: uv run python -m pipeline.fetch_authors [--restart] [--max-pages N]
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from pipeline.openalex.client import OpenAlexClient
from pipeline.openalex.sliced import Slice, fetch_sliced, iter_raw, verify
from pipeline.paths import AUTHORS_RAW_DIR

log = logging.getLogger("pipeline.fetch_authors")

SORT = "display_name:asc"
AUTHOR_FIELDS = (
    "id",
    "display_name",
    "display_name_alternatives",
    "orcid",
    "works_count",
    "cited_by_count",
    "summary_stats",
    "topics",
    "last_known_institutions",
)
# (key, works_count filter) — each well below 10k results (counts on 2026-11-16: 6.3k/4.5k/7.4k)
WORKS_COUNT_RANGES = (("wc005-009", "5-9"), ("wc010-019", "10-19"), ("wc020-plus", ">19"))


def author_slices(institution_id: str) -> list[Slice]:
    return [
        Slice(key=key, filter=f"last_known_institutions.id:{institution_id},works_count:{rng}")
        for key, rng in WORKS_COUNT_RANGES
    ]


def iter_raw_authors(out_dir: Path = AUTHORS_RAW_DIR) -> Iterator[dict[str, Any]]:
    return iter_raw(out_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch the UIUC author directory from OpenAlex")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--max-pages", type=int, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = get_settings()
    api_key = settings.openalex_api_key.get_secret_value() if settings.openalex_api_key else None
    with OpenAlexClient(api_key) as client:
        state = fetch_sliced(
            client,
            AUTHORS_RAW_DIR,
            endpoint="/authors",
            slices=author_slices(settings.openalex_institution_id),
            select=",".join(AUTHOR_FIELDS),
            sort=SORT,
            max_pages=args.max_pages,
            restart=args.restart,
        )
        log.info(
            "done=%s | %d/%d authors | calls=%d spent=$%.4f left=$%s",
            state.done,
            state.records,
            state.expected,
            client.calls,
            client.spent_usd,
            client.last_budget.remaining_usd,
        )
    if state.done:
        log.info("verify: %s", verify(AUTHORS_RAW_DIR, state))
    return 0 if state.done else 2


if __name__ == "__main__":
    sys.exit(main())
