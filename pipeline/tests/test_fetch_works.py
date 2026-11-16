import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from pipeline.fetch_works import (
    FetchState,
    fetch_works,
    iter_raw_works,
    month_slices,
    verify,
)
from pipeline.openalex.client import OpenAlexClient

# Two months: Jan has 3 works (2 pages at per_page=2 is simulated via PER_PAGE patch), Feb 1.
DATA = {
    "2025-01-15": ["W1", "W2", "W3"],
    "2025-02-01": ["W4"],
}


def _client(requested: list[tuple[str, int]], per_page: int) -> OpenAlexClient:
    def handler(req: httpx.Request) -> httpx.Response:
        flt = req.url.params["filter"]
        start = flt.split("from_publication_date:")[1].split(",")[0]
        page = int(req.url.params["page"])
        requested.append((start, page))
        ids = DATA[start]
        chunk = ids[(page - 1) * per_page : page * per_page]
        body = {"meta": {"count": len(ids)}, "results": [{"id": i} for i in chunk]}
        return httpx.Response(200, json=body, headers={"x-ratelimit-cost-usd": "0.0001"})

    return OpenAlexClient(transport=httpx.MockTransport(handler), sleep=lambda _: None)


@pytest.fixture(autouse=True)
def small_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pipeline.fetch_works.PER_PAGE", 2)


def _run(tmp_path: Path, requested: list[tuple[str, int]], **kw: object) -> FetchState:
    with _client(requested, per_page=2) as c:
        return fetch_works(
            c,
            tmp_path,
            institution_id="I1",
            window_start=date(2025, 1, 15),
            window_end=date(2025, 2, 10),
            select="id",
            **kw,  # type: ignore[arg-type]
        )


def test_month_slices_cover_window_exactly() -> None:
    assert month_slices(date(2024, 11, 16), date(2025, 1, 10)) == [
        (date(2024, 11, 16), date(2024, 11, 30)),
        (date(2024, 12, 1), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 1, 10)),
    ]


def test_fetch_pages_each_slice_and_resumes(tmp_path: Path) -> None:
    requested: list[tuple[str, int]] = []
    s1 = _run(tmp_path, requested, max_pages=1)
    assert not s1.done and s1.records == 2

    s2 = _run(tmp_path, requested)
    assert s2.done and s2.records == 4 and s2.expected == 4
    assert requested == [("2025-01-15", 1), ("2025-01-15", 2), ("2025-02-01", 1)]
    assert sorted(w["id"] for w in iter_raw_works(tmp_path)) == ["W1", "W2", "W3", "W4"]
    assert verify(tmp_path, s2) == {"expected": 4, "unique": 4, "mismatches": {}}

    _run(tmp_path, requested)  # completed fetch is a no-op
    assert len(requested) == 3


def test_refuses_to_mix_different_parameters(tmp_path: Path) -> None:
    FetchState(institution_id="I1", window="other", select="id").save(tmp_path / "_state.json")
    with pytest.raises(SystemExit):
        _run(tmp_path, [])


def test_state_roundtrip(tmp_path: Path) -> None:
    requested: list[tuple[str, int]] = []
    _run(tmp_path, requested)
    loaded = FetchState.load(tmp_path / "_state.json")
    assert loaded is not None and loaded.done
    assert json.loads((tmp_path / "_state.json").read_text())["slices"]["2025-01-15"]["count"] == 3
