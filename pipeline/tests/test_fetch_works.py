import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from pipeline.fetch_works import month_slices, work_slices
from pipeline.openalex.client import OpenAlexClient
from pipeline.openalex.sliced import FetchState, Slice, fetch_sliced, iter_raw, verify

DATA = {"jan": ["W1", "W2", "W3"], "feb": ["W4"]}
SLICES = [Slice(key="2025-01-15", filter="jan"), Slice(key="2025-02-01", filter="feb")]


def _client(requested: list[tuple[str, int]]) -> OpenAlexClient:
    def handler(req: httpx.Request) -> httpx.Response:
        flt, page = req.url.params["filter"], int(req.url.params["page"])
        requested.append((flt, page))
        ids = DATA[flt]
        chunk = ids[(page - 1) * 2 : page * 2]
        body = {"meta": {"count": len(ids)}, "results": [{"id": i} for i in chunk]}
        return httpx.Response(200, json=body, headers={"x-ratelimit-cost-usd": "0.0001"})

    return OpenAlexClient(transport=httpx.MockTransport(handler), sleep=lambda _: None)


@pytest.fixture(autouse=True)
def small_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pipeline.openalex.sliced.PER_PAGE", 2)


def _run(tmp_path: Path, requested: list[tuple[str, int]], **kw: object) -> FetchState:
    with _client(requested) as c:
        return fetch_sliced(
            c,
            tmp_path,
            endpoint="/works",
            slices=SLICES,
            select="id",
            sort="x",
            **kw,  # type: ignore[arg-type]
        )


def test_month_slices_cover_window_exactly() -> None:
    assert month_slices(date(2024, 11, 16), date(2025, 1, 10)) == [
        (date(2024, 11, 16), date(2024, 11, 30)),
        (date(2024, 12, 1), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 1, 10)),
    ]


def test_work_slices_filters() -> None:
    s = work_slices("I1", date(2025, 1, 15), date(2025, 2, 3))
    assert [x.key for x in s] == ["2025-01-15", "2025-02-01"]
    assert s[1].filter == (
        "institutions.id:I1,from_publication_date:2025-02-01,to_publication_date:2025-02-03"
    )


def test_fetch_pages_each_slice_and_resumes(tmp_path: Path) -> None:
    requested: list[tuple[str, int]] = []
    s1 = _run(tmp_path, requested, max_pages=1)
    assert not s1.done and s1.records == 2

    s2 = _run(tmp_path, requested)
    assert s2.done and s2.records == 4 and s2.expected == 4
    assert requested == [("jan", 1), ("jan", 2), ("feb", 1)]  # nothing fetched twice
    assert sorted(w["id"] for w in iter_raw(tmp_path)) == ["W1", "W2", "W3", "W4"]
    assert verify(tmp_path, s2) == {"expected": 4, "unique": 4, "mismatches": {}}

    _run(tmp_path, requested)  # completed fetch is a no-op
    assert len(requested) == 3


def test_refuses_to_mix_different_parameters(tmp_path: Path) -> None:
    FetchState(signature="other").save(tmp_path / "_state.json")
    with pytest.raises(SystemExit):
        _run(tmp_path, [])


def test_verify_flags_missing_records(tmp_path: Path) -> None:
    requested: list[tuple[str, int]] = []
    state = _run(tmp_path, requested)
    (tmp_path / "2025-02-01_p001.jsonl.gz").unlink()
    assert verify(tmp_path, state)["mismatches"] == {"2025-02-01": {"expected": 1, "unique": 0}}
    loaded = json.loads((tmp_path / "_state.json").read_text())
    assert loaded["slices"]["2025-01-15"]["count"] == 3
