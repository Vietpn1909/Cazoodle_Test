---
paths:
  - "pipeline/**"
  - "data/**"
---

# Data pipeline rules

- Read `.claude/docs/openalex.md` and `.claude/docs/faculty-data.md` before changing ingestion or matching.
- Every step is an idempotent CLI command (`uv run python -m pipeline.<step>`), safe to re-run; upsert, never blind insert.
- Network steps write raw responses to `data/raw/` first; transform steps read only from `data/raw/` (no network). Re-running transforms must cost $0 of OpenAlex budget.
- Fetching must be resumable (persist cursor/progress), honor `Retry-After`, log `X-RateLimit-Remaining-USD`.
- Always use `select=` and `per_page=200`; no per-record API loops without a cache and an explicit call cap.
- Each step prints/writes a short report (rows in/out, dropped per rule, match rates) to `data/processed/reports/`.
- `illinois-researchers.csv` is read-only input.
- Snapshot window comes from config (`SNAPSHOT_DATE`), never `date.today()`.
