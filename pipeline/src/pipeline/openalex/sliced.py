"""Key-free, resumable bulk download of an OpenAlex list endpoint (ADR-010).

OpenAlex needs an API key for ``cursor=`` paging, while ``page=`` paging is key-free but
capped at 10,000 results per query. We therefore split a query into *slices* (each with its
own filter, every slice < 10k results), page each slice with a stable sort, checkpoint after
every page, and finally verify unique ids per slice against ``meta.count``.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import os
import shutil
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.openalex.client import BudgetExhaustedError, OpenAlexClient

log = logging.getLogger(__name__)

PER_PAGE = 200
MAX_RESULTS_PER_QUERY = 10_000  # OpenAlex cap for page-based paging


@dataclass(frozen=True)
class Slice:
    key: str  # filesystem-safe, sortable
    filter: str


@dataclass
class SliceState:
    count: int | None = None
    pages_done: int = 0
    records: int = 0
    done: bool = False


@dataclass
class FetchState:
    signature: str  # endpoint + select + sort + slice filters; must match to resume
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
        atomic_write_bytes(path, json.dumps(asdict(self), indent=2).encode())


def atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def page_path(out_dir: Path, slice_key: str, page: int) -> Path:
    return out_dir / f"{slice_key}_p{page:03d}.jsonl.gz"


def write_page(path: Path, results: list[dict[str, Any]]) -> None:
    lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results)
    atomic_write_bytes(path, gzip.compress(lines.encode("utf-8"), compresslevel=6))


def iter_raw(out_dir: Path) -> Iterator[dict[str, Any]]:
    """Yield every cached raw record (page files in slice/page order)."""
    for f in sorted(out_dir.glob("*.jsonl.gz")):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)


def make_signature(endpoint: str, select: str, sort: str, slices: Sequence[Slice]) -> str:
    return json.dumps(
        {"endpoint": endpoint, "select": select, "sort": sort, "slices": [s.filter for s in slices]}
    )


def fetch_sliced(
    client: OpenAlexClient,
    out_dir: Path,
    *,
    endpoint: str,
    slices: Sequence[Slice],
    select: str,
    sort: str,
    max_pages: int | None = None,
    restart: bool = False,
) -> FetchState:
    """Fetch (or resume) every slice into ``out_dir``. Stops cleanly when the budget runs out."""
    state_path = out_dir / "_state.json"
    if restart and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    signature = make_signature(endpoint, select, sort, slices)
    state = FetchState.load(state_path)
    if state and state.signature != signature:
        raise SystemExit(
            f"Cached data in {out_dir} was fetched with different parameters. "
            "Re-run with --restart to discard it."
        )
    if state is None:
        state = FetchState(signature=signature)

    pages_this_run = 0
    try:
        for sl_def in slices:
            sl = state.slices.setdefault(sl_def.key, SliceState())
            while not sl.done:
                if max_pages is not None and pages_this_run >= max_pages:
                    return state
                page_no = sl.pages_done + 1
                resp = client.get(
                    endpoint,
                    {
                        "filter": sl_def.filter,
                        "select": select,
                        "sort": sort,
                        "per_page": PER_PAGE,
                        "page": page_no,
                    },
                )
                results = resp.get("results") or []
                sl.count = resp.get("meta", {}).get("count", sl.count)
                if sl.count is not None and sl.count > MAX_RESULTS_PER_QUERY:
                    raise SystemExit(
                        f"Slice {sl_def.key} has {sl.count} results (> {MAX_RESULTS_PER_QUERY});"
                        " use smaller slices."
                    )
                if results:
                    write_page(page_path(out_dir, sl_def.key, page_no), results)
                sl.pages_done = page_no
                sl.records += len(results)
                total_pages = math.ceil((sl.count or 0) / PER_PAGE)
                sl.done = not results or page_no >= total_pages
                state.spent_usd = round(state.spent_usd + (client.last_budget.cost_usd or 0), 6)
                state.save(state_path)  # checkpoint after every page -> resumable
                pages_this_run += 1
                log.info(
                    "%s %s p%d/%d: +%d (total %d), budget left $%s",
                    endpoint,
                    sl_def.key,
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
    """Compare unique ids per slice with OpenAlex ``meta.count``."""
    ids_by_slice: dict[str, set[str]] = {}
    for f in sorted(out_dir.glob("*.jsonl.gz")):
        key = f.name.rsplit("_p", 1)[0]
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            ids_by_slice.setdefault(key, set()).update(json.loads(line)["id"] for line in fh)
    mismatches = {
        k: {"expected": s.count, "unique": len(ids_by_slice.get(k, set()))}
        for k, s in state.slices.items()
        if s.count is not None and s.count != len(ids_by_slice.get(k, set()))
    }
    unique_total = len(set().union(*ids_by_slice.values())) if ids_by_slice else 0
    return {"expected": state.expected, "unique": unique_total, "mismatches": mismatches}
