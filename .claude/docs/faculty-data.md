# Faculty dataset & faculty ↔ OpenAlex author matching

Code: `pipeline/src/pipeline/faculty/{parse,keywords,names,text,matching}.py`, steps `load_faculty.py`, `fetch_authors.py`, `match_faculty.py`.
Reports: `data/processed/reports/load_faculty.json`, `match_faculty.json`.

## CSV shape (verified)
- 1,000 rows, UTF-8, quoted CSV. Columns: `email, name, photo_url, position, keywords`. Missing values = literal `NULL` (104 photos, 2 positions after merge).
- **Duplicate email** `c-cheng@illinois.edu` (same person: "Chi-Hing C Cheng-De Vries" / "C.-H. Christina Cheng") → merged: first row wins, other name → `faculty.name_variants`, keywords unioned → **999 faculty**.
- **Shared names**: two "Bo Li", two "Yi Lu" (different emails = different people).
- `keywords`: Postgres array literal (quoted items, backslash escapes, e.g. `"\\cal l"`) → `parse_pg_array` (tokenizer, unit-tested). 23–574 per person, 22.2k distinct.
- Names: 2–5 tokens, accents (Erdoğan, María), hyphens, initials-only ("G Peters"), nicknames ("Jim Rehg"), double/married surnames ("Chamorro Chavez", "Levin Fliflet").

## Keyword cleaning (`keywords.clean_keywords`) — 122,476 → 122,358 mentions kept
- Normalize: NFKC, unicode dashes/quotes → ASCII, lowercase, strip edge punctuation, **strip leading determiners** ("such peptides" → "peptides"), **join split formulas** ("co 2" → "co2", "l 1" → "l1").
- Drop: markup (`= \ < > { } |`), private-use glyphs, numbers with ≥4 digits (zip codes, years: "il 61801", "tokyo 2020"), too short (<3, except formulas & whitelist ai/hiv/dna/…), empty phrases ("real world", "system demonstrations", "empirical methods"…), venue/org names (ending in university/institute/conference/symposium/workshop/press/department/journal(s)/proceedings with ≥2 words; starting with ieee/acm/siam/nsf not followed by a number; containing "international conference"), and **people's names** (any directory "first last" inside a keyword).
- Must survive (tested): civil society, journalism, ieee 802.11, university student retention, co2, hiv, a. hybridus, el niño.
- **Display keywords** `faculty.keywords_top` (15): `score = 2·support + specificity`; support = share of the keyword's terms found in the person's matched publications; specificity = normalized IDF across the directory, ×0.5 for keywords only one person has. Full cleaned list stays in `keywords_clean` (for search).

## Author directory (`fetch_authors`, ADR-011)
- OpenAlex `/authors` with `last_known_institutions.id:I157725225`, `works_count` ≥ 5, sliced `5-9 / 10-19 / >19` (6.3k / 4.5k / 7.4k, each < 10k cap) → **18,180 authors**, 92 calls, **$0.0092**, verified.
- Gives `display_name_alternatives`, ORCID, `summary_stats.h_index`, `topics`. Name search (`/authors?search=`) costs $0.001/call (10×) → not used.
- Profiles = directory (18.2k) ∪ corpus-only UIUC authors from our works (8.7k) = 26.9k; research text = OpenAlex topics + titles/keywords/topics of their UIUC works.

## Matching algorithm (`faculty/matching.py`)
1. **Blocking**: index author names by family token and first token (reversed order); faculty names + `surname_variants`.
2. **Name score** (`names.name_score`, max over all spellings): 1.0 identical · 0.98 compact equal / umlaut transliteration (Klöckner↔Kloeckner) · 0.95 first+last equal, middles compatible · 0.93 one side lacks middle · 0.90 nickname (Jim↔James) · 0.80 first initial only · ×0.85 reversed order · ×0.90 via surname variant · 0 for conflicting middle initials. Normalization turns any non-letter into a space *before* accent folding (El‐Rayes, O’Keefe), maps ß→ss etc.
3. **Topic score**: TF-IDF cosine (faculty cleaned keywords vs author research text).
   Calibration (2026-11-16): unique exact-name pairs p5 = 0.196, p10 = 0.224, p50 = 0.31; random pairs p90 = 0.074, p95 = 0.105, p99 = 0.173.
4. **Confidence** (`MatchConfig`, accept ≥ 0.70):
   - cos < 0.10 → `low_topic` (rejected).
   - `unique` (no other candidate with a name score within 0.05): `name × (0.75 + 0.25·strength)`, strength = min(1, cos/0.20).
   - `ambiguous` (several people share the name — common at UIUC, e.g. students named like a professor): `name × (0.6 + 0.4·strength) × cos/best_cos`, capped below accept if cos < 0.17.
   - `alias_only`: the profile's display name shares no token with the faculty name (merged OpenAlex profile, e.g. "Yuxiong Wang" inside "Ziqi Pang") → never linked. Non-Latin display names are not judged.
5. **Resolution**: an author id goes to one faculty member (highest confidence, then topic); a faculty member keeps all accepted ids (fragmented profiles).

## Results (2026-11-16)
- **923 / 999 faculty matched (92.4%)**, 1,114 links (776 with 1 id, 115 with 2, 32 with 3–5); 830 have ≥1 UIUC work in the 2-year corpus.
- Links: 719 unique, 395 ambiguous; confidence 0.7–0.8: 65, 0.8–0.9: 84, 0.9–1.0: 202, 1.0: 763.
- Unmatched 76: 62 no name candidate (mostly left UIUC / emeriti / low-publishing fields: Weng Cho Chew, Mei-Po Kwan, Andrew Alleyne…), 11 low topic, 3 ambiguous.
- Random audit of 20 links with corpus works: 20/20 plausible. Hand-checked hard cases: Jiawei Han (1 of 8 same-name ids kept), Jim Rehg, Don Ort, Deborah Levin Fliflet, Chamorro Chavez, El-Rayes, Große Perdekamp, Klöckner, Tempest (Cyrillic display name), Ziqi Pang / Razafindrambinina merges blocked.
- **Known limitation**: the two "Bo Li" have near-identical keyword lists in the CSV, so their split of same-name OpenAlex ids is not reliable.
- Deterministic (identical link hash across runs); `load_works` re-runs keep linked authors (they are referenced by `faculty_author_map`).

## Faculty → works (`faculty_works`, derived in match_faculty)
- `author_id`: every authorship of a linked OpenAlex author (9,698 rows).
- `unresolved_name`: UIUC authorships without author id (2,647) whose listed name is a full-name match (≥ 0.93) to exactly one directory member and whose work overlaps their keywords (cos ≥ 0.10) → 98 rows; spot-check correct (Kevin Wise, Jorge Noronha, Tamer Başar…).
- 837 faculty have ≥1 corpus work. Use this table (not raw joins) for faculty publication lists and expertise evidence.

## Position-derived fields (`faculty/position.py`)
- `rank`: highest academic rank mentioned (named/endowed chairs = Professor): Professor 691, Associate 197, Assistant 85, Lecturer 3, Researcher 3, Other 20 (deans, directors, affiliates).
- `appointment`: regular 915 / research 34 / adjunct 26 / teaching 13 / clinical 11; `is_emeritus`: 71.

## Storage
- `faculty`: `name_variants`, `keywords_raw`, `keywords_clean`, `keywords_top`, stable `id` (upsert by email).
- `faculty_author_map`: `confidence`, `method`, `evidence` JSON (name, topic, author names, works counts, candidates).
- `authors`: `works_count`, `cited_by_count`, `h_index` from the directory (9.2k authors).
