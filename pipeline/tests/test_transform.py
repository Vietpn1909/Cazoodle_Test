from datetime import date
from typing import Any

import pytest
from factories import BECKMAN, UIUC, UIUC_INST, raw_work

from pipeline.openalex.transform import (
    NormalizedWork,
    compute_rank_scores,
    dedupe,
    drop_reason,
    normalize_doi,
    normalize_work,
    percentile_ranks,
    rebuild_abstract,
    short_id,
)


def test_short_id_and_doi() -> None:
    assert short_id("https://openalex.org/W123") == "W123"
    assert short_id("https://openalex.org/fields/17") == "17"
    assert short_id(None) is None
    assert normalize_doi("https://doi.org/10.1000/ABC") == "10.1000/abc"


def test_rebuild_abstract_orders_words_by_position() -> None:
    assert rebuild_abstract({"world": [1], "hello": [0, 2]}) == "hello world hello"
    assert rebuild_abstract(None) is None


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({}, None),
        ({"is_paratext": True}, "paratext"),
        ({"is_retracted": True}, "retracted"),
        ({"title": "  "}, "missing_title"),
        ({"type": "erratum"}, "type_not_allowed"),
        ({"language": "ru"}, "non_english"),
        ({"language": None}, None),
    ],
)
def test_drop_reason_rules(overrides: dict[str, Any], reason: str | None) -> None:
    assert drop_reason(raw_work(**overrides), UIUC) == reason


def test_misaffiliation_detected_when_raw_text_points_elsewhere() -> None:
    bad = raw_work(
        authorships=[
            {
                "author": {"id": "https://openalex.org/A9", "display_name": "X"},
                "institutions": [UIUC_INST],
                "raw_affiliation_strings": ["Moscow State University, Russia"],
            }
        ]
    )
    assert drop_reason(bad, UIUC) == "suspected_misaffiliation"


def test_misaffiliation_not_flagged_when_raw_text_missing() -> None:
    unknown = raw_work(
        authorships=[
            {
                "author": {"id": "https://openalex.org/A9", "display_name": "X"},
                "institutions": [UIUC_INST],
                "raw_affiliation_strings": [],
            }
        ]
    )
    assert drop_reason(unknown, UIUC) is None


def test_normalize_work_maps_fields_and_relations() -> None:
    nw = normalize_work(raw_work(), UIUC)
    w = nw.work
    assert w["id"] == "W1"
    assert w["doi"] == "10.1000/abc"
    assert w["title"] == "Vision models for medical imaging"  # tags stripped
    assert w["abstract"] == "Deep learning helps"
    assert w["publication_date"] == date(2025, 6, 1)
    assert (w["venue_name"], w["is_oa"], w["primary_topic_id"]) == (
        "Nature Medicine",
        True,
        "T10036",
    )
    assert w["sdgs"] == [{"id": "3", "name": "Good health", "score": 0.8}]
    assert nw.citation_percentile == 0.9

    # duplicate A1 authorship collapsed; order preserved
    assert [(a["author_id"], a["is_uiuc"]) for a in nw.authorships] == [("A1", True), ("A2", False)]
    assert nw.authors[0]["orcid"] == "0000-0001"
    assert nw.authorships[0]["is_corresponding"] is True
    assert {i["institution_id"] for i in nw.work_institutions} == {UIUC, "I999", "I63966007"}
    assert nw.topics[0]["field"] == "Computer Science" and nw.topics[0]["subfield_id"] == "1707"
    assert nw.work_topics == [{"work_id": "W1", "topic_id": "T10036", "score": 0.99}]


def test_is_uiuc_via_lineage_of_subunit() -> None:
    raw = raw_work(
        authorships=[
            {
                "author": {"id": "https://openalex.org/A5", "display_name": "C"},
                "institutions": [BECKMAN],
                "raw_affiliation_strings": ["Beckman Institute, Urbana, IL"],
            }
        ]
    )
    assert normalize_work(raw, UIUC).authorships[0]["is_uiuc"] is True


def test_dedupe_keeps_most_cited_copy() -> None:
    a = normalize_work(raw_work(id="https://openalex.org/W1", cited_by_count=1), UIUC)
    b = normalize_work(raw_work(id="https://openalex.org/W2", cited_by_count=50), UIUC)
    c = normalize_work(
        raw_work(id="https://openalex.org/W3", doi=None, title="Another paper"), UIUC
    )
    kept, dropped = dedupe([a, b, c])
    assert dropped == 1
    assert {k.work["id"] for k in kept} == {"W2", "W3"}


def test_percentile_ranks_ties_and_none() -> None:
    assert percentile_ranks([1.0, 2.0, 2.0, 3.0, None]) == [0.0, 0.5, 0.5, 1.0, 0.0]
    assert percentile_ranks([None]) == [0.0]


def test_rank_scores_prefer_impact_and_recency() -> None:
    def nw(wid: str, pub: str, cnp: float, fwci: float) -> NormalizedWork:
        return normalize_work(
            raw_work(
                id=f"https://openalex.org/{wid}",
                publication_date=pub,
                fwci=fwci,
                citation_normalized_percentile={"value": cnp},
            ),
            UIUC,
        )

    works = [nw("Wlow", "2025-01-01", 0.1, 0.1), nw("Whigh", "2025-01-01", 0.95, 5.0)]
    low, high = compute_rank_scores(works, date(2024, 11, 16), date(2026, 11, 16))
    assert high > low
    assert 0.0 <= low <= 1.0 and 0.0 <= high <= 1.0


def test_shrink_pulls_low_evidence_metrics_to_prior() -> None:
    from pipeline.openalex.transform import shrink

    assert shrink(1.0, 0, 5.0) == 0.5
    assert shrink(1.0, 2, 5.0) == pytest.approx(0.5 + 0.5 * 2 / 7)
    assert shrink(1.0, 1000, 5.0) > 0.99


def test_brand_new_outlier_does_not_beat_established_paper() -> None:
    def nw(wid: str, pub: str, cites: int, cnp: float, fwci: float) -> NormalizedWork:
        return normalize_work(
            raw_work(
                id=f"https://openalex.org/{wid}",
                publication_date=pub,
                cited_by_count=cites,
                fwci=fwci,
                citation_normalized_percentile={"value": cnp},
            ),
            UIUC,
        )

    fresh = nw("Wfresh", "2026-11-13", 2, 1.0, 66.0)  # 3 days old, 2 citations
    solid = nw("Wsolid", "2025-09-01", 150, 0.97, 8.0)
    filler = [nw(f"W{i}", "2025-06-01", i, 0.5, 1.0) for i in range(20)]
    scores = compute_rank_scores([fresh, solid, *filler], date(2024, 11, 16), date(2026, 11, 16))
    assert scores[1] > scores[0]
