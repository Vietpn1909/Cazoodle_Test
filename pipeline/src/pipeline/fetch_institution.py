"""Step 1b: download the UIUC institution record from OpenAlex (1 call, cached).

Usage: uv run python -m pipeline.fetch_institution [--refresh]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from app.core.config import get_settings
from pipeline.openalex.client import OpenAlexClient
from pipeline.paths import INSTITUTION_CHILDREN_FILE, INSTITUTION_RAW_FILE

log = logging.getLogger("pipeline.fetch_institution")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if INSTITUTION_RAW_FILE.exists() and INSTITUTION_CHILDREN_FILE.exists() and not args.refresh:
        log.info("Cached: %s", INSTITUTION_RAW_FILE)
        return 0

    settings = get_settings()
    api_key = settings.openalex_api_key.get_secret_value() if settings.openalex_api_key else None
    with OpenAlexClient(api_key) as client:
        data = client.get(f"/institutions/{settings.openalex_institution_id}")
        # UIUC units (NCSA, State Geological Survey, ...): needed to exclude them from the
        # "collaborating institutions" statistics.
        children = client.get(
            "/institutions",
            {
                "filter": f"lineage:{settings.openalex_institution_id}",
                "select": "id,display_name,type",
                "per_page": 100,
            },
        )
    INSTITUTION_RAW_FILE.parent.mkdir(parents=True, exist_ok=True)
    INSTITUTION_RAW_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    INSTITUTION_CHILDREN_FILE.write_text(
        json.dumps(children["results"], ensure_ascii=False, indent=2), "utf-8"
    )
    log.info(
        "Saved %s (%s) and %d lineage units",
        INSTITUTION_RAW_FILE,
        data.get("display_name"),
        len(children["results"]),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
