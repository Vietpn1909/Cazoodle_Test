"""Engine/session factories.

One driver (psycopg 3, sync) is used everywhere. FastAPI runs sync endpoints in its
threadpool, which is plenty for this workload and avoids async-driver pitfalls on
Windows (ADR-008).
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    # connect_timeout: fail fast (health check returns 503) instead of hanging if Postgres is down
    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        pool_size=10,
        connect_args={"connect_timeout": 5},
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    with get_sessionmaker()() as session:
        yield session
