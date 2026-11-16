"""Step 1: download all UIUC works in the snapshot window from OpenAlex (raw, cached, resumable).

Why monthly slices + ``page=`` paging instead of ``cursor=``: OpenAlex requires an API key
to page past the first page with ``cursor``, while basic ``page=`` paging works without a
key but is capped at 10,000 results per query. Each month has ~1-2k UIUC works, so slicing
by month keeps every query far below the cap and the whole fetch key-free (ADR-010).
A stable sort (``publication_date:asc``, ties broken by id) keeps pages consistent.

Usage:
    uv run python -m pipeline.fetch_works            # fetch or resume
    uv run python -m pipeline.fetch_works --restart  # discard progress and start over
    uv run python -m pipeline.fetch_works --max-pages 2

Output: ``data/raw/openalex/works/<YYYY-MM-DD>_p001.jsonl.gz`` ... plus ``_state.json``.
Transform steps read only these files, so re-running them costs no API budget.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import math
import os
import shutil
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from pipeline.openalex.client import BudgetExhaustedError, OpenAlexClient
from pipeline.paths import WORKS_RAW_DIR

log = logging.getLogger("pipeline.fetch_works")

PER_PAGE = 200
MAX_RESULTS_PER_QUERY = 10_000  # OpenAlex cap for page-based paging
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


def slice_filter(institution_id: str, start: date, end: date) -> str:
    return (
        f"institutions.id:{institution_id},"
        f"from_publication_date:{start.isoformat()},to_publication_date:{end.isoformat()}"
    )


@dataclass
class SliceState:
    count: int | None = None
    pages_done: int = 0
    records: int = 0
    done: bool = False


@dataclass
class FetchState:
    institution_id: str
    window: str
    select: str
    slices: dict[str, SliceState] = field(default_factory=dict)
    spent_usd: float = 0.0
    updated_at: str = ""

    @property
    def done(self) -> bool:
        return bool(self.slices) and all(s.done for s in self.slices.values())

    @property
    def records(self) -> int:
        return sum(s.records for s in self.slices.values())

    @property
    def expected(self) -> int:
        return sum(s.count or 0 for s in self.slices.values())

    @classmethod
    def load(cls, path: Path) -> FetchState | None:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["slices"] = {k: SliceState(**v) for k, v in raw.get("slices", {}).items()}
        return cls(**raw)

    def save(self, path: Path) -> None:
        self.updated_at = datetime.now(UTC).isoformat()
        _atomic_write_bytes(path, json.dumps(asdict(self), indent=2).encode())


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def page_path(out_dir: Path, slice_start: date, page: int) -> Path:
    return out_dir / f"{slice_start.isoformat()}_p{page:03d}.jsonl.gz"


def write_page(path: Path, results: list[dict[str, Any]]) -> None:
    lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results)
    _atomic_write_bytes(path, gzip.compress(lines.encode("utf-8"), compresslevel=6))


def iter_raw_works(out_dir: Path = WORKS_RAW_DIR) -> Iterator[dict[str, Any]]:
    """Yield every raw work record from the cache (used by transform steps)."""
    for f in sorted(out_dir.glob("*.jsonl.gz")):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)


def fetch_works(
    client: OpenAlexClient,
    out_dir: Path,
    *,
    institution_id: str,
    window_start: date,
    window_end: date,
    select: str = ",".join(WORK_FIELDS),
    max_pages: int | None = None,
    restart: bool = False,
) -> FetchState:
    """Fetch (or resume fetching) all works for the institution/window into ``out_dir``."""
    state_path = out_dir / "_state.json"
    if restart and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    window = f"{window_start.isoformat()}..{window_end.isoformat()}"
    state = FetchState.load(state_path)
    if state and (
        state.institution_id != institution_id or state.window != window or state.select != select
    ):
        raise SystemExit(
            "Existing raw data was fetched with different parameters. "
            "Re-run with --restart to discard it."
        )
    if state is None:
        state = FetchState(institution_id=institution_id, window=window, select=select)

    pages_this_run = 0
    try:
        for start, end in month_slices(window_start, window_end):
            key = start.isoformat()
            sl = state.slices.setdefault(key, SliceState())
            while not sl.done:
                if max_pages is not None and pages_this_run >= max_pages:
                    return state
                page_no = sl.pages_done + 1
                resp = client.get(
                    "/works",
                    {
                        "filter": slice_filter(institution_id, start, end),
                        "select": select,
                        "sort": SORT,
                        "per_page": PER_PAGE,
                        "page": page_no,
                    },
                )
                results = resp.get("results") or []
                sl.count = resp.get("meta", {}).get("count", sl.count)
                if sl.count is not None and sl.count > MAX_RESULTS_PER_QUERY:
                    raise SystemExit(
                        f"Slice {key} has {sl.count} works (> {MAX_RESULTS_PER_QUERY}); "
                        "use smaller slices."
                    )
                if results:
                    write_page(page_path(out_dir, start, page_no), results)
                sl.pages_done = page_no
                sl.records += len(results)
                total_pages = math.ceil((sl.count or 0) / PER_PAGE)
                sl.done = not results or page_no >= total_pages
                state.spent_usd = round(state.spent_usd + (client.last_budget.cost_usd or 0), 6)
                state.save(state_path)  # checkpoint after every page -> resumable
                pages_this_run += 1
                log.info(
                    "%s p%d/%d: +%d (total %d), budget left $%s",
                    key,
                    page_no,
                    total_pages,
                    len(results),
                    state.records,
                    client.last_budget.remaining_usd,
                )
    except BudgetExhaustedError as exc:
        log.warning("%s Progress saved (%d records).", exc, state.records)
    return state


def verify(out_dir: Path, state: FetchState) -> dict[str, Any]:
    """Check unique ids per slice against OpenAlex ``meta.count``."""
    ids_by_slice: dict[str, set[str]] = {}
    for f in sorted(out_dir.glob("*.jsonl.gz")):
        key = f.name.split("_p", 1)[0]
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            ids_by_slice.setdefault(key, set()).update(json.loads(line)["id"] for line in fh)
    mismatches = {
        k: {"expected": s.count, "unique": len(ids_by_slice.get(k, set()))}
        for k, s in state.slices.items()
        if s.count is not None and s.count != len(ids_by_slice.get(k, set()))
    }
    unique_total = len(set().union(*ids_by_slice.values())) if ids_by_slice else 0
    return {"expected": state.expected, "unique": unique_total, "mismatches": mismatches}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch UIUC works from OpenAlex")
    parser.add_argument("--restart", action="store_true", help="discard progress, start over")
    parser.add_argument("--max-pages", type=int, default=None, help="stop after N pages")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings: Settings = get_settings()
    api_key = settings.openalex_api_key.get_secret_value() if settings.openalex_api_key else None
    log.info("OpenAlex key: %s", "set" if api_key else "not set (shared $0.10/day budget)")

    with OpenAlexClient(api_key) as client:
        state = fetch_works(
            client,
            WORKS_RAW_DIR,
            institution_id=settings.openalex_institution_id,
            window_start=settings.window_start,
            window_end=settings.snapshot_date,
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
        report = verify(WORKS_RAW_DIR, state)
        log.info("verify: %s", report)
    return 0 if state.done else 2


if __name__ == "__main__":
    sys.exit(main())
