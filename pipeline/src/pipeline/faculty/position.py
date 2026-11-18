"""Derive normalized rank / appointment / emeritus flags from free-text ``position``.

Positions are long and multi-titled ("Swanlund Endowed Chair Emeritus and CAS Professor
Emeritus of ...", "Research Associate Professor", "ADJ RES PROF"). The highest academic
rank mentioned wins; named chairs count as full professor; appointment type comes from
qualifiers (adjunct/affiliate, clinical, research, teaching).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_RANKS = (  # (label, pattern) ordered from highest to lowest
    (
        "Professor",
        re.compile(
            # full professor title not preceded by assistant/associate ...
            r"(?<!assistant )(?<!associate )\b(professor|prof)\b"
            # ... or a named/endowed chair ("Grainger Distinguished Chair", "Copp Chair in CS")
            r"|\b(endowed|distinguished|named) chair\b|\bchair (in|of)\b|\b\w+ chair\b(?=,|$| and)"
        ),
    ),
    ("Associate Professor", re.compile(r"\bassociate (professor|prof)\b")),
    ("Assistant Professor", re.compile(r"\bassistant (professor|prof)\b")),
    ("Lecturer", re.compile(r"\b(lecturer|instructor|teaching associate)\b")),
    ("Researcher", re.compile(r"\b(research (scientist|associate|fellow)|scientist)\b")),
)


@dataclass(frozen=True)
class Position:
    rank: str  # Professor | Associate/Assistant Professor | Lecturer | Researcher | Other
    appointment: str  # regular | research | clinical | teaching | adjunct
    is_emeritus: bool


def _normalize(position: str) -> str:
    p = position.lower()
    p = re.sub(r"\badj\b", "adjunct", p)
    p = re.sub(r"\bres\b", "research", p)
    return re.sub(r"\s+", " ", p).strip()


def classify_position(position: str | None) -> Position:
    if not position:
        return Position(rank="Other", appointment="regular", is_emeritus=False)
    p = _normalize(position)
    rank = next((label for label, pattern in _RANKS if pattern.search(p)), "Other")

    if re.search(r"\b(adjunct|affiliate)\b", p):
        appointment = "adjunct"
    elif re.search(r"\bclinical\b", p):
        appointment = "clinical"
    elif rank == "Researcher" or re.search(
        r"\bresearch (professor|prof|associate professor|assistant professor)\b", p
    ):
        appointment = "research"
    elif rank == "Lecturer" or re.search(r"\bteaching (professor|associate|assistant)\b", p):
        appointment = "teaching"
    else:
        appointment = "regular"
    return Position(rank=rank, appointment=appointment, is_emeritus="emerit" in p)
