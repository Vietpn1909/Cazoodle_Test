"""Fetchers for the public sources of institutional metadata (all cached, all open licences).

- Wikipedia REST summary  -> description text (CC BY-SA), coordinates, Wikidata id
- Wikidata entity         -> inception year, motto, website, short name (CC0)
- ROR registry            -> canonical name, acronym, establishment year, city (CC0)
- Wikimedia Commons       -> campus photographs with author / licence metadata
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pipeline.institution.web import PoliteJsonClient, cached_get

WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKIDATA_ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
ROR_API = "https://api.ror.org/v2/organizations/"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
IMAGE_WIDTH = 1600  # thumbnail width we link to (Commons serves it on demand)

# One search per landmark: each contributes at most one photo, so the gallery is varied.
CAMPUS_QUERIES = (
    "Illini Union Main Quad University of Illinois",
    "Alma Mater statue University of Illinois Urbana-Champaign",
    "Altgeld Hall University of Illinois",
    "Beckman Institute University of Illinois",
    "Memorial Stadium Champaign Illinois",
    "Grainger Engineering Library Urbana",
    "Foellinger Auditorium Main Quad",
    "Siebel Center for Computer Science Urbana",
    "Engineering Quad University of Illinois Urbana",
    "University of Illinois Urbana-Champaign campus aerial",
)


def fetch_wikipedia_summary(
    client: PoliteJsonClient, cache_dir: Path, title: str, *, refresh: bool = False
) -> dict[str, Any]:
    url = WIKIPEDIA_SUMMARY + urllib.parse.quote(title.replace(" ", "_"), safe="")
    data: dict[str, Any] = cached_get(client, cache_dir, "wikipedia", url, refresh=refresh)
    return data


def fetch_wikidata_entity(
    client: PoliteJsonClient, cache_dir: Path, qid: str, *, refresh: bool = False
) -> dict[str, Any]:
    data = cached_get(
        client, cache_dir, "wikidata", WIKIDATA_ENTITY.format(qid=qid), refresh=refresh
    )
    entity: dict[str, Any] = data["entities"][qid]
    return entity


def fetch_ror(
    client: PoliteJsonClient, cache_dir: Path, ror_id: str, *, refresh: bool = False
) -> dict[str, Any]:
    short = ror_id.removeprefix("https://ror.org/")
    data: dict[str, Any] = cached_get(client, cache_dir, "ror", ROR_API + short, refresh=refresh)
    return data


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def search_commons_files(
    client: PoliteJsonClient, cache_dir: Path, query: str, *, limit: int = 15, refresh: bool = False
) -> list[str]:
    """File titles (``File:...``) matching ``query``, raster images only."""
    data = cached_get(
        client,
        cache_dir,
        "commons-search",
        COMMONS_API,
        {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": query,
            "srnamespace": 6,
            "srlimit": limit,
        },
        refresh=refresh,
    )
    titles = [hit["title"] for hit in data["query"]["search"]]
    return [t for t in titles if t.lower().endswith(IMAGE_EXTENSIONS)]


def commons_image_info(
    client: PoliteJsonClient,
    cache_dir: Path,
    titles: Sequence[str],
    *,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    """Size, thumbnail URL, author and licence for each file (flat dicts)."""
    if not titles:
        return []
    data = cached_get(
        client,
        cache_dir,
        "commons-info",
        COMMONS_API,
        {
            "action": "query",
            "format": "json",
            "prop": "imageinfo",
            "titles": "|".join(sorted(titles)),
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": IMAGE_WIDTH,
            "iiextmetadatafilter": (
                "LicenseShortName|LicenseUrl|Artist|Credit|ImageDescription|DateTimeOriginal"
            ),
        },
        refresh=refresh,
    )
    out: list[dict[str, Any]] = []
    for page in data.get("query", {}).get("pages", {}).values():
        info = (page.get("imageinfo") or [None])[0]
        if not info:
            continue
        meta = {k: _strip_html(v.get("value", "")) for k, v in info.get("extmetadata", {}).items()}
        out.append(
            {
                "title": page["title"],
                "width": info["width"],
                "height": info["height"],
                "mime": info["mime"],
                "thumb_url": info.get("thumburl"),
                # Commons rounds the requested width to a standard size (1600 -> 1920)
                "thumb_width": info.get("thumbwidth"),
                "thumb_height": info.get("thumbheight"),
                "source_url": info["descriptionurl"],
                "author": meta.get("Artist") or None,
                "credit": meta.get("Credit") or None,
                "license": meta.get("LicenseShortName") or None,
                "license_url": meta.get("LicenseUrl") or None,
                "description": meta.get("ImageDescription") or None,
                "date": meta.get("DateTimeOriginal") or None,
            }
        )
    return out


# --------------------------------------------------------------------------- curation (pure)

_FREE_LICENSE = re.compile(r"^(CC BY(-SA)?\b|CC0|Public domain|PD\b)", re.IGNORECASE)
# Not campus views: logos/maps/signs, and close-ups of plaques (one such "dedication" photo of a
# bronze plaque was the largest "Memorial Stadium" result and looked wrong in the gallery).
_UNWANTED_TITLE = re.compile(
    r"\b(logo|seal|flag|map|diagram|chart|poster|screenshot|stamp|coat of arms|sign|"
    r"plaque|dedication|marker|inscription)\b",
    re.IGNORECASE,
)
HERO_RATIO = (1.25, 3.2)  # the hero banner may be a wide panorama


def wikidata_image_title(entity: dict[str, Any]) -> str | None:
    """Commons file the Wikidata community chose as the item's main image (P18)."""
    for claim in entity.get("claims", {}).get("P18", []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(value, str) and value:
            return "File:" + value.replace("_", " ")
    return None


def is_free_license(license_name: str | None) -> bool:
    """Reusable with attribution: CC BY / CC BY-SA / CC0 / public domain (no NC/ND)."""
    if not license_name:
        return False
    name = license_name.strip()
    if re.search(r"\b(NC|ND)\b", name, re.IGNORECASE):
        return False
    return bool(_FREE_LICENSE.match(name))


def acceptable_image(
    info: dict[str, Any], *, min_width: int = IMAGE_WIDTH, ratio: tuple[float, float] = (1.25, 2.4)
) -> bool:
    if info.get("width", 0) < min_width or not info.get("height") or not info.get("thumb_url"):
        return False
    aspect = info["width"] / info["height"]
    return (
        ratio[0] <= aspect <= ratio[1]
        and is_free_license(info.get("license"))
        and not _UNWANTED_TITLE.search(info["title"])
        and info.get("mime") in {"image/jpeg", "image/png", "image/webp"}
        and bool(info.get("author"))  # attribution requires a named author/credit
    )


def pick_images(
    per_query: Sequence[Sequence[dict[str, Any]]], *, max_images: int = 6
) -> list[dict[str, Any]]:
    """Best acceptable photo per landmark query (largest first), no duplicates."""
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()
    for infos in per_query:
        candidates = sorted(
            (i for i in infos if acceptable_image(i) and i["title"] not in seen),
            key=lambda i: -i["width"] * i["height"],
        )
        if candidates:
            chosen.append(candidates[0])
            seen.add(candidates[0]["title"])
        if len(chosen) >= max_images:
            break
    return chosen


def select_gallery(
    client: PoliteJsonClient,
    cache_dir: Path,
    wikidata: dict[str, Any],
    gallery: Sequence[dict[str, Any]],
    *,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    """Hero image (Wikidata's main image, wide ratio allowed) followed by the landmark photos."""
    images: list[dict[str, Any]] = []
    hero_title = wikidata_image_title(wikidata)
    if hero_title:
        for info in commons_image_info(client, cache_dir, [hero_title], refresh=refresh):
            if acceptable_image(info, ratio=HERO_RATIO):
                images.append({**info, "role": "hero"})
    taken = {i["title"] for i in images}
    images.extend({**g, "role": "gallery"} for g in gallery if g["title"] not in taken)
    return images
