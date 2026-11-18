from pipeline.faculty.matching import (
    AuthorProfile,
    FacultyInput,
    MatchConfig,
    TfIdf,
    add_corpus_evidence,
    match_all,
    profiles_from_directory,
)

ML = "graph neural networks data mining text mining knowledge graph information network"
BIO = "protein folding molecular dynamics enzyme kinetics cell membrane biophysics"
ART = "painting sculpture studio art contemporary art exhibition gallery"


def profile(aid: str, names: list[str], text: str, works: int = 50) -> AuthorProfile:
    return AuthorProfile(id=aid, names=names, works_count=works, in_directory=True, text=[text])


def fac(fid: int, name: str, kw: str) -> FacultyInput:
    return FacultyInput(id=fid, names=[name], keywords=kw.split(" "))


# Padding authors so IDF is meaningful
FILLER = {
    f"F{i}": profile(f"F{i}", [f"Person{i} Other{i}"], t)
    for i, t in enumerate([BIO, ART, BIO, ART, "soil crop yield irrigation", "tax policy law"])
}


def run(faculty: list[FacultyInput], extra: dict[str, AuthorProfile]):  # type: ignore[no-untyped-def]
    return match_all(faculty, {**FILLER, **extra}, MatchConfig())


def test_unique_name_with_topical_overlap_is_accepted() -> None:
    accepted, _ = run([fac(1, "Ruta Mehta", ML)], {"A1": profile("A1", ["Ruta Mehta"], ML)})
    assert [(c.faculty_id, c.author_id, c.method) for c in accepted] == [(1, "A1", "unique")]


def test_same_name_different_field_is_rejected() -> None:
    accepted, scored = run([fac(1, "Bo Li", ML)], {"A1": profile("A1", ["Bo Li"], ART)})
    assert accepted == []
    assert scored[1][0].method == "low_topic"


def test_common_name_keeps_only_topically_close_profiles() -> None:
    extra = {
        "MAIN": profile("MAIN", ["Jiawei Han", "J. Han"], ML, works=1000),
        "FRAG": profile("FRAG", ["Jiawei Han"], ML + " knowledge graph", works=8),
        "STUDENT": profile("STUDENT", ["Jiawei Han"], BIO + " graph", works=6),
    }
    accepted, _ = run([fac(1, "Jiawei Han", ML)], extra)
    assert {c.author_id for c in accepted} == {"MAIN", "FRAG"}
    assert all(c.method == "ambiguous" for c in accepted)


def test_merged_profile_under_other_name_is_never_linked() -> None:
    extra = {"A1": profile("A1", ["Ziqi Pang", "Yu-Xiong Wang"], ML)}
    accepted, scored = run([fac(1, "Yuxiong Wang", ML)], extra)
    assert accepted == []
    assert scored[1][0].method == "alias_only"


def test_author_assigned_to_single_best_faculty() -> None:
    extra = {"A1": profile("A1", ["Yi Lu"], BIO)}
    accepted, _ = run([fac(1, "Yi Lu", ML), fac(2, "Yi Lu", BIO)], extra)
    assert [(c.faculty_id, c.author_id) for c in accepted] == [(2, "A1")]


def test_nickname_and_married_name_matches() -> None:
    extra = {
        "R": profile("R", ["James M. Rehg"], ML),
        "L": profile("L", ["Deborah A. Levin"], BIO),
    }
    accepted, _ = run([fac(1, "Jim Rehg", ML), fac(2, "Deborah Levin Fliflet", BIO)], extra)
    assert {(c.faculty_id, c.author_id) for c in accepted} == {(1, "R"), (2, "L")}


def test_profiles_from_directory_and_corpus_merge() -> None:
    raw = [
        {
            "id": "https://openalex.org/A1",
            "display_name": "Ann Lee",
            "display_name_alternatives": ["Lee, Ann", "Ann Lee"],
            "orcid": "https://orcid.org/0000-1",
            "works_count": 10,
            "summary_stats": {"h_index": 4},
            "topics": [{"display_name": "Graph Mining", "subfield": {"display_name": "AI"}}],
        }
    ]
    profiles = profiles_from_directory(raw)
    add_corpus_evidence(
        profiles,
        [
            {
                "author_id": "A1",
                "display_name": "Ann Lee",
                "orcid": None,
                "title": "T1",
                "keywords": ["k"],
                "topic": "x",
            },
            {
                "author_id": "A2",
                "display_name": "Bob Ray",
                "orcid": None,
                "title": "T2",
                "keywords": None,
                "topic": "",
            },
        ],
    )
    a1, a2 = profiles["A1"], profiles["A2"]
    assert a1.names == ["Ann Lee", "Lee, Ann"] and a1.orcid == "0000-1" and a1.h_index == 4
    assert a1.corpus_uiuc_works == 1 and "Graph Mining AI" in a1.text
    assert not a2.in_directory and a2.corpus_uiuc_works == 1


def test_tfidf_cosine_bounds() -> None:
    tf = TfIdf([["a", "b"], ["b", "c"], ["c", "d"]])
    v1, v2 = tf.vector(["a", "b"]), tf.vector(["a", "b"])
    assert abs(TfIdf.cosine(v1, v2) - 1.0) < 1e-9
    assert TfIdf.cosine(tf.vector(["a"]), tf.vector(["d"])) == 0.0
