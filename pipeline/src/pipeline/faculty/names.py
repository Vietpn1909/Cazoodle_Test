"""Person-name comparison for matching directory faculty to OpenAlex authors.

Scores are heuristic but explainable (see ``name_score``):
1.00 identical · 0.98 identical ignoring spaces/hyphens (``Yih-Kuen`` vs ``Yihkuen``)
0.95 same first & last name, middle names compatible · 0.93 same first & last, one side
has no middle · 0.80 same last name, first given only as a matching initial
0.85x of the above when given/family order is reversed (``Han Jiawei``)
0.0 otherwise (incl. conflicting middle initials, ``Michael T Heath`` vs ``Michael J Heath``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from pipeline.faculty.parse import fold_ascii, normalize_name

SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "phd", "md"})

# Common English given-name nicknames -> canonical form (directory uses "Jim", OpenAlex
# "James"). Both sides are canonicalized before comparison.
_NICKNAMES: dict[str, tuple[str, ...]] = {
    "alexander": ("alex", "sasha"),
    "andrew": ("andy", "drew"),
    "anthony": ("tony",),
    "benjamin": ("ben",),
    "catherine": ("cathy", "kate", "katie"),
    "christopher": ("chris",),
    "cynthia": ("cindy",),
    "daniel": ("dan", "danny"),
    "david": ("dave",),
    "deborah": ("deb", "debbie"),
    "donald": ("don",),
    "edward": ("ed", "eddie", "ted"),
    "elizabeth": ("liz", "beth", "betsy"),
    "gregory": ("greg",),
    "jacquelyn": ("jaki", "jackie"),
    "james": ("jim", "jimmy", "jamie"),
    "jeffrey": ("jeff",),
    "jennifer": ("jen", "jenny"),
    "john": ("jack",),
    "joseph": ("joe",),
    "katherine": ("kate", "kathy", "katie"),
    "kathleen": ("kathy", "kate"),
    "kenneth": ("ken",),
    "matthew": ("matt",),
    "michael": ("mike",),
    "nicholas": ("nick",),
    "patricia": ("trish", "pat", "patty"),
    "patrick": ("pat",),
    "peter": ("pete",),
    "philip": ("phil",),
    "phillip": ("phil",),
    "rebecca": ("becky",),
    "richard": ("rick", "dick", "rich"),
    "robert": ("bob", "rob", "bobby"),
    "samuel": ("sam",),
    "stephen": ("steve",),
    "steven": ("steve",),
    "susan": ("sue",),
    "thomas": ("tom",),
    "timothy": ("tim",),
    "william": ("bill", "will", "billy"),
}


def given_forms(token: str) -> frozenset[str]:
    """All forms a given name may take: itself, its formal name(s), and their nicknames."""
    forms = {token}
    for formal, nicks in _NICKNAMES.items():
        if token == formal or token in nicks:
            forms.add(formal)
            forms.update(nicks)
    return frozenset(forms)


def name_tokens(name: str) -> list[str]:
    """Tokens in given..family order; handles ``Family, Given`` and drops suffixes."""
    if "," in name:
        family, _, given = name.partition(",")
        if given.strip():
            name = f"{given} {family}"
    return [t for t in normalize_name(name).split() if t not in SUFFIXES]


def _compatible(a: str, b: str) -> bool:
    """Same token, or one is the other's initial."""
    if a == b:
        return True
    return (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b))


def _middles_compatible(ma: Sequence[str], mb: Sequence[str]) -> bool:
    """Every middle on the shorter side must be compatible with one on the other side."""
    short, long_ = (ma, mb) if len(ma) <= len(mb) else (mb, ma)
    pool = list(long_)
    for m in short:
        for i, cand in enumerate(pool):
            if _compatible(m, cand):
                del pool[i]
                break
        else:
            return False
    return True


def _ordered_score(ta: Sequence[str], tb: Sequence[str]) -> float:
    if len(ta) < 2 or len(tb) < 2 or ta[-1] != tb[-1]:
        return 0.0
    fa, fb = ta[0], tb[0]
    ma, mb = ta[1:-1], tb[1:-1]
    if not _middles_compatible(ma, mb):
        return 0.0
    if fa == fb and len(fa) > 1:
        return 0.95 if ma and mb else 0.93
    if _compatible(fa, fb):
        return 0.80
    # Compound given name split differently: "yih kuen jan" vs "yihkuen jan"
    if "".join(ta[:-1]) == "".join(tb[:-1]):
        return 0.95
    if len(fa) > 1 and len(fb) > 1 and fb in given_forms(fa):  # "jim" vs "james"
        return 0.90
    return 0.0


def _token_score(ta: Sequence[str], tb: Sequence[str]) -> float:
    if ta == tb:
        return 1.0
    if "".join(ta) == "".join(tb):
        return 0.98
    best = _ordered_score(ta, tb)
    if best < 0.93:
        best = max(best, 0.85 * _ordered_score(ta, list(reversed(tb))))
    return best


def _german_fold(tokens: Sequence[str]) -> list[str]:
    """``ludaescher`` / ``ludascher`` (from Ludäscher) -> ``ludascher``."""
    return [t.replace("ae", "a").replace("oe", "o").replace("ue", "u") for t in tokens]


def name_score(a: str, b: str) -> float:
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return 0.0
    best = _token_score(ta, tb)
    if best < 0.93:  # umlaut transliteration differences: Klöckner / Kloeckner
        best = max(best, 0.98 * _token_score(_german_fold(ta), _german_fold(tb)))
    return best


def shares_name_token(names_a: Iterable[str], name_b: str) -> bool:
    """True if ``name_b`` has a (non-initial) token in common with any of ``names_a``.

    Names with no Latin-script tokens (e.g. Cyrillic ``Ричард Темпест``) cannot be judged
    and count as sharing (True).
    """
    tb = {t for t in _german_fold(name_tokens(name_b)) if len(t) > 1}
    if not tb:
        return True
    return any(tb & set(_german_fold(name_tokens(a))) for a in names_a)


def best_name_score(names_a: Iterable[str], names_b: Iterable[str]) -> float:
    list_b = list(names_b)
    return max((name_score(a, b) for a in names_a for b in list_b), default=0.0)


SURNAME_VARIANT_DISCOUNT = 0.90  # kept below the "peer" band of an exact match


def surname_variants(name: str) -> list[str]:
    """Alternative spellings for double / married surnames, used with a discount.

    ``Leonardo Patricio Chamorro Chavez`` -> ``leonardo patricio chamorro`` (publishes as
    "Leonardo P. Chamorro"); ``Deborah Levin Fliflet`` -> ``deborah levin`` and
    ``deborah fliflet``. Only when the last two tokens are full words (not initials).
    """
    t = name_tokens(name)
    if len(t) < 3 or len(t[-1]) < 2 or len(t[-2]) < 2:
        return []
    return [" ".join(t[:-1]), " ".join([*t[:-2], t[-1]])]


def index_keys(name: str) -> set[str]:
    """Blocking keys for candidate lookup: family name and (for reversed order) first token."""
    t = name_tokens(name)
    if len(t) < 2:
        return set()
    return {t[-1], t[0]}


def first_last(name: str) -> str | None:
    """``"benjamin adam grosser"`` -> ``"benjamin grosser"`` (used to spot names in keywords)."""
    t = name_tokens(name)
    if len(t) < 2 or len(t[0]) < 2:
        return None
    return f"{t[0]} {t[-1]}"


__all__ = [
    "SURNAME_VARIANT_DISCOUNT",
    "best_name_score",
    "first_last",
    "fold_ascii",
    "given_forms",
    "index_keys",
    "name_score",
    "name_tokens",
    "surname_variants",
]
