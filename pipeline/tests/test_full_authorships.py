from datetime import date
from pathlib import Path

import httpx
from factories import MIT, UIUC, UIUC_INST, raw_work

from pipeline.fetch_full_authorships import (
    fetch_full_authorships,
    load_full_authorships,
    truncated_work_ids,
)
from pipeline.load_works import build_corpus
from pipeline.openalex.client import OpenAlexClient


def _a(aid: str, inst: dict) -> dict:  # type: ignore[type-arg]
    return {
        "author": {"id": f"https://openalex.org/{aid}", "display_name": aid},
        "institutions": [inst],
        "raw_affiliation_strings": [],
    }


def test_truncated_work_ids() -> None:
    raws = [
        raw_work(id="https://openalex.org/W1", is_authors_truncated=True),
        raw_work(id="https://openalex.org/W2"),
    ]
    assert truncated_work_ids(raws) == ["W1"]


def test_fetch_caches_and_skips_existing(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return httpx.Response(200, json={"id": "x", "authorships": [_a("A1", UIUC_INST)]})

    client = OpenAlexClient(transport=httpx.MockTransport(handler), sleep=lambda _: None)
    with client:
        assert fetch_full_authorships(client, ["W1", "W2"], tmp_path) == 2
        assert fetch_full_authorships(client, ["W1", "W2"], tmp_path) == 0
    assert calls == ["/works/W1", "/works/W2"]
    assert set(load_full_authorships(tmp_path)) == {"W1", "W2"}


def test_build_corpus_uses_full_authorships_for_truncated_works() -> None:
    # Visible (truncated) list has no UIUC author; the complete list does.
    raw = raw_work(authorships=[_a("A1", MIT)], is_authors_truncated=True)
    full = {"W1": [_a("A1", MIT), _a("A2", UIUC_INST)]}
    corpus = build_corpus(
        [raw],
        institution_id=UIUC,
        window_start=date(2024, 11, 16),
        window_end=date(2026, 11, 16),
        top_n=10,
        full_authorships=full,
    )
    assert corpus.report["truncated_authorships_completed"] == 1
    ships = corpus.works[0].authorships
    assert [(s["author_id"], s["is_uiuc"]) for s in ships] == [("A1", False), ("A2", True)]
