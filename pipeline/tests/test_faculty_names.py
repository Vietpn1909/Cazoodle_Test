import pytest

from pipeline.faculty.names import (
    given_forms,
    index_keys,
    name_score,
    name_tokens,
    shares_name_token,
    surname_variants,
)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("Jiawei Han", "Jiawei Han", 1.0),
        ("Jiawei Han", "Han, Jiawei", 1.0),  # "Family, Given"
        ("Yih-Kuen Jan", "Yihkuen Jan", 0.98),  # compound given name
        ("Kenneth S Schweizer", "Kenneth S. Schweizer", 1.0),
        ("David Michael Rosch", "David M. Rosch", 0.95),  # middle initial vs full
        ("Leslie Looney", "Leslie W. Looney", 0.93),  # one side lacks middle
        ("Jiawei Han", "J. Han", 0.80),  # initial only
        ("Jim Rehg", "James M. Rehg", 0.90),  # nickname
        ("Andreas Kloeckner", "Andreas Klöckner", 0.98 * 1.0),  # umlaut transliteration
    ],
)
def test_name_score_matches(a: str, b: str, expected: float) -> None:
    assert name_score(a, b) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Michael T Heath", "Michael J Heath"),  # conflicting middle initials
        ("Bo Li", "Bin Li"),  # different given names
        ("Jiawei Han", "Jiawei Zhang"),  # different family
        ("Jiawei Han", ""),
    ],
)
def test_name_score_rejects(a: str, b: str) -> None:
    assert name_score(a, b) == 0.0


def test_reversed_order_is_discounted() -> None:
    assert name_score("Jiawei Han", "Han Jiawei") == pytest.approx(0.85 * 0.93)


def test_suffixes_dropped() -> None:
    assert name_tokens("John Smith Jr.") == ["john", "smith"]


def test_given_forms_link_nicknames() -> None:
    assert {"jim", "james"} <= given_forms("jim")
    assert "steve" in given_forms("steven")


def test_surname_variants_for_double_and_married_names() -> None:
    assert "leonardo patricio chamorro" in surname_variants("Leonardo Patricio Chamorro Chavez")
    assert "deborah levin" in surname_variants("Deborah Levin Fliflet")
    assert surname_variants("Kenneth S Schweizer") == []  # middle initial, not a surname
    assert surname_variants("Bo Li") == []


def test_shares_name_token_detects_merged_profiles() -> None:
    assert not shares_name_token(["Yuxiong Wang"], "Ziqi Pang")
    assert shares_name_token(["Jose Meseguer"], "José Meseguer B")
    assert shares_name_token(["Bertram Ludaescher"], "Bertram Ludäscher")
    assert shares_name_token(["Richard Tempest"], "Ричард Темпест")  # non-Latin: undecidable


def test_index_keys_cover_family_and_reversed_order() -> None:
    assert index_keys("Jiawei Han") == {"han", "jiawei"}
    assert index_keys("Madonna") == set()
