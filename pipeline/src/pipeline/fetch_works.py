"""Step 1: download all UIUC works in the snapshot window from OpenAlex (raw, cached, resumable).

Usage:
    uv run python -m pipeline.fetch_works            # fetch or resume
    uv run python -m pipeline.fetch_works --restart  # discard progress and start over
    uv run python -m pipeline.fetch_works --max-pages 2

Output: ``data/raw/openalex/works/page-00001.jsonl.gz`` ... plus ``_state.json``.
Transform steps read only these files, so re-running them costs no API budget.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from pipeline.openalex.client import BudgetExhaustedError, OpenAlexClient
from pipeline.paths import WORKS_RAW_DIR

log = logging.getLogger("pipeline.fetch_works")

PER_PAGE = 200
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


def works_filter(settings: Settings) -> str:
    return ",".join(
        [
            f"institutions.id:{settings.openalex_institution_id}",
            f"from_publication_date:{settings.window_start.isoformat()}",
            f"to_publication_date:{settings.snapshot_date.isoformat()}",
        ]
    )


@dataclass
class FetchState:
    filter: str
    select: str
    next_cursor: str | None = "*"
    pages: int = 0
    records: int = 0
    total_count: int | None = None
    done: bool = False
    spent_usd: float = 0.0
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @classmethod
    def load(cls, path: Path) -> FetchState | None:
        if not path.exists():
            return None
        return cls(**json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        self.updated_at = datetime.now(UTC).isoformat()
        _atomic_write_bytes(path, json.dumps(asdict(self), indent=2).encode())


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_page(out_dir: Path, page_no: int, results: list[dict[str, Any]]) -> Path:
    path = out_dir / f"page-{page_no:05d}.jsonl.gz"
    lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results)
    _atomic_write_bytes(path, gzip.compress(lines.encode("utf-8"), compresslevel=6))
    return path


def fetch_works(
    client: OpenAlexClient,
    out_dir: Path,
    *,
    filter_str: str,
    select: str = ",".join(WORK_FIELDS),
    max_pages: int | None = None,
    restart: bool = False,
) -> FetchState:
    """Fetch (or resume fetching) all works matching ``filter_str`` into ``out_dir``."""
    state_path = out_dir / "_state.json"
    if restart and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    state = FetchState.load(state_path)
    if state and (state.filter != filter_str or state.select != select):
        raise SystemExit(
            "Existing raw data was fetched with a different filter/select. "
            "Re-run with --restart to discard it."
        )
    if state is None:
        state = FetchState(filter=filter_str, select=select)
    if state.done:
        log.info("Already complete: %d records in %d pages.", state.records, state.pages)
        return state

    params = {"filter": filter_str, "select": select, "per_page": PER_PAGE}
    fetched_now = 0
    try:
        for page, next_cursor in client.iter_cursor(
            "/works", params, start_cursor=state.next_cursor or "*"
        ):
            results = page.get("results", [])
            if results:
                write_page(out_dir, state.pages + 1, results)
                state.pages += 1
                state.records += len(results)
            state.total_count = page.get("meta", {}).get("count", state.total_count)
            state.next_cursor = next_cursor
            state.done = next_cursor is None
            state.spent_usd = round(state.spent_usd + (client.last_budget.cost_usd or 0), 6)
            state.save(state_path)  # checkpoint after every page -> resumable
            fetched_now += 1
            log.info(
                "page %d: %d records (total %d/%s), budget left $%s",
                state.pages,
                len(results),
                state.records,
                state.total_count,
                client.last_budget.remaining_usd,
            )
            if max_pages is not None and fetched_now >= max_pages:
                break
    except BudgetExhaustedError as exc:
        log.warning("%s Progress saved at page %d.", exc, state.pages)
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--restart", action="store_true", help="discard progress, start over")
    parser.add_argument("--max-pages", type=int, default=None, help="stop after N pages")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    api_key = settings.openalex_api_key.get_secret_value() if settings.openalex_api_key else None
    log.info("OpenAlex key: %s", "set" if api_key else "not set (shared $0.10/day budget)")

    with OpenAlexClient(api_key) as client:
        state = fetch_works(
            client,
            WORKS_RAW_DIR,
            filter_str=works_filter(settings),
            max_pages=args.max_pages,
            restart=args.restart,
        )
        log.info(
            "Done=%s | %d records, %d pages | calls this run=%d, spent this run=$%.4f, left=$%s",
            state.done,
            state.records,
            state.pages,
            client.calls,
            client.spent_usd,
            client.last_budget.remaining_usd,
        )
    return 0 if state.done else 2


if __name__ == "__main__":
    sys.exit(main())
