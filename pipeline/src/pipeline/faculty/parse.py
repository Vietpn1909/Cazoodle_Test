"""Parsing the provided faculty directory CSV (`illinois-researchers.csv`).

Columns: ``email, name, photo_url, position, keywords``. Missing values are the literal
string ``NULL``. ``keywords`` is a Postgres array literal, e.g.
``{"text mining",sampling,"\\\\cal l"}``.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

NULL = "NULL"


class ArrayParseError(ValueError):
    pass


def parse_pg_array(text: str) -> list[str]:
    """Parse a one-dimensional Postgres text-array literal into Python strings.

    Handles quoted elements, backslash escapes inside quotes, and unquoted elements.
    Unquoted ``NULL`` elements are dropped.
    """
    s = text.strip()
    if s in ("", NULL):
        return []
    if not (s.startswith("{") and s.endswith("}")):
        raise ArrayParseError(f"not an array literal: {s[:40]!r}")
    body = s[1:-1]
    out: list[str] = []
    i, n = 0, len(body)
    while i < n:
        if body[i] == '"':
            i += 1
            buf: list[str] = []
            while True:
                if i >= n:
                    raise ArrayParseError("unterminated quoted element")
                ch = body[i]
                if ch == "\\":
                    if i + 1 >= n:
                        raise ArrayParseError("dangling backslash")
                    buf.append(body[i + 1])
                    i += 2
                elif ch == '"':
                    i += 1
                    break
                else:
                    buf.append(ch)
                    i += 1
            out.append("".join(buf))
        else:
            j = body.find(",", i)
            j = n if j < 0 else j
            token = body[i:j].strip()
            if token and token.upper() != NULL:
                out.append(token)
            i = j
        if i < n:
            if body[i] != ",":
                raise ArrayParseError(f"expected ',' at {i}: {body[i : i + 20]!r}")
            i += 1
    return out


def _nullable(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    return None if v in ("", NULL) else v


# Letters that NFKD does not decompose into ASCII (would otherwise be dropped).
_SPECIAL_LETTERS = str.maketrans(
    {"ß": "ss", "æ": "ae", "ø": "o", "đ": "d", "ł": "l", "ı": "i"}
    | {"Æ": "AE", "Ø": "O", "Đ": "D", "Ł": "L"}
)


def fold_ascii(text: str) -> str:
    """Strip accents: ``Erdoğan`` -> ``Erdogan``, ``Große`` -> ``Grosse``."""
    t = text.translate(_SPECIAL_LETTERS)
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()


def normalize_name(name: str) -> str:
    """Lowercase, accent-free, punctuation -> space (``C.-H. Cheng`` -> ``c h cheng``).

    Non-letters become spaces *before* accent folding, so unicode hyphens/apostrophes
    (``El‐Rayes``, ``O’Keefe``) split tokens instead of silently gluing them.
    """
    t = "".join(ch if unicodedata.category(ch).startswith("L") else " " for ch in name)
    t = fold_ascii(t).lower()
    t = re.sub(r"[^a-z\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class FacultyRecord:
    email: str
    name: str
    position: str | None
    photo_url: str | None
    keywords_raw: list[str]
    name_variants: list[str] = field(default_factory=list)  # other spellings (merged rows)


def read_faculty_csv(path: Path) -> list[FacultyRecord]:
    with path.open(encoding="utf-8", newline="") as fh:
        return merge_duplicate_emails(_records(csv.DictReader(fh)))


def _records(rows: Iterable[dict[str, str]]) -> Iterable[FacultyRecord]:
    csv.field_size_limit(10**9)
    for row in rows:
        yield FacultyRecord(
            email=row["email"].strip().lower(),
            name=row["name"].strip(),
            position=_nullable(row.get("position")),
            photo_url=_nullable(row.get("photo_url")),
            keywords_raw=parse_pg_array(row.get("keywords") or ""),
        )


def merge_duplicate_emails(records: Iterable[FacultyRecord]) -> list[FacultyRecord]:
    """One record per email. Duplicates (same person, different name spellings) are merged:
    first row wins for scalar fields, other names become ``name_variants``, keywords union."""
    by_email: dict[str, FacultyRecord] = {}
    for r in records:
        existing = by_email.get(r.email)
        if existing is None:
            by_email[r.email] = r
            continue
        if r.name != existing.name and r.name not in existing.name_variants:
            existing.name_variants.append(r.name)
        existing.position = existing.position or r.position
        existing.photo_url = existing.photo_url or r.photo_url
        seen = set(existing.keywords_raw)
        existing.keywords_raw.extend(k for k in r.keywords_raw if k not in seen)
    return list(by_email.values())
