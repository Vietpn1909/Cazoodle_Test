import gzip
import json
from pathlib import Path

import httpx
import pytest

from pipeline.fetch_works import FetchState, fetch_works
from pipeline.openalex.client import OpenAlexClient

PAGES = {
    "*": {"meta": {"count": 5, "next_cursor": "c2"}, "results": [{"id": "W1"}, {"id": "W2"}]},
    "c2": {"meta": {"count": 5, "next_cursor": "c3"}, "results": [{"id": "W3"}, {"id": "W4"}]},
    "c3": {"meta": {"count": 5, "next_cursor": None}, "results": [{"id": "W5"}]},
}


def _client(requested: list[str]) -> OpenAlexClient:
    def handler(req: httpx.Request) -> httpx.Response:
        cursor = req.url.params["cursor"]
        requested.append(cursor)
        return httpx.Response(200, json=PAGES[cursor], headers={"x-ratelimit-cost-usd": "0.0001"})

    return OpenAlexClient(transport=httpx.MockTransport(handler), sleep=lambda _: None)


def _read_ids(out: Path) -> list[str]:
    ids = []
    for f in sorted(out.glob("page-*.jsonl.gz")):
        ids += [json.loads(line)["id"] for line in gzip.decompress(f.read_bytes()).splitlines()]
    return ids


def test_fetch_resumes_from_saved_cursor(tmp_path: Path) -> None:
    requested: list[str] = []
    with _client(requested) as c:
        s1 = fetch_works(c, tmp_path, filter_str="f", select="id", max_pages=1)
    assert (s1.pages, s1.done, s1.next_cursor) == (1, False, "c2")

    with _client(requested) as c:
        s2 = fetch_works(c, tmp_path, filter_str="f", select="id")
    assert s2.done and s2.records == 5 and s2.pages == 3
    assert requested == ["*", "c2", "c3"]  # nothing fetched twice
    assert _read_ids(tmp_path) == ["W1", "W2", "W3", "W4", "W5"]

    # completed fetch is a no-op
    with _client(requested) as c:
        fetch_works(c, tmp_path, filter_str="f", select="id")
    assert len(requested) == 3


def test_refuses_to_mix_different_filters(tmp_path: Path) -> None:
    FetchState(filter="old", select="id").save(tmp_path / "_state.json")
    with _client([]) as c, pytest.raises(SystemExit):
        fetch_works(c, tmp_path, filter_str="new", select="id")
