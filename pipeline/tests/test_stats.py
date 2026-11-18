from datetime import date
from typing import Any

import pytest
from factories import MIT, UIUC, UIUC_INST, raw_work

from pipeline.openalex.transform import NormalizedWork, normalize_work
from pipeline.stats.compute import (
    OTHER_BUCKET,
    bucket,
    clean_venue_name,
    collaboration_stats,
    compute_stats,
    fields_stats,
    funder_stats,
    infer_uiuc_units,
    month_keys,
    output_stats,
    overview_stats,
    rising_stats,
    sdg_stats,
    venue_stats,
)

START, END = date(2024, 11, 16), date(2026, 11, 16)
TOPIC_CS = {
    "id": "https://openalex.org/T1",
    "display_name": "Computer Vision Methods",
    "score": 0.9,
    "subfield": {"id": "https://openalex.org/subfields/1707", "display_name": "Computer Vision"},
    "field": {"id": "https://openalex.org/fields/17", "display_name": "Computer Science"},
    "domain": {"id": "https://openalex.org/domains/3", "display_name": "Physical Sciences"},
}
TOPIC_ECON = {
    "id": "https://openalex.org/T2",
    "display_name": "Labor Markets",
    "score": 0.9,
    "subfield": {"id": "https://openalex.org/subfields/2002", "display_name": "Economics"},
    "field": {"id": "https://openalex.org/fields/20", "display_name": "Economics, Econometrics"},
    "domain": {"id": "https://openalex.org/domains/2", "display_name": "Social Sciences"},
}
WOESE = {
    "id": "https://openalex.org/I1001",
    "display_name": "Woese Institute",
    "country_code": "US",
    "type": "facility",
    "lineage": ["https://openalex.org/I1001"],
}
ZJU = {
    "id": "https://openalex.org/I1002",
    "display_name": "Zhejiang University",
    "country_code": "CN",
    "type": "education",
    "lineage": ["https://openalex.org/I1002"],
}


def ship(aid: str, insts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "author": {"id": f"https://openalex.org/{aid}", "display_name": aid},
        "institutions": insts,
        "raw_affiliation_strings": [],
    }


_n = 0


def mk(**kw: Any) -> NormalizedWork:
    global _n
    _n += 1
    base: dict[str, Any] = {
        "id": f"https://openalex.org/W{_n}",
        "doi": f"10.1/{_n}",
        "title": f"Distinct title number {_n} about things",
        "publication_date": "2025-06-15",
        "publication_year": 2025,
        "primary_topic": TOPIC_CS,
        "topics": [TOPIC_CS],
    }
    base.update(kw)
    return normalize_work(raw_work(**base), UIUC)


def venue(name: str, typ: str = "journal", sid: str = "S1") -> dict[str, Any]:
    return {"source": {"id": f"https://openalex.org/{sid}", "display_name": name, "type": typ}}


def test_month_keys_and_buckets() -> None:
    assert month_keys(date(2024, 11, 16), date(2025, 2, 3)) == [
        "2024-11",
        "2024-12",
        "2025-01",
        "2025-02",
    ]
    assert bucket("review") == "Journal articles" and bucket("dissertation") == OTHER_BUCKET


def test_output_uses_day_precision_for_months_and_reports_year_only_separately() -> None:
    works = [
        mk(publication_date="2025-03-10", type="article"),
        mk(publication_date="2025-03-20", type="preprint"),
        mk(publication_date="2025-01-01", publication_year=2025),  # year-only (Jan 1)
        mk(publication_date="2024-11-20", publication_year=2024),
    ]
    out = output_stats(works, START, END)
    by_month = {m["month"]: m for m in out["by_month"]}
    assert by_month["2025-03"]["works"] == 2
    assert by_month["2025-03"]["by_type"]["Preprints"] == 1
    assert by_month["2025-01"]["works"] == 0  # the Jan 1 work is not a January spike
    assert out["year_only_dates_by_year"] == {"2025": 1} and out["year_only_dates_total"] == 1
    assert out["by_year"] == {"2024": 1, "2025": 3}
    assert by_month["2024-11"]["partial"] and by_month["2026-11"]["partial"]
    assert not by_month["2025-06"]["partial"]
    assert len(out["by_month"]) == 25  # every month of the window, zeros included


def test_fields_tree_sums_and_unclassified() -> None:
    works = [
        mk(cited_by_count=10),
        mk(cited_by_count=5),
        mk(primary_topic=TOPIC_ECON, topics=[TOPIC_ECON], cited_by_count=1),
        mk(primary_topic=None, topics=[]),
    ]
    f = fields_stats(works)
    by_domain = {d["name"]: d for d in f["domains"]}
    assert by_domain["Physical Sciences"]["works"] == 2
    assert by_domain["Physical Sciences"]["citations"] == 15
    cs = by_domain["Physical Sciences"]["children"][0]
    assert cs["name"] == "Computer Science" and cs["children"][0]["name"] == "Computer Vision"
    assert cs["children"][0]["top_topics"] == [{"name": "Computer Vision Methods", "works": 2}]
    assert by_domain["Unclassified"]["works"] == 1
    assert f["classified_works"] == 3 and sum(d["works"] for d in f["domains"]) == 4


def test_rising_compares_shares_not_counts_and_skips_venueless_works() -> None:
    # previous half: 20 CS + 20 econ; recent half: 40 CS + 20 econ -> CS share up, econ share down
    def many(n: int, topic: dict[str, Any], d: str) -> list[NormalizedWork]:
        return [
            mk(
                publication_date=d,
                primary_topic=topic,
                topics=[topic],
                primary_location=venue("J"),
            )
            for _ in range(n)
        ]

    works = [
        *many(20, TOPIC_CS, "2025-03-01"),
        *many(20, TOPIC_ECON, "2025-03-01"),
        *many(40, TOPIC_CS, "2026-06-01"),
        *many(20, TOPIC_ECON, "2026-06-01"),
        # 50 venue-less bulk records in a third topic must not create a "rising" topic
        *[
            mk(
                publication_date="2026-06-01",
                primary_topic=TOPIC_ECON,
                topics=[TOPIC_ECON],
                primary_location={"source": None},
            )
            for _ in range(50)
        ],
    ]
    r = rising_stats(works, START, END)
    assert (r["works_previous"], r["works_recent"]) == (40, 60)
    rising = {x["name"]: x for x in r["topics"]["rising"]}
    declining = {x["name"]: x for x in r["topics"]["declining"]}
    assert "Computer Vision Methods" in rising and rising["Computer Vision Methods"]["recent"] == 40
    assert "Labor Markets" in declining and declining["Labor Markets"]["recent"] == 20


def test_rising_respects_minimum_support() -> None:
    works = [mk(primary_location=venue("J")) for _ in range(5)]  # far below MIN_TOPIC_WORKS
    assert rising_stats(works, START, END)["topics"] == {"rising": [], "declining": []}


def test_venues_split_repositories_and_drop_platforms() -> None:
    works = [
        mk(primary_location=venue("Nature", "journal", "S1")),
        mk(primary_location=venue("Nature", "journal", "S1")),
        mk(primary_location=venue("Physical review. D/Physical review. D.", "journal", "S2")),
        mk(primary_location=venue("arXiv", "repository", "S3")),
        mk(primary_location=venue("Springer eBooks", "ebook platform", "S4")),
        mk(primary_location={"source": None}),
    ]
    v = venue_stats(works)
    assert [x["name"] for x in v["top_published"]] == ["Nature", "Physical review. D"]
    assert [x["name"] for x in v["top_repositories"]] == ["arXiv"]
    assert v["works_on_platforms"] == 1 and v["works_with_venue"] == 5
    assert clean_venue_name("A/B") == "A/B" and clean_venue_name(" X ") == "X"


def test_sdg_and_funder_counts() -> None:
    def sdg(i: int) -> dict[str, Any]:
        return {"id": f"https://metadata.un.org/sdg/{i}", "display_name": f"G{i}", "score": 0.8}

    works = [
        mk(
            sustainable_development_goals=[sdg(3), sdg(13)],
            funders=[{"id": "https://openalex.org/F1", "display_name": "NSF"}],
        ),
        mk(sustainable_development_goals=[sdg(3)], funders=[]),
        mk(sustainable_development_goals=[]),
    ]
    s = sdg_stats(works)
    assert s["works_with_sdg"] == 2 and [(g["id"], g["works"]) for g in s["goals"]] == [
        (3, 2),
        (13, 1),
    ]
    f = funder_stats(works)
    assert f["works_with_funder"] == 1 and f["top"] == [{"name": "NSF", "works": 1}]


def _collab_works() -> list[NormalizedWork]:
    internal = [  # Woese is always co-listed with UIUC on the same authorship
        mk(authorships=[ship("A1", [UIUC_INST, WOESE]), ship("A2", [MIT])]) for _ in range(20)
    ]
    # ZJU: joint appointments on 11 of 15 works (0.73) -> a real partner, must stay
    zju = [
        mk(authorships=[ship("A1", [UIUC_INST, ZJU] if i < 11 else [UIUC_INST]), ship("A3", [ZJU])])
        for i in range(15)
    ]
    return [*internal, *zju]


def test_co_uiuc_signal_recorded_per_work() -> None:
    w = mk(authorships=[ship("A1", [UIUC_INST, WOESE]), ship("A2", [MIT])])
    assert w.co_uiuc_institution_ids == {"I1001"}  # MIT shares no authorship with UIUC


def test_infer_uiuc_units_excludes_internal_keeps_partners_and_joint_institutes() -> None:
    works = _collab_works()
    excluded, report = infer_uiuc_units(works, {"I157725225"})
    assert "I1001" in excluded and "I1002" not in excluded and "I63966007" not in excluded
    assert {r["name"]: r["reason"] for r in report}["Woese Institute"] == "inferred"
    # too few works to infer anything
    excluded_small, _ = infer_uiuc_units(works[:5], {"I157725225"})
    assert "I1001" not in excluded_small


def test_collaboration_stats_counts_external_institutions_and_countries() -> None:
    c = collaboration_stats(_collab_works(), {"I157725225"})
    names = [i["name"] for i in c["institutions"]]
    assert "Woese Institute" not in names and "University of Illinois Urbana-Champaign" not in names
    top = {i["name"]: i["works"] for i in c["institutions"]}
    assert top["Zhejiang University"] == 15
    assert c["works_with_foreign_institution"] == 15  # ZJU works (CN); MIT is US
    assert c["share_international"] == pytest.approx(15 / 35, abs=1e-4)
    assert {x["code"] for x in c["countries"]} >= {"US", "CN"}


def test_overview_numbers() -> None:
    works = [
        mk(cited_by_count=10, fwci=2.0, open_access={"is_oa": True, "oa_status": "gold"}),
        mk(cited_by_count=0, fwci=None, open_access={"is_oa": False, "oa_status": "closed"}),
        mk(
            cited_by_count=2,
            fwci=0.5,
            open_access={"is_oa": True, "oa_status": "green"},
            citation_normalized_percentile={
                "value": 0.99,
                "is_in_top_1_percent": True,
                "is_in_top_10_percent": True,
            },
        ),
    ]
    o = overview_stats(
        works,
        start=START,
        end=END,
        indexed_works=2,
        faculty={"directory": 9, "matched": 7, "with_works": 6},
    )
    assert (o["works"], o["indexed_works"], o["citations_total"]) == (3, 2, 12)
    assert o["citations_median"] == 2 and o["works_cited"] == 2
    assert o["open_access_works"] == 2 and o["open_access_share"] == pytest.approx(0.6667, abs=1e-4)
    assert o["oa_status"] == {"gold": 1, "closed": 1, "green": 1}
    assert o["fwci_median"] == 1.25 and o["fwci_available_share"] == pytest.approx(0.6667, abs=1e-4)
    assert o["top_1_percent_works"] == 1
    assert (o["faculty_directory"], o["faculty_matched"], o["faculty_with_works"]) == (9, 7, 6)
    assert o["uiuc_authors"] == 1  # A1 (UIUC) appears on every work


def test_compute_stats_returns_every_key_and_is_json_serialisable() -> None:
    import json

    stats = compute_stats(
        _collab_works(),
        start=START,
        end=END,
        uiuc_unit_ids={"I157725225"},
        indexed_works=10,
        faculty={},
    )
    assert set(stats) == {
        "overview",
        "output",
        "fields",
        "rising_topics",
        "venues",
        "sdgs",
        "funders",
        "collaborators",
    }
    json.dumps(stats, default=str)
