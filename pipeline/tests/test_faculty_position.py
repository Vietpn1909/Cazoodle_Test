import pytest

from pipeline.faculty.position import classify_position


@pytest.mark.parametrize(
    ("position", "rank", "appointment", "emeritus"),
    [
        ("Professor", "Professor", "regular", False),
        ("Associate Professor Emerita", "Associate Professor", "regular", True),
        ("Research Associate Professor", "Associate Professor", "research", False),
        ("Adjunct Associate Professor", "Associate Professor", "adjunct", False),
        ("ADJ RES PROF", "Professor", "adjunct", False),
        ("Clinical Assistant Professor", "Assistant Professor", "clinical", False),
        ("Teaching Associate Professor", "Associate Professor", "teaching", False),
        ("Professor Emeritus of Chemistry", "Professor", "regular", True),
        ("Grainger Distinguished Chair in Engineering", "Professor", "regular", False),
        ("Head and Gregorio Weber Chair", "Professor", "regular", False),
        (
            "Swanlund Endowed Chair Emeritus and CAS Professor Emeritus",
            "Professor",
            "regular",
            True,
        ),
        ("Lecturer", "Lecturer", "teaching", False),
        ("Department Affiliate", "Other", "adjunct", False),
        ("Dean, College of Engineering", "Other", "regular", False),
        (None, "Other", "regular", False),
    ],
)
def test_classify_position(
    position: str | None, rank: str, appointment: str, emeritus: bool
) -> None:
    p = classify_position(position)
    assert (p.rank, p.appointment, p.is_emeritus) == (rank, appointment, emeritus)
