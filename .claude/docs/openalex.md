# OpenAlex — usage notes

Docs: https://help.openalex.org/api/ (agent-oriented summary: https://help.openalex.org/llms.txt)

## Access & cost (verified 2026-11-16)
- Base: `https://api.openalex.org`
- Budget is in USD per day. Without key: **$0.10/day shared by everyone on the IP**. Free key (https://openalex.org/settings/api): **$1/day**. Pass as `api_key=` query param (env `OPENALEX_API_KEY`).
- Cost seen: list/search request ≈ **$0.0001**; `group_by` institutions search ≈ $0.001.
- Response headers to log: `X-RateLimit-Remaining-USD`, `X-RateLimit-Cost-USD`, `X-RateLimit-Reset`, `Retry-After`.
- On 429/5xx: exponential backoff, honor `Retry-After`, max 5 retries.

## Key IDs
- UIUC institution: `I157725225` (ROR `047426m28`). Lineage parent: University of Illinois System `I2801919071` — do NOT use the system id (would include UIC/UIS).

## Works fetch
```
GET /works
  ?filter=institutions.id:I157725225,from_publication_date:2024-11-16,to_publication_date:2026-11-16
  &per_page=200&cursor=*            # then cursor=<meta.next_cursor> until null
  &select=id,doi,title,display_name,publication_date,publication_year,type,language,
          primary_location,open_access,authorships,cited_by_count,fwci,
          citation_normalized_percentile,primary_topic,topics,keywords,
          sustainable_development_goals,abstract_inverted_index,is_retracted,is_paratext,
          institutions_distinct_count,countries_distinct_count
```
- Counts in window: 26,962 total; 14,445 `type:article`; 22,268 `has_abstract:true`.
- Field distribution (primary_topic.field): CS 5,043 · Engineering 2,900 · Social Sciences 2,601 · Medicine 2,184 · Physics & Astronomy 2,082 · Agri & Bio 1,864 · Biochem/Genetics 1,521 · Environmental 1,469 · Materials 834 · Psychology 778.
- Useful aggregates without downloading: `group_by=primary_topic.field.id`, `group_by=publication_year`, `group_by=authorships.institutions.id`.

## Record shape notes
- `authorships[]`: `author_position`, `author{id,display_name,orcid}`, `institutions[]{id,display_name,ror,country_code,lineage}`, `raw_affiliation_strings`, `is_corresponding`. Mark `is_uiuc` when any institution id == I157725225.
- `abstract_inverted_index`: `{word: [positions]}` → rebuild:
  ```python
  def rebuild(inv):
      if not inv:
          return None
      pos = {p: w for w, ps in inv.items() for p in ps}
      return " ".join(pos[i] for i in sorted(pos))
  ```
- `topics[]` include `subfield/field/domain` — store hierarchy in `topics`.

## Quality filter (apply in order, count drops per rule)
1. `is_paratext` or `is_retracted` → drop.
2. Missing title → drop.
3. `type` not in {article, preprint, review, book-chapter, book, dissertation, proceedings-article} → drop (keep list in config).
4. Suspected mis-affiliation: no UIUC authorship with a matching `raw_affiliation_strings` mentioning Illinois/Urbana/Champaign/UIUC **and** language not `en` → flag/drop. (Observed: top-cited window work is a Russian paper wrongly attributed.)
5. Dedupe by DOI, then by normalized title.

## Ranking ("top" works)
`rank_score = 0.5*pct(cited_by_count within pub year-month) + 0.3*pct(fwci) + 0.2*recency` (percentiles 0–1; recency linear over window). Keep top 20,000 (or all if fewer survive). Document final weights in README.
