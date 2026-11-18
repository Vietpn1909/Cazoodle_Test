# Architecture

```
┌──────────── OFFLINE pipeline (pipeline/, Python) ─────────────┐
│ OpenAlex ─► data/raw/*.jsonl ─► normalize ─► filter + rank     │
│ illinois-researchers.csv ─► parse ─► clean keywords            │
│ Wikipedia/Commons/OpenAlex geo ─► institution.json             │
│ ─► faculty↔author matching ─► embeddings ─► precomputed stats  │
└──────────────────────────────┬────────────────────────────────┘
                               ▼
            PostgreSQL 16 (+ pgvector, tsvector, pg_trgm)
                               ▲
┌──────────────────────────────┴──────────────┐
│ backend/ FastAPI                             │
│  app/api        routers (REST + SSE)         │
│  app/services   retrieval, stats, faculty    │
│  app/assistant  orchestrator, tools,         │
│                 providers/ (none, ...)       │
│  app/db         models, session, migrations  │
└──────────────────────────────┬──────────────┘
                               ▼
┌──────────────────────────────────────────────┐
│ frontend/ Next.js: single portal page + chat │
└──────────────────────────────────────────────┘
```

## Data model
```
institution(id PK, openalex_id, ror, name, geo jsonb, images jsonb, facts jsonb, sources jsonb)
works(id PK 'W…', doi, title, abstract, publication_date, date_precision, publication_year, type,
      language, cited_by_count, fwci, citation_percentile, is_top_1pct, is_top_10pct, is_oa,
      oa_status, oa_url, landing_page_url, venue_name, venue_type, venue_id, primary_topic_id FK,
      keywords[], sdgs jsonb, funders jsonb, authors_count, institutions_count, countries_count,
      version_ids[], rank_score, embedding vector(384), tsv tsvector GENERATED)
authors(id PK 'A…', display_name, orcid, works_count, cited_by_count, h_index)  -- metrics from directory
authorships(work_id FK, author_order, author_id FK NULL (unresolved), author_name, author_position,
            is_corresponding, is_uiuc, raw_affiliation, PK(work_id, author_order),
            UNIQUE(work_id, author_id) WHERE author_id IS NOT NULL)
topics(id PK 'T…', name, subfield_id, subfield, field_id, field, domain_id, domain)
work_topics(work_id FK, topic_id FK, score float)
faculty(id PK serial, email UNIQUE, name, name_norm, name_variants text[], position, rank,
        appointment, is_emeritus, photo_url,
        keywords_raw text[], keywords_clean text[], keywords_top text[], embedding vector(384))
faculty_author_map(faculty_id FK, author_id FK, confidence float, method text, evidence jsonb)
faculty_works(faculty_id FK, work_id FK, via 'author_id'|'unresolved_name')   -- derived
work_institutions(work_id, institution_id, name, country_code)   -- collaborators
stats_kv(key PK, value jsonb, computed_at)                         -- precomputed widgets
meta(key PK, value)            -- snapshot_date, embedding_model, pipeline version
```
Indexes: HNSW on `works.embedding`, `faculty.embedding`; GIN on `works.tsv`; GIN trigram on `authors.display_name`, `faculty.name_norm`; btree on `works.publication_date`, `works.rank_score`.

## REST API (prefix `/api`)
| Method | Path | Purpose |
|---|---|---|
| GET | `/overview` | institution info + KPIs + "data as of" |
| GET | `/stats/output?granularity=month` | works per period |
| GET | `/stats/fields` | field → subfield tree (treemap) |
| GET | `/stats/rising-topics` | topics with largest growth |
| GET | `/stats/collaborators` | top partner institutions / countries |
| GET | `/papers?q=&field=&sort=cited\|recent\|relevance&page=` | paper list / search |
| GET | `/papers/{id}` | paper detail |
| GET | `/faculty?q=&page=` | faculty list / expertise search |
| GET | `/faculty/{id}` | profile: clean keywords, topics, UIUC papers |
| POST | `/chat` | SSE stream of assistant events |
| GET | `/health` | liveness + DB check |

All responses use Pydantic schemas; generate TS types for the frontend from `/openapi.json` (`openapi-typescript`).

## Chat SSE event types
`{"type":"tool_call","name":..,"args":..}` · `{"type":"tool_result","name":..,"count":..}` · `{"type":"token","text":..}` · `{"type":"citations","items":[{kind:"work"|"faculty",id,title,url}]}` · `{"type":"done"}` · `{"type":"error","message":..}`

## Config (env)
`DATABASE_URL`, `OPENALEX_API_KEY` (optional), `OPENALEX_MAILTO` (optional), `SNAPSHOT_DATE=2026-11-16`, `EMBEDDING_MODEL=BAAI/bge-small-en-v1.5`, `LLM_PROVIDER=none`, `LLM_MODEL=`, `LLM_API_KEY=`, `LLM_BASE_URL=`.
