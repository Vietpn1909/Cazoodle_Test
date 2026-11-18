"""Assemble the institution profile from the individual sources (pure, no I/O).

Principle: only facts that a source states unambiguously. Numbers the sources disagree on
(Wikidata lists 42,606 and 83,119 students) are not shown as structured data; the Wikipedia
description (which says "over 59,000 students") is quoted with attribution instead.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

WIKIPEDIA_LICENSE = "CC BY-SA 4.0"
WIKIPEDIA_LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"


def short_openalex_id(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def wikidata_year(entity: dict[str, Any], prop: str) -> int | None:
    """First point-in-time claim of ``prop`` as a year (``+1867-00-00T00:00:00Z`` -> 1867)."""
    for claim in entity.get("claims", {}).get(prop, []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        match = (
            re.match(r"^[+-](\d{1,4})-", value.get("time", "")) if isinstance(value, dict) else None
        )
        if match:
            return int(match.group(1))
    return None


def wikidata_text(entity: dict[str, Any], prop: str, lang: str = "en") -> str | None:
    for claim in entity.get("claims", {}).get(prop, []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(value, dict) and value.get("language") == lang and value.get("text"):
            return str(value["text"])
    return None


def ror_value(ror: dict[str, Any], name_type: str) -> str | None:
    for n in ror.get("names", []):
        if name_type in n.get("types", []):
            return str(n["value"])
    return None


def ror_link(ror: dict[str, Any], link_type: str) -> str | None:
    for link in ror.get("links", []):
        if link.get("type") == link_type:
            return str(link["value"])
    return None


def thumb_size(info: dict[str, Any]) -> tuple[int, int]:
    """Real size of the thumbnail the URL serves.

    The API reports the *requested* width (1600) although Commons serves the next standard
    size (``.../1920px-Name.jpg``); the URL is the source of truth, height follows the
    original aspect ratio.
    """
    match = re.search(r"/(\d+)px-[^/]+$", info["thumb_url"])
    width = int(match.group(1)) if match else int(info["thumb_width"])
    return width, round(width * info["height"] / info["width"])


def image_record(info: dict[str, Any]) -> dict[str, Any]:
    license_name = info["license"]
    width, height = thumb_size(info)
    return {
        "role": info.get("role", "gallery"),
        "title": re.sub(r"^File:", "", info["title"]).rsplit(".", 1)[0].replace("_", " "),
        "url": info["thumb_url"],
        "width": width,
        "height": height,
        "original_width": info["width"],
        "original_height": info["height"],
        "author": info["author"],
        "license": license_name,
        "license_url": info.get("license_url"),
        "source_url": info["source_url"],
        "attribution": f"Photo: {info['author']}, {license_name}, via Wikimedia Commons",
        "description": info.get("description"),
    }


def build_institution(
    *,
    openalex: dict[str, Any],
    ror: dict[str, Any],
    wikipedia: dict[str, Any],
    wikidata: dict[str, Any],
    images: Sequence[dict[str, Any]],
    retrieved_at: str,
) -> dict[str, Any]:
    wiki_coords = wikipedia.get("coordinates") or {}
    geo_oa = openalex.get("geo") or {}
    ror_location = (ror.get("locations") or [{}])[0].get("geonames_details", {})
    founded_wd = wikidata_year(wikidata, "P571")
    founded_ror = ror.get("established")
    qid = wikipedia.get("wikibase_item")
    wiki_url = (wikipedia.get("content_urls") or {}).get("desktop", {}).get("page")

    city = geo_oa.get("city") or ror_location.get("name")
    region = geo_oa.get("region") or ror_location.get("country_subdivision_name")
    country = geo_oa.get("country") or ror_location.get("country_name")

    geo = {
        "city": city,
        "region": region,
        "country": country,
        "country_code": geo_oa.get("country_code") or ror_location.get("country_code"),
        "label": ", ".join(p for p in (city, region, country) if p),
        # campus centre (Wikipedia) for the map pin; city-level point from OpenAlex/ROR
        "campus": {"lat": wiki_coords.get("lat"), "lon": wiki_coords.get("lon")},
        "city_point": {"lat": geo_oa.get("latitude"), "lon": geo_oa.get("longitude")},
        # the en dash is the proper typography of the metro-area name
        "metro": "Champaign–Urbana" if "Champaign" in (wikipedia.get("extract") or "") else None,  # noqa: RUF001
    }

    facts = {
        "short_name": ror_value(ror, "acronym")
        or (openalex.get("display_name_acronyms") or [None])[0],
        "tagline": wikipedia.get("description"),
        "description": wikipedia.get("extract"),
        "founded": founded_wd or founded_ror,
        # independent sources agree on the founding year -> safe to show
        "founded_verified": bool(founded_wd and founded_ror and founded_wd == founded_ror),
        "motto": wikidata_text(wikidata, "P1451"),
        "website": ror_link(ror, "website") or openalex.get("homepage_url"),
        "wikipedia_url": wiki_url,
        "wikidata_id": qid,
        "ror": ror.get("id"),
        "openalex": {
            "works_count": openalex.get("works_count"),
            "cited_by_count": openalex.get("cited_by_count"),
            "h_index": (openalex.get("summary_stats") or {}).get("h_index"),
            "i10_index": (openalex.get("summary_stats") or {}).get("i10_index"),
            "note": "all-time totals in OpenAlex (not limited to the 2-year window)",
        },
    }

    sources = [
        {
            "name": "OpenAlex",
            "url": openalex["id"],
            "license": "CC0 1.0",
            "used_for": "all-time publication/citation totals, city-level coordinates",
            "retrieved_at": retrieved_at,
        },
        {
            "name": "ROR (Research Organization Registry)",
            "url": ror.get("id"),
            "license": "CC0 1.0",
            "used_for": "acronym, website, founding year cross-check",
            "retrieved_at": retrieved_at,
        },
        {
            "name": "Wikipedia",
            "url": wiki_url,
            "license": WIKIPEDIA_LICENSE,
            "license_url": WIKIPEDIA_LICENSE_URL,
            "used_for": "description text, campus coordinates",
            "retrieved_at": retrieved_at,
        },
        {
            "name": "Wikidata",
            "url": f"https://www.wikidata.org/wiki/{qid}" if qid else None,
            "license": "CC0 1.0",
            "used_for": "founding year, motto",
            "retrieved_at": retrieved_at,
        },
        {
            "name": "Wikimedia Commons",
            "url": "https://commons.wikimedia.org/",
            "license": "per file (see each image)",
            "used_for": "campus photographs",
            "retrieved_at": retrieved_at,
        },
    ]
    return {
        "id": short_openalex_id(openalex["id"]),
        "ror": ror.get("id") or openalex.get("ror"),
        "name": openalex["display_name"],
        "geo": geo,
        "images": [image_record(i) for i in images],
        "facts": facts,
        "sources": sources,
    }
