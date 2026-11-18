from datetime import date

import pytest
from factories import raw_work
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Author, Faculty, FacultyAuthorMap
from pipeline.faculty.matching import AuthorProfile, Candidate
from pipeline.faculty.parse import FacultyRecord
from pipeline.load_faculty import faculty_rows, load_faculty
from pipeline.load_works import build_corpus, load_corpus
from pipeline.match_faculty import write_results


def rec(email: str, name: str, kws: list[str]) -> FacultyRecord:
    return FacultyRecord(
        email=email, name=name, position="Professor", photo_url=None, keywords_raw=kws
    )


def test_faculty_rows_report() -> None:
    rows, report = faculty_rows(
        [
            rec("a@x.edu", "Ann Lee", ["Deep Learning", "simon fraser university", "Bob Ray"]),
            rec("b@x.edu", "Bob Ray", ["ecology"]),
            rec("c@x.edu", "Bob  Ray", ["ecology"]),
        ]
    )
    assert rows[0]["keywords_clean"] == ["deep learning"]  # venue + colleague name dropped
    assert rows[0]["name_norm"] == "ann lee"
    assert report["keywords"]["raw_mentions"] == 5
    assert report["keywords"]["kept_mentions"] == 3
    assert report["shared_names"] == ["bob ray"]


@pytest.mark.db
def test_load_faculty_upserts_by_email_and_prunes(db_session: Session) -> None:
    rows, _ = faculty_rows([rec("a@x.edu", "Ann Lee", ["x"]), rec("b@x.edu", "Bob Ray", ["y"])])
    load_faculty(db_session, rows)
    db_session.commit()
    ids = dict(db_session.execute(select(Faculty.email, Faculty.id)).all())

    rows2, _ = faculty_rows([rec("a@x.edu", "Ann B. Lee", ["zoology"])])
    load_faculty(db_session, rows2)
    db_session.commit()
    db_session.expire_all()
    remaining = db_session.scalars(select(Faculty)).all()
    assert [(f.email, f.id, f.name, f.keywords_clean) for f in remaining] == [
        ("a@x.edu", ids["a@x.edu"], "Ann B. Lee", ["zoology"])  # id stable, b@ pruned
    ]


@pytest.mark.db
def test_write_results_links_survive_works_reload(db_session: Session) -> None:
    rows, _ = faculty_rows([rec("a@x.edu", "Ann Lee", ["vision"])])
    load_faculty(db_session, rows)
    db_session.commit()
    fid = db_session.scalar(select(Faculty.id))
    assert fid is not None

    # A directory-only author (no works in the corpus) linked to the faculty member.
    profile = AuthorProfile(
        id="A777",
        names=["Ann Lee"],
        orcid="0000-9",
        works_count=42,
        cited_by_count=900,
        h_index=12,
        in_directory=True,
        text=["vision"],
    )
    cand = Candidate(
        faculty_id=fid,
        author_id="A777",
        name=1.0,
        topic=0.3,
        confidence=0.95,
        method="unique",
        evidence={"author_names": ["Ann Lee"]},
    )
    write_results(db_session, [cand], {"A777": profile}, [{"id": fid, "keywords_top": ["vision"]}])
    db_session.commit()

    link = db_session.scalars(select(FacultyAuthorMap)).one()
    assert (link.author_id, link.method, link.evidence["topic"]) == ("A777", "unique", 0.3)
    author = db_session.get(Author, "A777")
    assert author is not None and (author.h_index, author.works_count) == (12, 42)
    assert db_session.scalar(select(Faculty.keywords_top)) == ["vision"]

    # Reloading the works corpus must not delete linked authors (they have no authorships).
    corpus = build_corpus(
        [raw_work()],
        institution_id="I157725225",
        window_start=date(2024, 11, 16),
        window_end=date(2026, 11, 16),
        top_n=10,
    )
    load_corpus(db_session, corpus.works, {})
    db_session.commit()
    assert db_session.scalar(select(func.count()).select_from(FacultyAuthorMap)) == 1
    assert db_session.get(Author, "A777") is not None
