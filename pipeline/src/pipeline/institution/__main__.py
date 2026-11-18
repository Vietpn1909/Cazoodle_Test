"""Step 8: gather institutional metadata from public sources into the ``institution`` table.

Sources (open licences, no API key): OpenAlex record (cached by ``fetch_institution``), ROR,
Wikipedia, Wikidata, Wikimedia Commons. Every web response is cached under
``data/raw/institution/`` so each URL is requested once; the step is idempotent.

Usage:
    uv run python -m pipeline.institution            # use cache, fetch what is missing
    uv run python -m pipeline.institution --refresh  # re-download everything
Output: ``institution`` row + data/processed/institution.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Institution
from app.db.session import get_sessionmaker
from pipeline.institution.build import build_institution
from pipeline.institution.sources import (
    CAMPUS_QUERIES,
    commons_image_info,
    fetch_ror,
    fetch_wikidata_entity,
    fetch_wikipedia_summary,
    pick_images,
    search_commons_files,
    select_gallery,
)
from pipeline.institution.web import PoliteJsonClient
from pipeline.paths import INSTITUTION_RAW_DIR, INSTITUTION_RAW_FILE, PROCESSED_DIR

log = logging.getLogger("pipeline.institution")

WIKIPEDIA_TITLE = "University of Illinois Urbana-Champaign"


def store_institution(session: Session, record: dict[str, Any]) -> None:
    """Insert or replace the institution row."""
    session.merge(Institution(**record))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gather institutional metadata")
    parser.add_argument("--refresh", action="store_true", help="re-download cached responses")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not INSTITUTION_RAW_FILE.exists():
        log.error("Run `python -m pipeline.fetch_institution` first (OpenAlex record).")
        return 1
    openalex = json.loads(INSTITUTION_RAW_FILE.read_text(encoding="utf-8"))
    client = PoliteJsonClient()
    cache = INSTITUTION_RAW_DIR
    refresh = args.refresh

    wikipedia = fetch_wikipedia_summary(client, cache, WIKIPEDIA_TITLE, refresh=refresh)
    qid = wikipedia["wikibase_item"]
    wikidata = fetch_wikidata_entity(client, cache, qid, refresh=refresh)
    ror = fetch_ror(client, cache, openalex["ror"], refresh=refresh)

    per_query = []
    for query in CAMPUS_QUERIES:
        titles = search_commons_files(client, cache, query, refresh=refresh)
        per_query.append(commons_image_info(client, cache, titles, refresh=refresh))
    gallery = pick_images(per_query, max_images=5)
    images = select_gallery(client, cache, wikidata, gallery, refresh=refresh)
    log.info("Selected %d campus photos: %s", len(images), [i["title"] for i in images])

    record = build_institution(
        openalex=openalex,
        ror=ror,
        wikipedia=wikipedia,
        wikidata=wikidata,
        images=images,
        retrieved_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
    )
    with get_sessionmaker()() as session, session.begin():
        store_institution(session, record)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out = PROCESSED_DIR / "institution.json"
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(
        "Institution %s stored (%d web requests, %d images). Copy: %s",
        record["name"],
        client.requests,
        len(record["images"]),
        out,
    )
    get_settings()  # fail early on a missing .env rather than later
    return 0


if __name__ == "__main__":
    sys.exit(main())
