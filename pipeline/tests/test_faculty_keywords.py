import pytest

from pipeline.faculty.keywords import clean_keywords, normalize_keyword, rank_keywords

PEOPLE = frozenset({"chengxiang zhai", "benjamin grosser"})


@pytest.mark.parametrize(
    ("raw", "norm"),
    [
        ("  Palladium‐Catalyzed  Cross–Coupling ", "palladium-catalyzed cross-coupling"),
        ("children ’", "children"),
        ("such peptides", "peptides"),
        ("various aerosol components", "aerosol components"),
        ("co 2", "co2"),
        ("l 1", "l1"),
    ],
)
def test_normalize_keyword(raw: str, norm: str) -> None:
    assert normalize_keyword(raw) == norm


@pytest.mark.parametrize(
    "junk",
    [
        "simon fraser university",
        "energy institute",
        "siam international conference",
        "acm sigmod record",
        "ieee engineering",
        "mi mathvariant=",
        "\\cal l",
        " decays",
        "il 61801",
        "real world",
        "system demonstrations",
        "2",
        "chengxiang zhai university",
        "benjamin grosser",
    ],
)
def test_junk_is_dropped(junk: str) -> None:
    assert clean_keywords([junk], PEOPLE) == []


@pytest.mark.parametrize(
    "keep",
    [
        "civil society",
        "journalism",
        "ieee 802.11",
        "university student retention",
        "college students",
        "co2",
        "o 3",
        "hiv",
        "ai",
        "a. hybridus",
        "el niño",
    ],
)
def test_legitimate_topics_survive(keep: str) -> None:
    assert len(clean_keywords([keep], PEOPLE)) == 1


def test_clean_keywords_dedupes_after_normalization_preserving_order() -> None:
    assert clean_keywords(["Deep Learning", "deep  learning", "Text Mining"]) == [
        "deep learning",
        "text mining",
    ]


def test_rank_keywords_prefers_supported_and_specific() -> None:
    kws = ["applied mathematics", "graph neural networks", "weird singleton phrase"]
    df = {"applied mathematics": 266, "graph neural networks": 20, "weird singleton phrase": 1}
    ranked = rank_keywords(
        kws, doc_freq=df, n_docs=1000, evidence_terms={"graph", "neural", "network"}, top_k=3
    )
    assert ranked[0] == "graph neural networks"  # backed by publications
    assert ranked.index("weird singleton phrase") < ranked.index("applied mathematics")
    assert rank_keywords(kws, doc_freq=df, n_docs=1000, top_k=1) == ["graph neural networks"]
