"""Persist / read precomputed statistics (``stats_kv``: one JSONB document per key)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import StatsKV
from pipeline.db_bulk import copy_upsert


def store_stats(session: Session, stats: Mapping[str, Any]) -> int:
    """Upsert every key (replacing its previous value); returns the number of keys written."""
    now = datetime.now(UTC)
    rows = [{"key": key, "value": value, "computed_at": now} for key, value in stats.items()]
    copy_upsert(session, StatsKV, rows, key=["key"])
    return len(rows)


def read_stats(session: Session, key: str) -> Any | None:
    row = session.get(StatsKV, key)
    return None if row is None else row.value
