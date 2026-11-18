# Architecture Decision Records

Format: context → decision → consequences. Append new ADRs; mark superseded ones instead of deleting.

## ADR-001 — Single database: PostgreSQL 16 + pgvector
- **Context:** ~20k works, ~1k faculty, tens of thousands of authorships. Need relational joins, full-text, vector search.
- **Decision:** One Postgres with `pgvector`, `tsvector`, `pg_trgm`.
- **Consequences:** No separate vector DB to operate; hybrid search in SQL; easy Docker setup.

## ADR-002 — Fetch all recent works, curate locally
- **Context:** 26,962 UIUC works since 2024-11-16; spec asks for "top 10k–20k". OpenAlex data contains mis-affiliations. API is USD-budgeted.
- **Decision:** Fetch all works in the window once (~135 calls), cache JSONL, apply quality filters + composite `rank_score` locally, keep top ~20k.
- **Consequences:** Reproducible, cheap, transparent ("top" is defined by us and documented).

## ADR-003 — Assistant uses tool calling over structured search, not naive top-k RAG
- **Context:** Required queries need sort-by-date after relevance filtering, and per-faculty aggregation.
- **Decision:** Expose typed tools (`search_papers`, `search_faculty`, …) backed by hybrid retrieval; the model/router picks tools and parameters.
- **Consequences:** Correct ordering/aggregation; answers grounded in tool output with citations.

## ADR-004 — LLM provider deferred; provider-agnostic interface + `none` mode  *(status: open)*
- **Context:** No Anthropic API key planned for now; provider to be chosen later.
- **Decision:** Define `LLMProvider` protocol + neutral tool schema. Ship `none` provider (rule-based intent router + templated answers) so the full system works without any key. Add real adapters later (candidates: Anthropic, OpenAI-compatible incl. local Ollama, Gemini) selected via `LLM_PROVIDER` env.
- **Consequences:** Demo always runs; swapping provider touches only `backend/app/assistant/providers/`.

## ADR-005 — Local embeddings (`BAAI/bge-small-en-v1.5`, 384-d, via fastembed)
- **Context:** Must work without paid keys; corpus is small.
- **Decision:** Local CPU embeddings, computed in the pipeline, stored in pgvector. Model name stored in DB metadata.
- **Consequences:** ~10–20 min one-off embedding on CPU; swappable later (re-embed required).
- **Update 2026-11-18 (measured):** fastembed serves the **int8-quantized** ONNX build of bge-small. On this
  24-core machine throughput is capped at ~15 texts/s whatever the thread count, `parallel=` workers or
  `max_length` (20,000 works = 22.7 min, 999 faculty = 30 s), so the step is resumable instead of faster.
  Vectors are unit-length (norm 1.0000), so cosine similarity == dot product.

## ADR-013 — Embedding freshness by content hash; faculty `profile_text`
- **Context:** Works/faculty are reloaded whenever the audit rules or the CSV change; re-embedding 20k works
  costs 23 minutes, but silently keeping vectors of changed text would corrupt search.
- **Decision:** Store `embedding_hash` (sha256/16) of the embedded text next to each vector; the step embeds
  only rows whose hash differs (or all, if `meta.embedding_model` changed), commits per batch, and clears the
  vector when a faculty member no longer has any text. Work text = title + topic hierarchy + OpenAlex keywords +
  abstract (<=1,500 chars). Faculty text = expertise only (no name/position): ranked cleaned keywords + the topics
  of their matched works, stored as `faculty.profile_text` and full-text indexed (`faculty.tsv`) for hybrid search.
- **Consequences:** Idempotent, resumable, no stale vectors. Run order: `load_works` -> `load_faculty` ->
  `match_faculty` -> `embed` (faculty text depends on matched works).

## ADR-006 — Python backend + Next.js frontend
- **Context:** Data/LLM ecosystem is strongest in Python; grading emphasizes full-stack + UI.
- **Decision:** FastAPI backend, Next.js 15 + TS frontend, talking via typed REST + SSE.
- **Consequences:** Two runtimes; mitigated with Docker Compose and generated TS types from OpenAPI.

## ADR-008 — Sync SQLAlchemy + psycopg 3 for API and pipeline
- **Context:** psycopg async does not run on Windows' default Proactor event loop; a second async driver (asyncpg) would mean two URLs/drivers. Load is small (single-user demo).
- **Decision:** One driver (`postgresql+psycopg`), sync sessions; FastAPI `def` endpoints run in the threadpool. Async is used only at the HTTP edge (SSE streaming / future LLM calls), calling DB work via `run_in_threadpool`.
- **Consequences:** Simpler code and tests, identical behavior on Windows/Linux.

## ADR-009 — uv workspace (`backend` + `pipeline`)
- **Decision:** Root virtual project with workspace members `uiuc-backend` (package `app`) and `uiuc-pipeline` (package `pipeline`, src layout). Pipeline depends on backend to reuse `app.db.models` and settings — single source of truth for schema.
- **Consequences:** One lockfile/venv; API Docker image installs only `uiuc-backend`.

## ADR-010 — Key-free OpenAlex fetch: monthly slices + `page=` paging
- **Context:** OpenAlex requires an API key for `cursor=` paging beyond page 1 (verified 401). `page=` paging is key-free but capped at 10k results/query. User prefers $0 and no sign-up.
- **Decision:** Split the window into calendar months (each < 2.5k works), page each with `sort=publication_date:asc`, checkpoint after every page, verify unique ids vs `meta.count`. Same ~135 calls (~$0.014) as cursor paging.
- **Consequences:** Works with or without a key; resumable across days if the shared budget runs out. If a slice ever exceeds 10k, fetch aborts with a clear message (split smaller).

## ADR-011 — Faculty matching: bulk author directory + precision-first scoring
- **Context:** Per-name `/authors?search=` costs $0.001/call (10× a list call) and would blow the free budget for 999 people. OpenAlex fragments people into several ids and many UIUC people share names (e.g. 8 "Jiawei Han" ids at UIUC).
- **Decision:** Download the UIUC author directory once (works_count ≥ 5, 3 slices, ~$0.009) and match offline: name score (variants, nicknames, transliteration) × TF-IDF topic similarity, with thresholds calibrated on near-certain vs random pairs; ambiguous names require topical closeness to the best candidate; merged profiles (display name of another person) are never linked. Wrong links (someone else's papers on a profile) are worse than missing ones.
- **Consequences:** 92.4% matched, deterministic, explainable (`evidence` JSON). Unmatched are mostly people who left UIUC; no per-name fallback search. Lexical similarity may be revisited with embeddings in M3 if needed.

## ADR-012 — Data-audit corrections (scope, authorship model, versions, fairness)
- **Context:** Audit on real data (`.claude/docs/data-audit.md`) found coverage gaps (sub-units, truncated and unresolved authorships), version duplicates and field bias in the top-20k cut.
- **Decision:** (1) scope by authorship lineage + campus-checked system-only works; (2) complete truncated author lists via the free single-work endpoint, store first 100 + all UIUC authorships; (3) `authorships` keyed by byline position with nullable `author_id`; (4) version merge by DOI or title + shared author-name tokens, canonical = published version; (5) field-normalized citation percentile, neutral missing FWCI; (6) derived `faculty_works` incl. name-attributed unresolved authorships.
- **Consequences:** Every corpus work has a UIUC authorship; fairer cut; richer fields for the portal. Queries joining `authors` must handle NULL `author_id` (`IS NOT NULL` in `NOT IN` subqueries).

## ADR-014 — Portal statistics from the eligible set, with explicit data-quality rules
- **Context:** The loaded corpus (top 20k) over-represents recent works, so charts built from it would be wrong.
  The data also contains year-only dates (Jan 1), bulk auto-generated records and UIUC units that OpenAlex does not
  list under UIUC's lineage.
- **Decision:** Compute statistics from all works that pass the quality filters (23,694) and say so (`scope`,
  "indexed for the assistant: 20,000"). Monthly series use day-precision dates only (2,137 year-only dates reported
  separately). "Rising topics" compare *shares* between the two window halves and only consider works with an identified
  venue (otherwise ~90 species-description records made "Hemiptera Insect Studies" the top riser). UIUC-internal units
  (Woese Institute, Prairie Research Institute, Natural History Survey, Cancer Center at Illinois, CABBI, ...) are excluded
  from "collaborating institutions" when >= 90% of their appearances sit on the same authorship as UIUC (>= 15 works);
  genuine joint institutes stay (Zhejiang University 0.73). The excluded list is stored in the stats for inspection.
- **Consequences:** Honest numbers, explainable rules; a few small partners with near-1.0 ratios can be dropped
  (Dali University, 28 works). Venue platforms (Springer eBooks) are not ranked as venues.

## ADR-007 — Fixed snapshot date
- **Decision:** `SNAPSHOT_DATE=2026-11-16`, window start `2024-11-16`, stored in config and shown in UI ("data as of").
