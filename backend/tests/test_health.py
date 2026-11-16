from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db.session import get_db


class _BrokenSession:
    def execute(self, *_: Any, **__: Any) -> None:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))


def test_health_reports_degraded_when_db_down(client: TestClient) -> None:
    def broken_db() -> Iterator[_BrokenSession]:
        yield _BrokenSession()

    client.app.dependency_overrides[get_db] = broken_db  # type: ignore[attr-defined]
    resp = client.get("/api/health")
    assert resp.status_code == 503
    assert resp.json()["database"] == "unavailable"


@pytest.mark.db
def test_health_ok_with_real_db(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok", "version": "0.1.0"}


def test_openapi_is_served(client: TestClient) -> None:
    resp = client.get("/api/openapi.json")
    assert resp.status_code == 200
    assert "/api/health" in resp.json()["paths"]
