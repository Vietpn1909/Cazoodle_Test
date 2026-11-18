"""Step 6: embed works and faculty with the local model (resumable, idempotent).

Run after ``load_works`` -> ``load_faculty`` -> ``match_faculty`` (faculty text uses the topics
of their matched works). A vector is recomputed only when the text it was computed from changed
(``embedding_hash``) or the model changed (``meta.embedding_model``); each batch is committed, so
an interrupted run resumes where it stopped. Works are processed by ``rank_score`` so the most
important ones are available first.

Usage:
    uv run python -m pipeline.embed                 # works + faculty
    uv run python -m pipeline.embed --only faculty
    uv run python -m pipeline.embed --limit 500     # embed at most N stale items per table
    uv run python -m pipeline.embed --force         # recompute everything
Report: data/processed/reports/embed.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import DeclarativeBase, Session

from app.core.config import get_settings
from app.db.models import Faculty, Meta, Work
from app.db.session import get_sessionmaker
from pipeline.db_bulk import copy_update, copy_upsert
from pipeline.embedding.model import Embedder, FastEmbedder, to_pgvector
from pipeline.embedding.texts import faculty_text, text_hash, work_text
from pipeline.paths import REPORTS_DIR

log = logging.getLogger("pipeline.embed")

BATCH_SIZE = 128
SessionFactory = Callable[[], Session]

WORKS_SQL = text(
    """
    SELECT w.id, w.title, w.abstract, w.keywords, w.embedding_hash,
           t.name AS topic, t.subfield, t.field
    FROM works w
    LEFT JOIN topics t ON t.id = w.primary_topic_id
    ORDER BY w.rank_score DESC NULLS LAST, w.id
    """
)
FACULTY_TOPICS_SQL = text(
    """
    SELECT fw.faculty_id, t.name, count(*) AS n
    FROM faculty_works fw
    JOIN works w ON w.id = fw.work_id
    JOIN topics t ON t.id = w.primary_topic_id
    GROUP BY fw.faculty_id, t.name
    ORDER BY fw.faculty_id, n DESC, t.name
    """
)


@dataclass(frozen=True)
class Item:
    id: Any
    text: str  # empty -> nothing to embed (vector is cleared)
    hash: str | None
    stored_hash: str | None

    @property
    def stale(self) -> bool:
        return self.hash != self.stored_hash


def work_items(session: Session) -> list[Item]:
    items = []
    for r in session.execute(WORKS_SQL):
        t = work_text(
            r.title,
            r.abstract,
            topic=r.topic,
            subfield=r.subfield,
            field=r.field,
            keywords=r.keywords,
        )
        items.append(Item(r.id, t, text_hash(t), r.embedding_hash))
    return items


def faculty_items(session: Session) -> list[Item]:
    topics: dict[int, list[str]] = defaultdict(list)
    for fid, name, _n in session.execute(FACULTY_TOPICS_SQL):
        topics[fid].append(name)
    items = []
    for f in session.execute(
        select(
            Faculty.id, Faculty.keywords_top, Faculty.keywords_clean, Faculty.embedding_hash
        ).order_by(Faculty.id)
    ):
        t = faculty_text(f.keywords_top, f.keywords_clean, topics.get(f.id))
        items.append(Item(f.id, t, text_hash(t) if t else None, f.embedding_hash))
    return items


def embed_items(
    session_factory: SessionFactory,
    embedder: Embedder,
    model: type[DeclarativeBase],
    items: Sequence[Item],
    *,
    with_profile_text: bool = False,
    batch_size: int = BATCH_SIZE,
) -> int:
    """Embed ``items`` and store them batch by batch. Returns the number of items written."""
    done = 0
    t0 = time.perf_counter()
    for start in range(0, len(items), batch_size):
        chunk = items[start : start + batch_size]
        to_embed = [it for it in chunk if it.text]
        vectors = embedder.embed([it.text for it in to_embed])
        by_id = {it.id: vectors[i] for i, it in enumerate(to_embed)}
        rows = []
        for it in chunk:
            row: dict[str, Any] = {
                "id": it.id,
                "embedding": to_pgvector(by_id[it.id]) if it.id in by_id else None,
                "embedding_hash": it.hash,
            }
            if with_profile_text:
                row["profile_text"] = it.text or None
            rows.append(row)
        with session_factory() as session, session.begin():
            copy_update(session, model, rows, key=["id"])
        done += len(chunk)
        elapsed = time.perf_counter() - t0
        rate = done / elapsed
        log.info(
            "%s: %d/%d embedded (%.1f/s, ETA %.1f min)",
            model.__tablename__,
            done,
            len(items),
            rate,
            (len(items) - done) / rate / 60 if rate else 0,
        )
    return done


def run(
    session_factory: SessionFactory,
    embedder: Embedder,
    *,
    only: str | None = None,
    limit: int | None = None,
    force: bool = False,
    batch_size: int = BATCH_SIZE,
) -> dict[str, Any]:
    with session_factory() as session:
        stored = session.get(Meta, "embedding_model")
        if stored is not None and stored.value != embedder.name:
            log.warning(
                "Model changed (%s -> %s): re-embedding everything", stored.value, embedder.name
            )
            force = True
        plans: dict[str, tuple[type[DeclarativeBase], list[Item], bool]] = {}
        if only in (None, "works"):
            plans["works"] = (Work, work_items(session), False)
        if only in (None, "faculty"):
            plans["faculty"] = (Faculty, faculty_items(session), True)

    report: dict[str, Any] = {"model": embedder.name, "dim": embedder.dim, "forced": force}
    for name, (model, items, with_profile) in plans.items():
        pending = [it for it in items if force or it.stale]
        if limit is not None:
            pending = pending[:limit]
        t0 = time.perf_counter()
        written = embed_items(
            session_factory,
            embedder,
            model,
            pending,
            with_profile_text=with_profile,
            batch_size=batch_size,
        )
        lengths = [len(it.text) for it in items if it.text]
        report[name] = {
            "total": len(items),
            "embedded_now": written,
            "up_to_date_skipped": len(items) - len(pending),
            "without_text": sum(1 for it in items if not it.text),
            "text_chars_mean": round(sum(lengths) / len(lengths), 1) if lengths else 0,
            "text_chars_max": max(lengths, default=0),
            "seconds": round(time.perf_counter() - t0, 1),
        }
    with session_factory() as session, session.begin():
        copy_upsert(
            session,
            Meta,
            [
                {"key": "embedding_model", "value": embedder.name},
                {"key": "embedding_dim", "value": str(embedder.dim)},
                {"key": "embedded_at", "value": datetime.now(UTC).isoformat()},
            ],
            key=["key"],
        )
        session.execute(text("ANALYZE works"))
        session.execute(text("ANALYZE faculty"))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Embed works and faculty")
    parser.add_argument("--only", choices=["works", "faculty"])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = get_settings()
    log.info("Loading model %s ...", settings.embedding_model)
    embedder = FastEmbedder(settings.embedding_model, settings.embedding_dim)
    report = run(get_sessionmaker(), embedder, only=args.only, limit=args.limit, force=args.force)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / "embed.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("Done: %s", json.dumps({k: v for k, v in report.items() if isinstance(v, dict)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
