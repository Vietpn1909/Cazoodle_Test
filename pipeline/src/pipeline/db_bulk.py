"""Fast bulk writes via Postgres ``COPY`` (psycopg 3).

Row-by-row INSERTs through Docker Desktop's port forwarding take tens of minutes for
~500k rows; COPY into a temp table + one ``INSERT ... SELECT ... ON CONFLICT`` takes seconds.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from psycopg.types.json import Jsonb
from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Session


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def copy_upsert(
    session: Session,
    model: type[DeclarativeBase],
    rows: Sequence[dict[str, Any]],
    *,
    key: Sequence[str],
    on_conflict: str = "update",
) -> int:
    """Upsert ``rows`` into the model's table inside the session's current transaction.

    ``on_conflict``: ``"update"`` (overwrite given columns), ``"nothing"`` (skip existing).
    Only the columns present in the rows are written, so other columns (e.g. embeddings)
    keep their values on update.
    """
    if not rows:
        return 0
    table = cast(Table, model.__table__)
    cols = list(rows[0].keys())
    json_cols = {c for c in cols if isinstance(table.c[c].type, JSONB)}
    collist = ", ".join(_q(c) for c in cols)
    tmp = _q(f"_tmp_{table.name}")

    conn = session.connection()
    conn.exec_driver_sql(f"DROP TABLE IF EXISTS {tmp}")
    conn.exec_driver_sql(
        f"CREATE TEMP TABLE {tmp} AS SELECT {collist} FROM {_q(table.name)} WITH NO DATA"  # noqa: S608
    )
    raw = conn.connection.driver_connection
    assert raw is not None
    with raw.cursor() as cur, cur.copy(f"COPY {tmp} ({collist}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(
                [Jsonb(r[c]) if c in json_cols and r[c] is not None else r[c] for c in cols]
            )

    if on_conflict == "update":
        updates = ", ".join(f"{_q(c)} = EXCLUDED.{_q(c)}" for c in cols if c not in key)
        action = f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
    elif on_conflict == "nothing":
        action = "DO NOTHING"
    else:
        raise ValueError(f"unknown on_conflict={on_conflict!r}")
    keylist = ", ".join(_q(k) for k in key)
    result = conn.exec_driver_sql(
        # identifiers come from model metadata (quoted); values travel via COPY
        f"INSERT INTO {_q(table.name)} ({collist}) SELECT {collist} FROM {tmp} "  # noqa: S608
        f"ON CONFLICT ({keylist}) {action}"
    )
    conn.exec_driver_sql(f"DROP TABLE {tmp}")
    return int(result.rowcount or 0)


def copy_update(
    session: Session,
    model: type[DeclarativeBase],
    rows: Sequence[dict[str, Any]],
    *,
    key: Sequence[str],
) -> int:
    """Bulk ``UPDATE`` existing rows (columns given in ``rows``) matched on ``key``.

    Unlike an upsert, rows need not carry the table's NOT NULL columns.
    """
    if not rows:
        return 0
    table = cast(Table, model.__table__)
    cols = list(rows[0].keys())
    json_cols = {c for c in cols if isinstance(table.c[c].type, JSONB)}
    collist = ", ".join(_q(c) for c in cols)
    tmp = _q(f"_tmpu_{table.name}")
    conn = session.connection()
    conn.exec_driver_sql(f"DROP TABLE IF EXISTS {tmp}")
    conn.exec_driver_sql(
        f"CREATE TEMP TABLE {tmp} AS SELECT {collist} FROM {_q(table.name)} WITH NO DATA"  # noqa: S608
    )
    raw = conn.connection.driver_connection
    assert raw is not None
    with raw.cursor() as cur, cur.copy(f"COPY {tmp} ({collist}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(
                [Jsonb(r[c]) if c in json_cols and r[c] is not None else r[c] for c in cols]
            )
    sets = ", ".join(f"{_q(c)} = s.{_q(c)}" for c in cols if c not in key)
    cond = " AND ".join(f"t.{_q(k)} = s.{_q(k)}" for k in key)
    result = conn.exec_driver_sql(
        f"UPDATE {_q(table.name)} AS t SET {sets} FROM {tmp} AS s WHERE {cond}"  # noqa: S608
    )
    conn.exec_driver_sql(f"DROP TABLE {tmp}")
    return int(result.rowcount or 0)
