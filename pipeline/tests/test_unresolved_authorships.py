"""Unresolved (null-id) authorships, duplicate listings, version matching by names."""

from datetime import date
from typing import Any

import pytest
from factories import MIT, UIUC, UIUC_INST, raw_work
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Author, Authorship
from pipeline.faculty.matching import FacultyInput, UnresolvedAuthorship, attribute_unresolved
from pipeline.load_works import build_corpus, load_corpus
from pipeline.openalex.transform import dedupe, normalize_title, normalize_work


def ship(aid: str | None, name: str, insts: list[dict[str, Any]]) -> dict[str, Any]:
    author = {"id": f"https://openalex.org/{aid}" if aid else None, "display_name": name}
    return {"author": author, "institutions": insts, "raw_affiliation_strings": []}


def test_unresolved_uiuc_authorship_is_kept() -> None:
    nw = normalize_work(
        raw_work(authorships=[ship("A1", "Bob Ray", [MIT]), ship(None, "Zhao, Ziyi", [UIUC_INST])]),
        UIUC,
    )
    assert [(a["author_id"], a["author_name"], a["is_uiuc"]) for a in nw.authorships] == [
        ("A1", "Bob Ray", False),
        (None, "Zhao, Ziyi", True),
    ]
    assert [a["id"] for a in nw.authors] == ["A1"]  # no author row for unresolved people


def test_duplicate_listing_merges_uiuc_flag() -> None:
    nw = normalize_work(
        raw_work(authorships=[ship("A1", "Ann Lee", [MIT]), ship("A1", "Ann Lee", [UIUC_INST])]),
        UIUC,
    )
    assert len(nw.authorships) == 1 and nw.authorships[0]["is_uiuc"] is True


def test_normalize_title_splits_unicode_hyphens() -> None:
    assert normalize_title("CARE‑PD: a dataset") == normalize_title("CARE-PD: A Dataset")
    assert normalize_title("CARE‑PD") == "care pd"


def test_versions_matched_when_name_order_flips() -> None:
    title = "Physics informed deformable gaussian splatting towards unified laws"
    a = normalize_work(
        raw_work(
            id="https://openalex.org/W1",
            doi="10.1/a",
            title=title,
            type="preprint",
            authorships=[ship("A1", "Haoqin Hong", [UIUC_INST])],
        ),
        UIUC,
    )
    b = normalize_work(
        raw_work(
            id="https://openalex.org/W2",
            doi="10.1/b",
            title=title,
            authorships=[ship("A2", "Hong Haoqin", [UIUC_INST])],
        ),
        UIUC,
    )
    assert dedupe([a, b])[1] == 1


def test_versions_matched_by_author_names_when_ids_differ() -> None:
    title = "Clip and verify linear constraint driven domain clipping for verification"
    a = normalize_work(
        raw_work(
            id="https://openalex.org/W1",
            doi="10.1/a",
            title=title,
            type="preprint",
            authorships=[ship("A1", "Huan Zhang", [UIUC_INST])],
        ),
        UIUC,
    )
    b = normalize_work(
        raw_work(
            id="https://openalex.org/W2",
            doi="10.1/b",
            title=title,
            authorships=[ship("A9", "H. Zhang", [UIUC_INST])],
        ),
        UIUC,
    )
    kept, merged = dedupe([a, b])
    assert merged == 1 and kept[0].work["id"] == "W2"  # published version preferred


FAC = [
    FacultyInput(id=1, names=["Ziyi Zhao"], keywords=["graph neural networks", "deep learning"]),
    FacultyInput(id=2, names=["Bo Li"], keywords=["adversarial machine learning"]),
    FacultyInput(id=3, names=["Bo Li"], keywords=["spatial statistics"]),
]


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            UnresolvedAuthorship("W1", "Zhao, Ziyi", "graph neural networks for molecules"),
            [(1, "W1")],
        ),
        (UnresolvedAuthorship("W2", "Ziyi Zhao", "medieval french poetry"), []),  # off-topic
        (UnresolvedAuthorship("W3", "Z. Zhao", "graph neural networks"), []),  # initials only
        (UnresolvedAuthorship("W4", "Bo Li", "adversarial machine learning attacks"), []),  # shared
    ],
)
def test_attribute_unresolved(row: UnresolvedAuthorship, expected: list[tuple[int, str]]) -> None:
    filler = [
        UnresolvedAuthorship(f"F{i}", "Nobody Here", t)
        for i, t in enumerate(["soil crops", "tax law", "opera music", "protein folding"])
    ]
    assert attribute_unresolved(FAC, [row, *filler]) == expected


@pytest.mark.db
def test_load_corpus_with_unresolved_authors_and_orphan_cleanup(db_session: Session) -> None:
    def corpus(raws: list[dict[str, Any]]) -> Any:
        return build_corpus(
            raws,
            institution_id=UIUC,
            window_start=date(2024, 11, 16),
            window_end=date(2026, 11, 16),
            top_n=10,
        ).works

    first = corpus(
        [
            raw_work(
                authorships=[
                    ship("A1", "Ann Lee", [UIUC_INST]),
                    ship(None, "Unresolved Person", [UIUC_INST]),
                ]
            )
        ]
    )
    load_corpus(db_session, first, {})
    db_session.commit()
    assert db_session.scalar(select(func.count()).select_from(Authorship)) == 2

    # Reload without A1: A1 must be removed even though authorships contain NULL author_ids
    # (regression: `NOT IN (subquery with NULL)` silently deleted nothing).
    second = corpus([raw_work(authorships=[ship(None, "Unresolved Person", [UIUC_INST])])])
    load_corpus(db_session, second, {})
    db_session.commit()
    assert db_session.get(Author, "A1") is None
    row = db_session.scalars(select(Authorship)).one()
    assert (row.author_id, row.author_name, row.is_uiuc) == (None, "Unresolved Person", True)
