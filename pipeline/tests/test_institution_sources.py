import json
from pathlib import Path
from typing import Any

import pytest
from test_institution_web import Clock, FakeOpener, client, ok

from pipeline.institution.sources import (
    HERO_RATIO,
    acceptable_image,
    commons_image_info,
    is_free_license,
    pick_images,
    search_commons_files,
    select_gallery,
    wikidata_image_title,
)


def img(title: str = "File:Main Quad.jpg", **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": title,
        "width": 4000,
        "height": 3000,
        "mime": "image/jpeg",
        "thumb_url": "https://upload.wikimedia.org/x/1920px-Main_Quad.jpg",
        "source_url": "https://commons.wikimedia.org/wiki/File:Main_Quad.jpg",
        "author": "Jane Doe",
        "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
    }
    base.update(kw)
    return base


@pytest.mark.parametrize(
    ("name", "free"),
    [
        ("CC BY 2.0", True),
        ("CC BY-SA 4.0", True),
        ("CC0", True),
        ("Public domain", True),
        ("CC BY-NC 2.0", False),
        ("CC BY-ND 4.0", False),
        ("CC BY-NC-SA 3.0", False),
        ("All rights reserved", False),
        ("Fair use", False),
        (None, False),
    ],
)
def test_free_license(name: str | None, free: bool) -> None:
    assert is_free_license(name) is free


@pytest.mark.parametrize(
    ("change", "ok_"),
    [
        ({}, True),
        ({"width": 1200, "height": 900}, False),  # too small
        ({"width": 1600, "height": 1600}, False),  # square
        ({"width": 9996, "height": 3528}, False),  # panorama is not allowed in the gallery ...
        ({"license": "CC BY-NC 4.0"}, False),
        ({"author": None}, False),  # attribution needs a named author
        ({"mime": "image/svg+xml"}, False),
        ({"title": "File:University seal.png"}, False),
        ({"title": "File:Memorial Stadium Dedication plaque.jpg"}, False),
        ({"thumb_url": None}, False),
    ],
)
def test_acceptable_image(change: dict[str, Any], ok_: bool) -> None:
    assert acceptable_image(img(**change)) is ok_


def test_panorama_is_acceptable_for_the_hero_only() -> None:
    pano = img(width=9996, height=3528)
    assert not acceptable_image(pano)
    assert acceptable_image(pano, ratio=HERO_RATIO)


def test_pick_images_one_best_per_query_without_duplicates() -> None:
    small, big = img("File:A small.jpg", width=2000, height=1500), img("File:A big.jpg")
    per_query = [
        [small, big, img("File:Bad logo.jpg")],  # best acceptable = the biggest
        [big],  # already chosen by the first query -> contributes nothing
        [],  # nothing found
        [img("File:Other view.jpg", width=3000, height=2000)],
    ]
    assert [i["title"] for i in pick_images(per_query)] == ["File:A big.jpg", "File:Other view.jpg"]
    assert len(pick_images(per_query, max_images=1)) == 1


def test_wikidata_image_title() -> None:
    entity = {"claims": {"P18": [{"mainsnak": {"datavalue": {"value": "UIUC_Illini Union.jpg"}}}]}}
    assert wikidata_image_title(entity) == "File:UIUC Illini Union.jpg"
    assert wikidata_image_title({"claims": {}}) is None


COMMONS_INFO = {
    "query": {
        "pages": {
            "1": {
                "title": "File:Altgeld Hall.jpg",
                "imageinfo": [
                    {
                        "width": 4020,
                        "height": 3099,
                        "mime": "image/jpeg",
                        "thumburl": "https://upload.wikimedia.org/a/1920px-Altgeld_Hall.jpg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Altgeld_Hall.jpg",
                        "extmetadata": {
                            "Artist": {"value": '<a href="//x">Daniel Schwen</a>'},
                            "LicenseShortName": {"value": "CC BY-SA 4.0"},
                            "LicenseUrl": {
                                "value": "https://creativecommons.org/licenses/by-sa/4.0"
                            },
                            "ImageDescription": {"value": "Altgeld   <b>Hall</b>"},
                        },
                    }
                ],
            },
            "-1": {"title": "File:Missing.jpg", "missing": ""},
        }
    }
}


def test_commons_image_info_flattens_and_strips_html(tmp_path: Path) -> None:
    c = client(FakeOpener(ok(COMMONS_INFO)), Clock())
    (info,) = commons_image_info(c, tmp_path, ["File:Altgeld Hall.jpg", "File:Missing.jpg"])
    assert info["author"] == "Daniel Schwen" and info["description"] == "Altgeld Hall"
    assert (info["width"], info["license"]) == (4020, "CC BY-SA 4.0")
    assert info["thumb_url"].endswith("1920px-Altgeld_Hall.jpg")
    assert commons_image_info(c, tmp_path, []) == []


def test_search_keeps_only_raster_images(tmp_path: Path) -> None:
    hits = {
        "query": {
            "search": [
                {"title": t} for t in ("File:A.jpg", "File:B.SVG", "File:C.png", "File:D.pdf")
            ]
        }
    }
    c = client(FakeOpener(ok(hits)), Clock())
    assert search_commons_files(c, tmp_path, "q") == ["File:A.jpg", "File:C.png"]


def test_select_gallery_puts_hero_first_and_skips_duplicates(tmp_path: Path) -> None:
    hero_info = json.loads(json.dumps(COMMONS_INFO))
    pages = hero_info["query"]["pages"]
    pages["1"]["title"] = "File:Illini Union.jpg"
    pages["1"]["imageinfo"][0].update(width=9996, height=3528)
    c = client(FakeOpener(ok(hero_info)), Clock())
    wikidata = {"claims": {"P18": [{"mainsnak": {"datavalue": {"value": "Illini Union.jpg"}}}]}}
    gallery = [img("File:Illini Union.jpg"), img("File:Altgeld.jpg")]  # first duplicates the hero
    out = select_gallery(c, tmp_path, wikidata, gallery)
    assert [(i["title"], i["role"]) for i in out] == [
        ("File:Illini Union.jpg", "hero"),
        ("File:Altgeld.jpg", "gallery"),
    ]
