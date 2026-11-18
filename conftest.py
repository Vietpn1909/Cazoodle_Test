"""Shared pytest fixtures.

DB tests (``@pytest.mark.db``) run against a separate database ``<DB>_test`` created on the
same server and migrated with Alembic once per session, so the dev data is never touched.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import Base

ROOT = Path(__file__).parent


@pytest.fixture(scope="session")
def db_available() -> bool:
    url = make_url(get_settings().database_url).set(database="postgres")
    engine = create_engine(url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect():
            return True
    except OperationalError:
        return False
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _skip_db_tests_without_db(request: pytest.FixtureRequest) -> None:
    """Tests marked ``db`` are skipped (fast) when Postgres is not reachable."""
    if request.node.get_closest_marker("db") and not request.getfixturevalue("db_available"):
        pytest.skip("Postgres not reachable (docker compose up -d db)")


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    url = make_url(get_settings().database_url)
    test_url = url.set(database=f"{url.database}_test")
    # Short connect timeout: if Postgres is down, DB tests are skipped in ~3 s instead of
    # hanging on the driver's default timeout.
    admin = create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 3},
    )
    try:
        with admin.connect() as conn:
            exists = conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": test_url.database}
            )
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{test_url.database}"'))
    except OperationalError:
        pytest.skip("Postgres not reachable (docker compose up -d db)")
    finally:
        admin.dispose()

    cfg = Config(str(ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", test_url.render_as_string(hide_password=False))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")

    engine = create_engine(test_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    """Session on the test DB; all tables are truncated after each test."""
    with Session(db_engine) as session:
        yield session
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with db_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
