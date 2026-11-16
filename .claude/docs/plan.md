# Plan & Progress

Source of truth for progress. Tick `[x]` when done; add a one-line note if scope changed.
Snapshot date for "last two years": **2026-11-16 → from_publication_date = 2024-11-16** (fixed in config for reproducibility).

## Milestone 0 — Scaffold (D1 morning)
- [x] Monorepo layout + git init (`backend/`, `pipeline/`, `docker/`); uv workspace (ADR-009)
- [x] `docker-compose.yml` with `pgvector/pgvector:pg16` + api (verified: container migrates & `/api/health` ok) — `web` service pending frontend
- [x] `Makefile`, `.env.example`, `.gitignore`, `.dockerignore`
- [x] Python tooling: ruff (+isort first-party), mypy strict, pytest (8 tests passing)
- [x] FastAPI skeleton: settings, sync DB session (ADR-008), `/api/health`, OpenAPI at `/api/docs`
- [x] Alembic `0001` initial schema (all tables + HNSW/GIN/trigram indexes); up/down/up verified, `alembic check` clean
- [ ] Next.js app + Tailwind + shadcn/ui — Node 26.13.1 + pnpm 12.21.0 now installed; scaffold pending

## Milestone 1 — OpenAlex ingestion (D1)
- [ ] `pipeline/fetch_works.py`: cursor paging, `per_page=200`, `select=` field list, retries on 429/5xx honoring `Retry-After`, resumable (store last cursor), writes `data/raw/works-*.jsonl`
- [ ] Fetch institution record + topic hierarchy
- [ ] Abstract reconstruction from `abstract_inverted_index`
- [ ] Quality filter + `rank_score`; keep top ~20k (rules in `openalex.md`)
- [ ] Load `works`, `authors`, `authorships`, `topics`, `work_topics`
- [ ] Data-quality report (counts dropped per rule) → `data/processed/quality_report.json`

## Milestone 2 — Faculty & matching (D2)
- [ ] Parse CSV (Postgres array keywords, NULL handling) → `faculty`
- [ ] Keyword cleaning (blocklist, IDF ranking) → `keywords_clean`
- [ ] Faculty ↔ OpenAlex author matching with confidence → `faculty_author_map`
- [ ] Budgeted fallback `/authors?search=` for unmatched
- [ ] Match-rate report (target ≥ 70% matched with confidence ≥ 0.7)

## Milestone 3 — Embeddings & stats (D2)
- [ ] Embed works (title + abstract + topics) and faculty (clean keywords + top topics)
- [ ] HNSW indexes; `tsvector` GIN indexes
- [ ] Precompute stats tables (KPIs, output by month, fields/subfields, rising topics, top venues, collaborators, SDGs)
- [ ] Institution metadata JSON (Wikipedia/Wikimedia Commons, with source URLs + licenses)

## Milestone 4 — Backend API (D3)
- [ ] Endpoints per `architecture.md` with Pydantic schemas + OpenAPI
- [ ] Hybrid search service (FTS + vector + RRF), unit-tested
- [ ] Faculty expertise search service
- [ ] Response caching for overview/stats
- [ ] Tests (pytest + test DB fixture)

## Milestone 5 — Portal UI (D3–D4)
- [ ] Sections per `frontend.md`
- [ ] Responsive, dark mode, loading skeletons, empty/error states
- [ ] Faculty drawer, paper list filters

## Milestone 6 — Assistant (D4–D5)
- [ ] `LLMProvider` interface + `none` provider (deterministic, no key)
- [ ] Tools: `search_papers`, `search_faculty`, `get_faculty_profile`, `get_stats`
- [ ] `/api/chat` SSE streaming, citations
- [ ] Chat panel UI with suggested prompts + clickable citations
- [ ] Golden set (`eval/golden.yaml`) + `make eval`; both required queries pass
- [ ] (Later) real LLM adapter once provider chosen — see ADR-004

## Milestone 7 — Polish & submit (D6)
- [ ] README: architecture diagram, decisions & trade-offs, data-quality + match-rate numbers, how to run
- [ ] Demo GIF/video
- [ ] Optional deploy (Vercel + Railway/Fly)

## Open questions
- LLM provider (ADR-004) — pending user decision.
- Deadline / deploy requirement — pending.
