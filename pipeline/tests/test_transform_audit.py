"""Tests for the data-audit fixes (scope, truncation, versions, fairness, cleaning)."""

from datetime import date
from typing import Any

import pytest
from factories import MIT, UIUC, UIUC_INST, raw_work

from pipeline.openalex.transform import (
    MAX_STORED_AUTHORSHIPS,
    clean_abstract,
    compute_rank_scores,
    dedupe,
    drop_reason,
    field_percentiles,
    is_uiuc_authorship,
    normalize_work,
)

SYSTEM = {
    "id": "https://openalex.org/I2801919071",
    "display_name": "University of Illinois System",
    "lineage": ["https://openalex.org/I2801919071"],
}


def authorship(aid: str, insts: list[dict[str, Any]], raw: list[str]) -> dict[str, Any]:
    return {
        "author": {"id": f"https://openalex.org/{aid}", "display_name": aid},
        "institutions": insts,
        "raw_affiliation_strings": raw,
    }


@pytest.mark.parametrize(
    ("insts", "raw", "expected"),
    [
        ([UIUC_INST], [], True),
        ([SYSTEM], ["Dept. of Physics, University of Illinois, Urbana, IL"], True),
        ([SYSTEM], ["University of Illinois Chicago, Chicago, IL"], False),
        ([SYSTEM], ["University of Illinois"], False),  # campus unknown
        ([MIT], ["Urbana"], False),
    ],
)
def test_is_uiuc_authorship(insts: list[dict[str, Any]], raw: list[str], expected: bool) -> None:
    assert is_uiuc_authorship(authorship("A1", insts, raw), UIUC) is expected


def test_work_without_uiuc_authorship_dropped_unless_truncated() -> None:
    other_campus = [authorship("A1", [SYSTEM], ["UIC, Chicago"])]
    assert drop_reason(raw_work(authorships=other_campus), UIUC) == "no_uiuc_authorship"
    truncated = raw_work(authorships=[authorship("A1", [MIT], [])], is_authors_truncated=True)
    assert drop_reason(truncated, UIUC) is None  # cannot verify -> keep


@pytest.mark.parametrize(
    ("title", "dropped"),
    [
        ("Preface", True),
        ("Introduction", True),
        ("Conclusions", True),
        ("Cover Picture: X-Ray Absorption Studies (Chem. Methods 7/2025)", True),
        ("Thank You to Our 2025 Peer Reviewers", True),
        ("PREFACE - A Reinforcement Learning Framework for Code Verification", False),
        ("Peer reviewers disagree: a study of review consistency", False),
        ("Introduction to Quantum Error Correction", False),
        ("Cover crops and double-crop soybean", False),
    ],
)
def test_generic_titles(title: str, dropped: bool) -> None:
    assert (drop_reason(raw_work(title=title), UIUC) == "generic_title") is dropped


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Abstract Background Lipolysis matters.", "Background Lipolysis matters."),
        ("ABSTRACT: We study X.", "We study X."),
        ("Summary—We study X.", "We study X."),
        ("<jats:p>We <i>study</i> X.</jats:p>", "We study X."),
        ("Abstraction layers are useful.", "Abstraction layers are useful."),
        ("", None),
    ],
)
def test_clean_abstract(raw: str, clean: str | None) -> None:
    assert clean_abstract(raw) == clean


def test_new_fields_and_authorship_cap() -> None:
    many = [authorship(f"A{i}", [MIT], []) for i in range(MAX_STORED_AUTHORSHIPS + 50)]
    many.append(authorship("AUIUC", [UIUC_INST], ["UIUC"]))
    nw = normalize_work(
        raw_work(
            authorships=many,
            publication_date="2026-01-01",
            primary_location={
                "landing_page_url": "https://x.org/p",
                "source": {"id": "https://openalex.org/S1", "display_name": "J", "type": "journal"},
            },
            open_access={"is_oa": True, "oa_status": "gold"},
            funders=[{"id": "https://openalex.org/F1", "display_name": "NSF"}],
            citation_normalized_percentile={"value": 0.99, "is_in_top_1_percent": True},
        ),
        UIUC,
    )
    w = nw.work
    assert w["authors_count"] == MAX_STORED_AUTHORSHIPS + 51
    assert len(nw.authorships) == MAX_STORED_AUTHORSHIPS + 1  # first 100 + the UIUC author
    assert nw.authorships[-1]["author_id"] == "AUIUC" and nw.authorships[-1]["is_uiuc"]
    assert w["date_precision"] == "year"
    assert (w["landing_page_url"], w["venue_type"], w["venue_id"]) == (
        "https://x.org/p",
        "journal",
        "S1",
    )
    assert (w["oa_status"], w["is_top_1pct"], w["funders"]) == (
        "gold",
        True,
        [{"id": "F1", "name": "NSF"}],
    )


def _nw(wid: str, **kw: Any):  # type: ignore[no-untyped-def]
    return normalize_work(raw_work(id=f"https://openalex.org/{wid}", **kw), UIUC)


def test_dedupe_merges_cross_year_versions_preferring_published() -> None:
    title = "Physics informed deformable gaussian splatting for materials"
    pre = _nw(
        "Wpre",
        doi="10.48550/arxiv.1",
        title=title,
        type="preprint",
        publication_date="2025-11-09",
        publication_year=2025,
        cited_by_count=40,
        primary_location={"source": {"type": "repository", "display_name": "arXiv"}},
    )
    pub = _nw(
        "Wpub",
        doi="10.1609/aaai.1",
        title=title,
        type="conference-paper",
        publication_date="2026-03-14",
        publication_year=2026,
        cited_by_count=3,
    )
    kept, merged = dedupe([pre, pub])
    assert merged == 1 and len(kept) == 1
    k = kept[0].work
    assert k["id"] == "Wpub" and k["cited_by_count"] == 40 and k["version_ids"] == ["Wpre"]


def test_dedupe_does_not_merge_same_title_without_shared_authors() -> None:
    title = "A survey of graph neural networks for recommendation systems"
    a = _nw("Wa", doi="10.1/a", title=title)
    b = _nw("Wb", doi="10.1/b", title=title, authorships=[authorship("Z9", [UIUC_INST], [])])
    short_a = _nw("Wc", doi="10.1/c", title="Introduction to X")  # < 5 words
    short_b = _nw("Wd", doi="10.1/d", title="Introduction to X")
    kept, merged = dedupe([a, b, short_a, short_b])
    assert merged == 0 and len(kept) == 4


def test_field_percentiles_rank_within_field() -> None:
    cs = [_nw(f"C{i}", cited_by_count=10 * i) for i in range(40)]
    for w in cs:
        w.field_id = "17"
    econ = [_nw(f"E{i}", cited_by_count=i) for i in range(40)]
    for w in econ:
        w.field_id = "20"
    pct = field_percentiles([*cs, *econ], [int(w.work["cited_by_count"]) for w in [*cs, *econ]])
    assert pct[39] == pytest.approx(1.0) and pct[79] == pytest.approx(1.0)  # top of each field


def test_missing_fwci_is_neutral_not_zero() -> None:
    base = dict(
        cited_by_count=50,
        citation_normalized_percentile={"value": 0.5},
        publication_date="2025-06-01",
    )
    with_fwci = [_nw(f"F{i}", fwci=float(i), **base) for i in range(10)]
    unknown = _nw("Fnone", fwci=None, **base)
    lowest = _nw("Flow", fwci=0.0, **base)
    scores = compute_rank_scores(
        [*with_fwci, unknown, lowest], date(2024, 11, 16), date(2026, 11, 16)
    )
    assert scores[10] > scores[11]  # unknown FWCI ranks above a known-zero FWCI
