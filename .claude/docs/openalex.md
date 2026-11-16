# OpenAlex — usage notes

Docs: https://help.openalex.org/api/ (agent-oriented summary: https://help.openalex.org/llms.txt)
Code: `pipeline/src/pipeline/openalex/client.py`, `fetch_works.py`, `openalex/transform.py`, `load_works.py`.

## Access & cost (verified 2026-11-16)
- Base: `https://api.openalex.org`
- Budget is in USD per day. Without key: **$0.10/day shared by everyone on the IP**. Free key (https://openalex.org/settings/api): **$1/day**. Pass as `api_key=` query param (env `OPENALEX_API_KEY`). Exhausted budget → HTTP 429, never a charge (prepaid credit is opt-in).
- Cost seen: list/search request ≈ **$0.0001**; `group_by` institutions search ≈ $0.001.
- **`cursor=` paging past page 1 requires an API key** (HTTP 401 "API key required for cursor paging"). Basic `page=` paging works without a key but is capped at 10,000 results per query.
- Response headers logged: `X-RateLimit-Remaining-USD`, `X-RateLimit-Cost-USD`, `X-RateLimit-Reset`, `Retry-After`.
- Client: retries 429/5xx/network with backoff (honors `Retry-After`); stops with `BudgetExhaustedError` when remaining USD ≤ 0; never logs the key (httpx logger set to WARNING, key redacted from errors).

## Key IDs
- UIUC institution: `I157725225` (ROR `047426m28`). Lineage parent: University of Illinois System `I2801919071` — do NOT use the system id (would include UIC/UIS).

## Works fetch (key-free, ADR-010)
- Window split into **calendar-month slices** (each ~1–2.3k works, Jan has a Jan-1 spike) → `page=1..ceil(count/200)` per slice with `sort=publication_date:asc` (ties ordered by id → stable pages). `sort=id` is NOT accepted.
- Request: `filter=institutions.id:I157725225,from_publication_date:<slice start>,to_publication_date:<slice end>&per_page=200&page=N&sort=publication_date:asc&select=<WORK_FIELDS>`
- `select` fields: `id,doi,title,publication_date,publication_year,type,language,primary_location,open_access,authorships,is_authors_truncated,cited_by_count,fwci,citation_normalized_percentile,primary_topic,topics,keywords,sustainable_development_goals,abstract_inverted_index,is_retracted,is_paratext,institutions_distinct_count,countries_distinct_count`
- Raw cache: `data/raw/openalex/works/<slice>_pNNN.jsonl.gz` + `_state.json` (checkpoint per page → resumable). After completion `verify()` compares unique ids per slice with `meta.count`.
- A 200-work page is ~8 MB of JSON (authorships dominate) → stored gzipped.
- Counts in window: 26,960 total; 14,445 `type:article`; 22,268 `has_abstract:true`.
- Field distribution (primary_topic.field): CS 5,043 · Engineering 2,900 · Social Sciences 2,601 · Medicine 2,184 · Physics & Astronomy 2,082 · Agri & Bio 1,864 · Biochem/Genetics 1,521 · Environmental 1,469 · Materials 834 · Psychology 778.
- Useful aggregates without downloading: `group_by=primary_topic.field.id`, `group_by=publication_year`, `group_by=authorships.institutions.id`.

## Record shape notes
- `authorships[]` keys: `author_position`, `author{id,display_name,orcid}`, `institutions[]{id,display_name,ror,country_code,type,lineage}`, `countries`, `affiliations`, `raw_affiliation_strings`, `raw_author_name`, `raw_orcid`, `is_corresponding`. Authorships are **truncated to 100** in list responses (`is_authors_truncated`).
- `is_uiuc` = institution id is UIUC **or** UIUC is in its `lineage` (sub-units like institutes).
- The same author can appear twice in one work's authorships → deduped (first occurrence kept).
- `abstract_inverted_index`: `{word: [positions]}` → `transform.rebuild_abstract`.
- `topics[]` carry `subfield/field/domain` → stored denormalized in `topics`; ids stored short (`T10036`, field `17`).
- Work types seen: article, conference-paper (not "proceedings-article"), review, book-chapter, book, preprint, …

## Quality filter (`transform.drop_reason`, counts per rule in `data/processed/reports/load_works.json`)
1. `is_paratext` / `is_retracted` → drop.
2. Missing title or publication date → drop.
3. `type` not in {article, preprint, review, book-chapter, book, dissertation, conference-paper, report} → drop.
4. Suspected mis-affiliation: the UIUC-tagged authorships have raw affiliation strings, and none mentions Illinois/Urbana/Champaign/UIUC/Beckman/NCSA → drop. (Empty raw strings → cannot verify → keep.)
5. Duplicate id; then duplicate DOI or normalized title+year (most-cited copy kept).

## Ranking ("top" works, `transform.compute_rank_scores` / `RankWeights`)
`rank_score = 0.4·shrink(norm_cite_pct) + 0.2·shrink(pct(fwci)) + 0.2·pct(cited_by_count) + 0.2·recency`
- `norm_cite_pct` = OpenAlex `citation_normalized_percentile.value`; fallback own percentile of citations.
- `shrink(x, c) = w·x + (1−w)·0.5`, `w = c/(c+5)`: normalized metrics on few citations are noise (observed: 3-day-old paper, 2 citations, FWCI 66 ranked #1 before shrinkage).
- `recency` linear 0..1 over the window (keeps recent, not-yet-cited work competitive).
- Keep top 20,000 (`--top-n`).

## Corpus numbers (run 2026-11-16, `data/processed/reports/load_works.json`)
- Raw 26,960 → dropped: type 2,879 · dup DOI/title 317 · non-English 89 · paratext 18 · retracted 6 · no title 1 (mis-affiliation regex: 0 hits) → eligible 23,650 → **top 20,000** loaded.
- Selected types: article 12,271 · preprint 3,957 · conference-paper 2,605 · review 608 · book-chapter 339 · report 137 · book 45 · dissertation 38. With abstract: 17,274.
- DB: 83.6k authors, 154k authorships, 17.7k distinct UIUC-tagged authors, 3.6k topics, 100k work-institution links.
- Fetch: 147 calls, $0.0147, 59 MB gzipped. Load (COPY): ~11 s warm.
- Many works dated late 2026 already have dozens of citations → journal versions of earlier preprints (OpenAlex `publication_date` = version date).
