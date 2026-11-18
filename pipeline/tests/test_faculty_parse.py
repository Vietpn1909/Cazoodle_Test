from pathlib import Path

import pytest

from pipeline.faculty.parse import (
    ArrayParseError,
    normalize_name,
    parse_pg_array,
    read_faculty_csv,
)


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        ('{"text mining",sampling,"data, cubes"}', ["text mining", "sampling", "data, cubes"]),
        (r'{"\\cal l","say \"hi\""}', [r"\cal l", 'say "hi"']),
        ("{a,NULL,b}", ["a", "b"]),
        ("{}", []),
        ("NULL", []),
        ("", []),
    ],
)
def test_parse_pg_array(literal: str, expected: list[str]) -> None:
    assert parse_pg_array(literal) == expected


@pytest.mark.parametrize("bad", ['{"unterminated}', "not an array", '{"a"b}'])
def test_parse_pg_array_rejects_malformed(bad: str) -> None:
    with pytest.raises(ArrayParseError):
        parse_pg_array(bad)


@pytest.mark.parametrize(
    ("raw", "norm"),
    [
        ("C.-H. Christina Cheng", "c h christina cheng"),
        ("M. Burak Erdoğan", "m burak erdogan"),
        ("Khaled El‐Rayes", "khaled el rayes"),  # unicode hyphen splits, not glues
        ("Joy O’Keefe", "joy o keefe"),
        ("M. Große Perdekamp", "m grosse perdekamp"),
    ],
)
def test_normalize_name(raw: str, norm: str) -> None:
    assert normalize_name(raw) == norm


def test_read_csv_merges_duplicate_emails_and_handles_null(tmp_path: Path) -> None:
    csv_text = (
        '"email","name","photo_url","position","keywords"\n'
        '"A@illinois.edu","Chi-Hing Cheng",NULL,"Professor","{ecology,physiology}"\n'
        '"a@illinois.edu","C.-H. Christina Cheng","http://x/p.jpg",NULL,"{ecology,genetics}"\n'
        '"b@illinois.edu","Bo Li",NULL,NULL,"{}"\n'
    )
    path = tmp_path / "f.csv"
    path.write_text(csv_text, encoding="utf-8")
    recs = read_faculty_csv(path)
    assert [r.email for r in recs] == ["a@illinois.edu", "b@illinois.edu"]
    a = recs[0]
    assert a.name == "Chi-Hing Cheng"
    assert a.name_variants == ["C.-H. Christina Cheng"]
    assert a.photo_url == "http://x/p.jpg"  # filled from duplicate
    assert a.position == "Professor"
    assert a.keywords_raw == ["ecology", "physiology", "genetics"]
    assert recs[1].photo_url is None and recs[1].keywords_raw == []
