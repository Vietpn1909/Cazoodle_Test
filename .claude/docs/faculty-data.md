# Faculty dataset (`illinois-researchers.csv`)

## Shape (verified)
- 1,000 rows, UTF-8, quoted CSV. Columns: `email, name, photo_url, position, keywords`.
- `photo_url`: 105 are literal `NULL` → UI falls back to initials avatar.
- `position`: 2 NULL. Top: Professor 322, Associate Professor 145, Assistant Professor 73, Professor Emeritus 24; many long titled chairs.
- `keywords`: Postgres array literal, e.g. `{"parallel computing",sampling,"text mining"}`; 23–574 items (avg ~122).
- No department column, no OpenAlex/ORCID id. `email` is the stable key.
- Large fields → set `csv.field_size_limit` high.

## Parsing keywords
Postgres array literal: items separated by `,`; items with spaces/special chars are double-quoted with `\"` / `""` escaping. Write a small tokenizer (unit-tested) — do not split naively on commas.

## Keyword cleaning
1. lowercase, strip, collapse whitespace, dedupe.
2. Blocklist / patterns: institution names (`university`, `college`, `institute of`), conference/venue names (`conference`, `symposium`, `siam`, `ieee`, `acm` when standalone), overly generic phrases (`real world`, `language`, `mathematics`, `such information networks`, `category names`). Keep blocklist in `pipeline/config/keyword_blocklist.txt`.
3. Rank remaining by IDF across all 1,000 faculty (rare = more distinctive); keep top ~25 as `keywords_clean`.
4. Optional: map keywords to OpenAlex topics by embedding similarity for consistent facets.

## Faculty ↔ OpenAlex author matching
Goal: `faculty_author_map(faculty_id, author_id, confidence, method)`; a faculty member may map to several author ids (fragmented profiles).
1. Candidate pool: authors appearing in UIUC authorships of fetched works (free — no extra API calls).
2. Name normalization: unidecode, lowercase, remove punctuation, handle middle names/initials (`Benjamin Adam Grosser` ↔ `Benjamin Grosser`, `B. A. Grosser`), surname-first variants.
3. Match: exact normalized → `method=exact`; else trigram similarity ≥ 0.85 on full name with same surname → `method=fuzzy`.
4. Disambiguate multiple candidates with a score: name similarity, #UIUC works, cosine(faculty keyword embedding, author's works centroid), ORCID presence. Accept candidates with confidence ≥ 0.7; keep all accepted (merge fragments).
5. Unmatched faculty → budgeted fallback `GET /authors?filter=last_known_institutions.id:I157725225&search=<name>&select=id,display_name,orcid,works_count` (cap total calls; cache responses).
6. Emit report: matched %, by method, confidence histogram, sample of low-confidence pairs for manual check.

## Faculty expertise profile (used by search & UI)
- `keywords_clean` + top topics/subfields from their matched UIUC works + their top cited recent works.
- Faculty embedding = embed("name-free" text of clean keywords + top topics).
