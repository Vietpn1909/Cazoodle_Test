# Data audit (2026-11-16) — findings, fixes, before/after

Senior data review of ingestion (M1) and faculty matching (M2), done with queries on the real
raw cache and DB (not code reading alone). Scratch audit scripts were run ad hoc; the checks
that matter are now unit/integration tests (169 passing).

## Findings and fixes

| # | Sev | Finding (evidence) | Fix |
|---|---|---|---|
| 1 | P0 | `institutions.id` filter missed works of UIUC sub-units (NCSA, State Geological Survey, Leggett Institute…): **+265** works; **178** tagged only "University of Illinois System" | Scope = `authorships.institutions.lineage:I157725225` + a system-only slice; system-only authorship counts as UIUC only if raw text names Urbana/Champaign/UIUC/NCSA (not Chicago/Springfield). 119 other-campus/ambiguous works dropped (`no_uiuc_authorship`). |
| 2 | P0 | List API truncates authorships at 100: 260 corpus works, **167 had no visible UIUC author** | `fetch_full_authorships`: single-work endpoint (free, $0) for 438 truncated works; store first 100 + every UIUC authorship; `authors_count` keeps the true size. |
| 3 | P0 | **~7.5% of authorships have no OpenAlex author id** (5.3% of UIUC ones, 2,656) and were silently skipped → **491 works lost their UIUC authorship** | Schema: `authorships` key = (work_id, author_order), nullable `author_id`, `author_name`; partial unique index on (work_id, author_id). |
| 4 | P0 | Orphan-author cleanup used `NOT IN (SELECT author_id …)`; with NULLs it is never true → would silently delete nothing | Added `IS NOT NULL`; regression test proven by mutation (fails with the old query). |
| 5 | P0 | Version duplicates (arXiv preprint + journal/conference) survived when years differed (35 groups); same-year merges kept the *preprint* | Union-find over same DOI, or same title (≥5 words) + shared author **name** tokens (author ids differ across versions; name order flips); canonical = published type > non-repository > most cited; carries max citations + `version_ids`. Unicode hyphens no longer glue title tokens. 35 → 1 groups. |
| 6 | P0 | Same author listed twice in a work: only the first listing kept → UIUC flag lost (3 works) | Merge listings (OR `is_uiuc`, join raw affiliations). |
| 7 | P1 | Top-20k cut biased by field: Economics 26%, Social Sci 23% vs Physics 9% cut; missing FWCI (19%) scored as 0 | Raw-citation percentile computed **within field**; missing FWCI = neutral 0.5. Cut now 11–19% in every field. |
| 8 | P1 | Paratext not flagged by OpenAlex ("Preface", "Introduction", "Cover Picture: …", "Thank you to our 2025 peer reviewers") | `generic_title` rule (exact titles / prefixes; "PREFACE - A RL framework…" kept). 26 dropped. |
| 9 | P1 | Abstracts prefixed "Abstract …" (2,960), markup tags (4) | `clean_abstract`. |
| 10 | P1 | 7.7% of works dated Jan 1 = year-only precision → misleading "most recent" | `works.date_precision` ('year' / 'day'). |
| 11 | P1 | Misaffiliation regex knew "ncsa" but not "National Center for Supercomputing Applications" / Leggett Institute | Regex extended with unit names; remaining 128 drops verified = Northwestern / UChicago / CZ Biohub Chicago authors. |
| 12 | P2 | Fetched-but-not-stored fields | Stored: `landing_page_url` (all 154 DOI-less works now linkable), `oa_status`, `venue_type`, `venue_id`, `citation_percentile`, `is_top_1pct/10pct`, `funders`, `authors_count`, `institutions_count`, `countries_count`. |
| 13 | P2 | Faculty position is free text | `faculty.rank` / `appointment` / `is_emeritus` (`pipeline/faculty/position.py`): Professor 691, Associate 197, Assistant 85, Other 20 (deans/directors/affiliates); 71 emeriti. |
| 14 | P2 | Faculty publications missed unresolved UIUC authorships | `faculty_works` table: via linked author ids (9,698) + unresolved UIUC authorships with a unique full-name match and topical overlap (98, spot-checked correct). |
| 15 | Ops | A 200 OK response truncated mid-body (2.2 MB) crashed the fetch | Client retries invalid JSON as transient (test added); fetch resumed from checkpoint. |

## Verified OK (no change needed)
- Excluded types are right for "papers": dataset 1,070, conference-abstract 699, editorial 194, software 188, erratum 143, book-review 132, peer-review 93, letter 24 (replies). (Counts can feed portal "other outputs" stats.)
- Fetch completeness: unique ids = `meta.count` for every slice (works 27,403; authors 18,180).
- Faculty matching precision: 20/20 random + 15/15 lowest-confidence links plausible; deterministic.

## Before → after
| Metric | Before | After |
|---|---|---|
| Raw works in scope | 26,960 | **27,403** |
| Eligible after filters | 23,650 | 23,694 |
| Works in corpus with **no UIUC authorship** | 491 | **0** |
| UIUC authorships stored | 47.4k (resolved only) | **50.1k** (incl. 2.6k unresolved) |
| Same-title (≥5 words) duplicate groups | 35 | **1** |
| Merged version groups (in corpus) | – | 259 |
| Field cut range | 9%–26% | 11%–19% |
| DOI-less works without a link | 154 | 0 |
| Faculty matched | 923 (92.4%) | 923 (92.4%) |
| Faculty with ≥1 corpus work | 830 | **837** (`faculty_works`) |

## Known limitations (accepted)
- Top-20k cut (spec): works below the cut are mostly low-citation 2024/2025 items. (An earlier note claimed "23/30 AI x military works kept";
  that came from a loose keyword regex full of false positives, e.g. "military veteran entrepreneurship". The real number of
  papers on AI in war/conflict is small — see `llm-assistant.md`, retrieval probes.)
- "Univ. of Illinois (United States)" (SPIE style) gives no campus → those system-only works are excluded.
- Two directory entries named "Bo Li" share near-identical CSV keywords → their split of same-name OpenAlex ids is unreliable.
- `date_precision` is a heuristic (Jan 1); month-level precision (day = 1) is not flagged.
