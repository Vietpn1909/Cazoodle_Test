"""Step 2: complete the author lists of works whose authorships were truncated.

List endpoints return at most 100 authorships per work (``is_authors_truncated``). For large
collaborations the UIUC authors are often beyond the first 100, so the work could not be
linked to UIUC people. The single-work endpoint returns the full list and is free
(cost $0 on 2026-11-16). One cached file per work -> resumable, idempotent.

Usage: uv run python -m pipeline.fetch_full_authorships
"""

from __future__ import annotations

import gzip
import json
import logging
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from pipeline.fetch_works import iter_raw_works
from pipeline.openalex.client import BudgetExhaustedError, OpenAlexClient
from pipeline.openalex.sliced import atomic_write_bytes
from pipeline.openalex.transform import short_id
from pipeline.paths import FULL_AUTHORSHIPS_DIR

log = logging.getLogger("pipeline.fetch_full_authorships")


def truncated_work_ids(raw_works: Iterable[dict[str, Any]]) -> list[str]:
    ids = {short_id(w["id"]) for w in raw_works if w.get("is_authors_truncated")}
    return sorted(i for i in ids if i)


def cache_path(work_id: str, out_dir: Path = FULL_AUTHORSHIPS_DIR) -> Path:
    return out_dir / f"{work_id}.json.gz"


def load_full_authorships(out_dir: Path = FULL_AUTHORSHIPS_DIR) -> dict[str, list[dict[str, Any]]]:
    """work id -> complete authorship list (only for works that were truncated)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for f in out_dir.glob("*.json.gz"):
        data = json.loads(gzip.decompress(f.read_bytes()))
        out[f.name.removesuffix(".json.gz")] = data.get("authorships") or []
    return out


def fetch_full_authorships(client: OpenAlexClient, work_ids: Iterable[str], out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    fetched = 0
    for wid in work_ids:
        path = cache_path(wid, out_dir)
        if path.exists():
            continue
        data = client.get(f"/works/{wid}", {"select": "id,authorships"})
        atomic_write_bytes(path, gzip.compress(json.dumps(data, ensure_ascii=False).encode()))
        fetched += 1
        if fetched % 50 == 0:
            log.info("fetched %d (budget left $%s)", fetched, client.last_budget.remaining_usd)
    return fetched


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    api_key = settings.openalex_api_key.get_secret_value() if settings.openalex_api_key else None
    ids = truncated_work_ids(iter_raw_works())
    with OpenAlexClient(api_key) as client:
        try:
            n = fetch_full_authorships(client, ids, FULL_AUTHORSHIPS_DIR)
        except BudgetExhaustedError as exc:
            log.warning("%s Cached works are kept; re-run later.", exc)
            return 2
        log.info(
            "%d truncated works, %d fetched now, %d cached; calls=%d spent=$%.4f",
            len(ids),
            n,
            len(list(FULL_AUTHORSHIPS_DIR.glob("*.json.gz"))),
            client.calls,
            client.spent_usd,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
