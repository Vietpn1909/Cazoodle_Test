"""Faculty directory <-> OpenAlex author matching.

Evidence per (faculty, author) pair:
- ``name``: best ``names.name_score`` over all spellings (faculty name + merged variants vs.
  OpenAlex display name + ``display_name_alternatives``).
- ``topic``: TF-IDF cosine between the faculty's cleaned keywords and the author's research
  text (OpenAlex topics + titles/keywords/topics of their UIUC works in our corpus).

A faculty member can map to several author ids (OpenAlex splits people into fragments);
an author id maps to at most one faculty member (best confidence wins).
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pipeline.faculty.names import (
    SURNAME_VARIANT_DISCOUNT,
    best_name_score,
    index_keys,
    shares_name_token,
    surname_variants,
)
from pipeline.faculty.text import terms
from pipeline.openalex.transform import short_id


@dataclass
class AuthorProfile:
    id: str
    names: list[str]
    orcid: str | None = None
    works_count: int | None = None  # all-time, from the OpenAlex directory
    cited_by_count: int | None = None
    h_index: int | None = None
    in_directory: bool = False
    corpus_uiuc_works: int = 0  # UIUC-affiliated works in our 2-year corpus
    text: list[str] = field(default_factory=list)  # research text snippets


def profiles_from_directory(raw_authors: Iterable[Mapping[str, Any]]) -> dict[str, AuthorProfile]:
    out: dict[str, AuthorProfile] = {}
    for a in raw_authors:
        aid = short_id(a["id"])
        if not aid:
            continue
        names = [a.get("display_name") or "", *(a.get("display_name_alternatives") or [])]
        topics = a.get("topics") or []
        text = [
            " ".join(
                filter(
                    None,
                    [
                        t.get("display_name"),
                        (t.get("subfield") or {}).get("display_name"),
                    ],
                )
            )
            for t in topics
        ]
        out[aid] = AuthorProfile(
            id=aid,
            names=[n for n in dict.fromkeys(names) if n],
            orcid=(a.get("orcid") or "").removeprefix("https://orcid.org/") or None,
            works_count=a.get("works_count"),
            cited_by_count=a.get("cited_by_count"),
            h_index=(a.get("summary_stats") or {}).get("h_index"),
            in_directory=True,
            text=text,
        )
    return out


def add_corpus_evidence(
    profiles: dict[str, AuthorProfile],
    corpus_rows: Iterable[Mapping[str, Any]],
) -> None:
    """Merge UIUC authorships from our corpus.

    ``corpus_rows``: one row per UIUC authorship with keys
    ``author_id, display_name, orcid, title, keywords, topic`` (see ``load_corpus_rows``).
    """
    for r in corpus_rows:
        aid = r["author_id"]
        p = profiles.get(aid)
        if p is None:
            p = profiles[aid] = AuthorProfile(id=aid, names=[r["display_name"]], orcid=r["orcid"])
        elif r["display_name"] not in p.names:
            p.names.append(r["display_name"])
        p.corpus_uiuc_works += 1
        p.text.append(
            " ".join(filter(None, [r["title"], " ".join(r["keywords"] or []), r["topic"]]))
        )


# --------------------------------------------------------------------------- tf-idf


class TfIdf:
    """Minimal sparse TF-IDF (log-scaled tf, smoothed idf) with cosine similarity."""

    def __init__(self, docs: Iterable[Sequence[str]]) -> None:
        df: Counter[str] = Counter()
        n = 0
        for d in docs:
            n += 1
            df.update(set(d))
        self.n = n
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()}

    def vector(self, doc: Sequence[str]) -> dict[str, float]:
        tf = Counter(doc)
        vec = {t: (1 + math.log(c)) * self.idf.get(t, 1.0) for t, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    @staticmethod
    def cosine(a: Mapping[str, float], b: Mapping[str, float]) -> float:
        if len(a) > len(b):
            a, b = b, a
        return sum(v * b.get(t, 0.0) for t, v in a.items())


# --------------------------------------------------------------------------- matching


@dataclass(frozen=True)
class FacultyInput:
    id: int
    names: list[str]
    keywords: list[str]


@dataclass
class Candidate:
    faculty_id: int
    author_id: str
    name: float
    topic: float
    confidence: float
    method: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class MatchConfig:
    """Thresholds calibrated on 2026-11-16 data (see `.claude/docs/faculty-data.md`):
    topic cosine of near-certain pairs (unique exact name): p5=0.196, p10=0.224, p50=0.31;
    random faculty/author pairs: p90=0.074, p95=0.105, p99=0.173."""

    min_name: float = 0.80  # pairs below this name score are never considered
    accept: float = 0.70  # final confidence needed to keep a link
    topic_full: float = 0.20  # cosine giving full topical strength (~p5 of true pairs)
    topic_floor: float = 0.10  # below: topical evidence is noise (~p95 of random pairs)
    ambiguous_floor: float = 0.17  # several same-name candidates: need ~p99 of random pairs
    peer_margin: float = 0.05  # candidates within this of the best name score are "peers"


def _score_with_variants(fac_names: Sequence[str], author_names: Sequence[str]) -> float:
    direct = best_name_score(fac_names, author_names)
    variants = [v for n in fac_names for v in surname_variants(n)]
    via_variant = best_name_score(variants, author_names) * SURNAME_VARIANT_DISCOUNT
    return max(direct, via_variant)


def author_name_score(
    fac_names: Sequence[str], author_names: Sequence[str], cfg: MatchConfig
) -> tuple[float, bool]:
    """(name score, alias_only). ``author_names[0]`` is the OpenAlex display name.

    ``alias_only``: the display name shares no name token with the faculty member, i.e. the
    faculty name only appears among the alternatives of a profile shown under a different
    person's name — a merged OpenAlex profile (observed: "Yuxiong Wang" inside "Ziqi Pang",
    "Nicole Riemer" inside "Patricia N. Razafindrambinina"). Formatting variants of the
    same person ("José Meseguer B", "Kim A. Selting") share a token and are not flagged.
    """
    if not author_names:
        return 0.0, False
    score = _score_with_variants(fac_names, author_names)
    alias_only = not shares_name_token(fac_names, author_names[0])
    return score, alias_only


def topic_strength(cos: float, cfg: MatchConfig) -> float:
    return min(1.0, cos / cfg.topic_full) if cfg.topic_full > 0 else 0.0


def confidence(
    name: float, cos: float, *, ambiguous: bool, best_cos: float, cfg: MatchConfig
) -> tuple[float, str]:
    """Combine name and topical evidence into (confidence, method).

    - ``unique``: the only name-compatible candidate; name + topic above noise suffices.
    - ``ambiguous``: several people share the name (common at a large university, e.g.
      students named like a professor). A candidate must be topically close to the best
      one *and* above the random-pair p99, otherwise it is likely a different person.
    - topic below the noise floor -> rejected regardless of name.
    """
    strength = topic_strength(cos, cfg)
    if cos < cfg.topic_floor:
        return name * 0.6 * strength, "low_topic"
    if not ambiguous:
        return name * (0.75 + 0.25 * strength), "unique"
    rel = cos / best_cos if best_cos > 0 else 0.0
    conf = name * (0.6 + 0.4 * strength) * rel
    if cos < cfg.ambiguous_floor:
        conf = min(conf, cfg.accept - 0.05)
    return conf, "ambiguous"


def match_all(
    faculty: Sequence[FacultyInput],
    profiles: Mapping[str, AuthorProfile],
    cfg: MatchConfig = MatchConfig(),
) -> tuple[list[Candidate], dict[int, list[Candidate]]]:
    """Return (accepted links, all scored candidates per faculty)."""
    # Blocking index: family name / first token -> author ids
    index: dict[str, set[str]] = defaultdict(set)
    for p in profiles.values():
        for n in p.names:
            for k in index_keys(n):
                index[k].add(p.id)

    fac_docs = {f.id: terms(" ".join(f.keywords)) for f in faculty}
    auth_docs = {aid: terms(" ".join(p.text)) for aid, p in profiles.items()}
    tfidf = TfIdf([*fac_docs.values(), *auth_docs.values()])
    fac_vecs = {fid: tfidf.vector(d) for fid, d in fac_docs.items()}
    auth_vec_cache: dict[str, dict[str, float]] = {}

    scored: dict[int, list[Candidate]] = {}
    for f in faculty:
        lookup_names = [*f.names, *(v for n in f.names for v in surname_variants(n))]
        cand_ids = set().union(*(index.get(k, set()) for n in lookup_names for k in index_keys(n)))
        pairs: list[tuple[str, float, float]] = []
        alias_only: set[str] = set()
        for aid in cand_ids:
            ns, is_alias = author_name_score(f.names, profiles[aid].names, cfg)
            if ns < cfg.min_name:
                continue
            if is_alias:
                alias_only.add(aid)
            if aid not in auth_vec_cache:
                auth_vec_cache[aid] = tfidf.vector(auth_docs[aid])
            pairs.append((aid, ns, TfIdf.cosine(fac_vecs[f.id], auth_vec_cache[aid])))

        # Only candidates with a name match as strong as the best one create ambiguity
        # (a stray "H. Phillips" must not make an exact "Heidi Phillips" ambiguous).
        # Weaker-name candidates are judged as ambiguous against the stronger ones.
        best_name = max((ns for _, ns, _ in pairs), default=0.0)
        n_peers = sum(1 for _, ns, _ in pairs if ns >= best_name - cfg.peer_margin)
        best_cos = max((cos for _, _, cos in pairs), default=0.0)
        cands: list[Candidate] = []
        for aid, ns, cos in pairs:
            p = profiles[aid]
            ambiguous = n_peers >= 2 or ns < best_name - cfg.peer_margin
            conf, method = confidence(ns, cos, ambiguous=ambiguous, best_cos=best_cos, cfg=cfg)
            if aid in alias_only:  # merged profile under someone else's name: never link
                conf, method = min(conf, cfg.accept - 0.05), "alias_only"
            cands.append(
                Candidate(
                    faculty_id=f.id,
                    author_id=aid,
                    name=round(ns, 3),
                    topic=round(cos, 4),
                    confidence=round(conf, 4),
                    method=method,
                    evidence={
                        "author_names": p.names[:5],
                        "works_count": p.works_count,
                        "corpus_uiuc_works": p.corpus_uiuc_works,
                        "in_directory": p.in_directory,
                        "n_candidates": len(pairs),
                        "n_name_peers": n_peers,
                    },
                )
            )
        cands.sort(key=lambda c: (c.confidence, c.topic), reverse=True)
        scored[f.id] = cands

    # Resolve: each author goes to the single best faculty (confidence, then topical fit);
    # a faculty member keeps all accepted links (fragmented OpenAlex profiles).
    best_for_author: dict[str, Candidate] = {}
    for cands in scored.values():
        for c in cands:
            if c.confidence < cfg.accept:
                continue
            cur = best_for_author.get(c.author_id)
            if cur is None or (c.confidence, c.topic) > (cur.confidence, cur.topic):
                best_for_author[c.author_id] = c
    accepted = sorted(best_for_author.values(), key=lambda c: (c.faculty_id, -c.confidence))
    return accepted, scored
