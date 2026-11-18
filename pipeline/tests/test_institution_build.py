from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db.models import Institution
from pipeline.institution.__main__ import store_institution
from pipeline.institution.build import (
    build_institution,
    image_record,
    ror_link,
    ror_value,
    short_openalex_id,
    thumb_size,
    wikidata_text,
    wikidata_year,
)

OPENALEX = {
    "id": "https://openalex.org/I157725225",
    "ror": "https://ror.org/047426m28",
    "display_name": "University of Illinois Urbana-Champaign",
    "display_name_acronyms": ["UIUC"],
    "homepage_url": "https://illinois.edu",
    "works_count": 354645,
    "cited_by_count": 41472395,
    "summary_stats": {"h_index": 1627, "i10_index": 429231},
    "geo": {
        "city": "Urbana",
        "region": "Illinois",
        "country": "United States",
        "country_code": "US",
        "latitude": 40.11059,
        "longitude": -88.20727,
    },
}
ROR = {
    "id": "https://ror.org/047426m28",
    "established": 1867,
    "names": [
        {"value": "University of Illinois Urbana-Champaign", "types": ["ror_display", "label"]},
        {"value": "UIUC", "types": ["acronym"]},
    ],
    "links": [
        {"type": "website", "value": "https://illinois.edu"},
        {"type": "wikipedia", "value": "http://en.wikipedia.org/wiki/X"},
    ],
    "locations": [{"geonames_details": {"name": "Urbana", "country_code": "US"}}],
}
WIKIPEDIA = {
    "wikibase_item": "Q457281",
    "description": "Public university in Illinois, US",
    "extract": "A public land-grant research university in the Champaign–Urbana area.",
    "coordinates": {"lat": 40.1105, "lon": -88.2283},
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/UIUC"}},
}
WIKIDATA = {
    "claims": {
        "P571": [{"mainsnak": {"datavalue": {"value": {"time": "+1867-00-00T00:00:00Z"}}}}],
        "P1451": [
            {"mainsnak": {"datavalue": {"value": {"text": "Apprendre", "language": "fr"}}}},
            {
                "mainsnak": {
                    "datavalue": {"value": {"text": "Learning and Labor", "language": "en"}}
                }
            },
        ],
        # conflicting enrolment figures: must NOT end up as structured facts
        "P2196": [
            {"mainsnak": {"datavalue": {"value": {"amount": "+42606"}}}},
            {"mainsnak": {"datavalue": {"value": {"amount": "+83119"}}}},
        ],
    }
}
INFO = {
    "title": "File:Alma Mater, Lorado Taft.jpg",
    "width": 3600,
    "height": 2400,
    "thumb_url": "https://thumb.wikimedia.org/a/b/1920px-Alma_Mater%2C_Lorado_Taft.jpg?utm=1",
    "thumb_width": 1600,
    "source_url": "https://commons.wikimedia.org/wiki/File:Alma.jpg",
    "author": "Daniel Schwen",
    "license": "Public domain",
    "license_url": None,
    "description": "Alma Mater",
}


def test_small_helpers() -> None:
    assert short_openalex_id("https://openalex.org/I157725225") == "I157725225"
    assert wikidata_year(WIKIDATA, "P571") == 1867 and wikidata_year(WIKIDATA, "P999") is None
    assert wikidata_text(WIKIDATA, "P1451") == "Learning and Labor"  # picks the English text
    assert (
        ror_value(ROR, "acronym") == "UIUC" and ror_link(ROR, "website") == "https://illinois.edu"
    )


def test_thumb_size_uses_the_url_not_the_requested_width() -> None:
    # API said 1600 but the URL serves 1920px; height follows the original aspect ratio
    assert thumb_size(INFO) == (1920, 1280)
    assert thumb_size({**INFO, "thumb_url": "https://x/y.jpg", "thumb_width": 800}) == (800, 533)


def test_image_record_carries_attribution() -> None:
    rec = image_record({**INFO, "role": "hero"})
    assert rec["title"] == "Alma Mater, Lorado Taft" and rec["role"] == "hero"
    assert (rec["width"], rec["height"]) == (1920, 1280)
    assert rec["attribution"] == "Photo: Daniel Schwen, Public domain, via Wikimedia Commons"
    assert rec["source_url"].startswith("https://commons.wikimedia.org/")


def build(**kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "openalex": OPENALEX,
        "ror": ROR,
        "wikipedia": WIKIPEDIA,
        "wikidata": WIKIDATA,
        "images": [INFO],
        "retrieved_at": "2026-11-18T00:00:00+00:00",
    }
    args.update(kw)
    return build_institution(**args)


def test_build_institution_combines_sources() -> None:
    r = build()
    assert (r["id"], r["ror"]) == ("I157725225", "https://ror.org/047426m28")
    assert r["geo"]["label"] == "Urbana, Illinois, United States"
    assert r["geo"]["campus"] == {"lat": 40.1105, "lon": -88.2283}
    assert r["geo"]["city_point"] == {"lat": 40.11059, "lon": -88.20727}
    f = r["facts"]
    assert (f["founded"], f["founded_verified"], f["motto"]) == (1867, True, "Learning and Labor")
    assert (f["short_name"], f["website"], f["wikidata_id"]) == (
        "UIUC",
        "https://illinois.edu",
        "Q457281",
    )
    assert f["openalex"]["works_count"] == 354645 and f["openalex"]["h_index"] == 1627
    # Wikidata lists conflicting enrolment figures (42,606 / 83,119): not shown as structured data
    assert not any(k in f for k in ("students", "enrollment", "enrolment"))
    assert "42606" not in str(r) and "83119" not in str(r)
    assert {s["name"] for s in r["sources"]} >= {
        "OpenAlex",
        "Wikipedia",
        "Wikidata",
        "ROR (Research Organization Registry)",
    }
    wiki = next(s for s in r["sources"] if s["name"] == "Wikipedia")
    assert wiki["license"] == "CC BY-SA 4.0" and wiki["url"].endswith("/UIUC")
    assert all(s["retrieved_at"] == "2026-11-18T00:00:00+00:00" for s in r["sources"])
    assert len(r["images"]) == 1


def test_founding_year_flagged_when_sources_disagree() -> None:
    r = build(ror={**ROR, "established": 1868})
    assert r["facts"]["founded"] == 1867 and r["facts"]["founded_verified"] is False


def test_build_tolerates_missing_optional_data() -> None:
    r = build(wikipedia={"wikibase_item": None}, wikidata={}, ror={"id": None}, images=[])
    assert r["facts"]["motto"] is None and r["facts"]["founded"] is None
    assert r["geo"]["campus"] == {"lat": None, "lon": None} and r["images"] == []


@pytest.mark.db
def test_store_institution_inserts_then_replaces(db_session: Session) -> None:
    store_institution(db_session, build())
    db_session.commit()
    store_institution(db_session, {**build(), "name": "Renamed"})
    db_session.commit()
    db_session.expire_all()
    row = db_session.get(Institution, "I157725225")
    assert row is not None and row.name == "Renamed"
    assert row.facts["founded"] == 1867 and row.images[0]["license"] == "Public domain"
    assert pytest.approx(row.geo["campus"]["lat"]) == 40.1105
