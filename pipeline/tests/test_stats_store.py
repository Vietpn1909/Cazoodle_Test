import time

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from pipeline.stats.store import read_stats, store_stats


@pytest.mark.db
def test_store_stats_roundtrip_and_replace(db_session: Session) -> None:
    first = {
        "overview": {"works": 10, "oa_status": {"gold": 1}, "note": "café über"},
        "output": {"by_month": [{"month": "2025-01", "works": 3, "partial": False}]},
    }
    assert store_stats(db_session, first) == 2
    db_session.commit()
    assert read_stats(db_session, "overview") == first["overview"]  # JSONB roundtrip
    stamp1 = db_session.execute(
        text("select computed_at from stats_kv where key = 'overview'")
    ).scalar_one()

    time.sleep(0.01)
    store_stats(db_session, {"overview": {"works": 11}})  # replaces the document, keeps others
    db_session.commit()
    db_session.expire_all()
    assert read_stats(db_session, "overview") == {"works": 11}
    assert read_stats(db_session, "output") == first["output"]
    stamp2 = db_session.execute(
        text("select computed_at from stats_kv where key = 'overview'")
    ).scalar_one()
    assert stamp2 > stamp1  # computed_at is refreshed on update
    assert read_stats(db_session, "missing") is None
