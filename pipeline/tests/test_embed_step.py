from datetime import date
from typing import Any

import pytest
from factories import raw_work
from fakes import FakeEmbedder
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.db.models import Faculty, FacultyWork, Meta
from pipeline.embed import faculty_items, run
from pipeline.faculty.parse import FacultyRecord
from pipeline.load_faculty import faculty_rows, load_faculty
from pipeline.load_works import build_corpus, load_corpus

UIUC = "I157725225"


def _factory(engine: Engine):  # type: ignore[no-untyped-def]
    return lambda: Session(engine)


def _load_two_works(session: Session) -> None:
    corpus = build_corpus(
        [
            raw_work(id="https://openalex.org/W1", doi="10.1/a", title="Vision for imaging"),
            raw_work(id="https://openalex.org/W2", doi="10.1/b", title="Another distinct paper"),
        ],
        institution_id=UIUC,
        window_start=date(2024, 11, 16),
        window_end=date(2026, 11, 16),
        top_n=10,
    )
    load_corpus(session, corpus.works, {})
    session.commit()


def _scalar(session: Session, sql: str, **params: Any) -> Any:
    return session.execute(text(sql), params).scalar()


@pytest.mark.db
def test_works_embedded_once_then_only_when_text_or_model_changes(
    db_engine: Engine, db_session: Session
) -> None:
    _load_two_works(db_session)
    fake = FakeEmbedder()
    factory = _factory(db_engine)

    r1 = run(factory, fake, only="works")
    assert r1["works"]["embedded_now"] == 2 and r1["works"]["up_to_date_skipped"] == 0
    assert len(fake.embedded) == 2
    assert _scalar(db_session, "select count(*) from works where vector_dims(embedding) = 384") == 2
    assert _scalar(db_session, "select count(*) from works where embedding_hash is not null") == 2
    assert db_session.get(Meta, "embedding_model").value == "fake-model"  # type: ignore[union-attr]

    r2 = run(factory, fake, only="works")  # nothing changed
    assert r2["works"]["embedded_now"] == 0 and len(fake.embedded) == 2

    db_session.execute(text("update works set abstract = 'A new abstract' where id = 'W1'"))
    db_session.commit()
    r3 = run(factory, fake, only="works")  # only the edited work is stale
    assert r3["works"]["embedded_now"] == 1 and "A new abstract" in fake.embedded[-1]

    other = FakeEmbedder(name="other-model")
    r4 = run(factory, other, only="works")  # model changed -> everything again
    assert r4["forced"] is True and r4["works"]["embedded_now"] == 2


@pytest.mark.db
def test_limit_and_force(db_engine: Engine, db_session: Session) -> None:
    _load_two_works(db_session)
    fake = FakeEmbedder()
    factory = _factory(db_engine)
    assert run(factory, fake, only="works", limit=1)["works"]["embedded_now"] == 1
    assert run(factory, fake, only="works")["works"]["embedded_now"] == 1  # the remaining one
    assert run(factory, fake, only="works", force=True)["works"]["embedded_now"] == 2


@pytest.mark.db
def test_faculty_profile_text_uses_keywords_and_work_topics(
    db_engine: Engine, db_session: Session
) -> None:
    _load_two_works(db_session)
    rows, _ = faculty_rows(
        [
            FacultyRecord("a@x.edu", "Ann Lee", "Professor", None, ["computer vision", "mri"]),
            FacultyRecord("b@x.edu", "Bob Ray", "Professor", None, []),
        ]
    )
    load_faculty(db_session, rows)
    db_session.commit()
    ann = db_session.query(Faculty).filter_by(email="a@x.edu").one()
    db_session.add_all(
        [FacultyWork(faculty_id=ann.id, work_id=w, via="author_id") for w in ("W1", "W2")]
    )
    db_session.commit()

    items = {i.id: i for i in faculty_items(db_session)}
    assert "Research interests: computer vision, mri." in items[ann.id].text
    assert "Publication topics: Advanced Neural Network Applications." in items[ann.id].text

    fake = FakeEmbedder()
    report = run(_factory(db_engine), fake, only="faculty")
    # Bob has nothing to describe and no vector yet: nothing to write for him
    assert report["faculty"]["embedded_now"] == 1
    assert report["faculty"]["without_text"] == 1
    db_session.expire_all()
    bob = db_session.query(Faculty).filter_by(email="b@x.edu").one()
    ann = db_session.query(Faculty).filter_by(email="a@x.edu").one()
    assert bob.embedding is None and bob.profile_text is None
    assert ann.embedding is not None and "computer vision" in (ann.profile_text or "")
    # generated tsvector supports full-text expertise search
    hit = _scalar(
        db_session,
        "select count(*) from faculty"
        " where tsv @@ websearch_to_tsquery('english', 'computer vision')",
    )
    assert hit == 1

    assert run(_factory(db_engine), fake, only="faculty")["faculty"]["embedded_now"] == 0

    # Bob gains keywords -> embedded; then loses them again -> his vector is cleared
    db_session.execute(
        text("update faculty set keywords_clean = array['quantum optics'] where id = :i"),
        {"i": bob.id},
    )
    db_session.commit()
    assert run(_factory(db_engine), fake, only="faculty")["faculty"]["embedded_now"] == 1
    assert _scalar(db_session, "select embedding is not null from faculty where id=:i", i=bob.id)
    db_session.execute(
        text("update faculty set keywords_clean = '{}' where id = :i"), {"i": bob.id}
    )
    db_session.commit()
    assert run(_factory(db_engine), fake, only="faculty")["faculty"]["embedded_now"] == 1
    assert _scalar(db_session, "select embedding is null from faculty where id = :i", i=bob.id)


@pytest.mark.slow
def test_real_model_is_normalised_and_semantically_sensible() -> None:
    from app.core.config import get_settings
    from pipeline.embedding.model import FastEmbedder

    s = get_settings()
    emb = FastEmbedder(s.embedding_model, s.embedding_dim)
    docs = [
        "Deep learning for tumor detection in CT scans of patients",
        "Medieval French poetry and manuscript culture",
        "Autonomous weapons and the ethics of AI in armed conflict",
        "Soil nitrogen cycling in maize fields",
    ]
    q_med, q_war = emb.embed(
        ["computer vision in healthcare", "artificial intelligence in war and military conflict"]
    )
    d = emb.embed(docs)
    assert d.shape == (4, 384) and abs(float((d[0] ** 2).sum()) - 1) < 1e-3
    assert float(q_med @ d[0]) > float(q_med @ d[1]) and float(q_med @ d[0]) > float(q_med @ d[3])
    assert float(q_war @ d[2]) > float(q_war @ d[1]) and float(q_war @ d[2]) > float(q_war @ d[3])
