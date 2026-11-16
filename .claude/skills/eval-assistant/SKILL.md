---
name: eval-assistant
description: Evaluate the research assistant against the golden question set (including the two required queries) and report metrics and failures. Use after changes to retrieval, tools, routing, prompts or LLM provider.
---

# Evaluate the assistant

1. Confirm DB is populated (works + faculty + embeddings). If not, suggest running the `etl` skill.
2. Note the active `LLM_PROVIDER` (default `none`).
3. Run `make eval` (fallback: `uv run python -m eval.run`).
4. Report a table: tool/intent accuracy, recall@10, citation validity (must be 100%), p50/p95 latency.
5. Show full answers for the two required queries and judge them:
   - "What are the most recent UIUC papers on AI in war conflict?" — relevant + newest first + dates shown.
   - "Which faculty members work on Computer Vision in Healthcare?" — relevant faculty + evidence papers.
6. For failures, delegate deep diagnosis to the `retrieval-evaluator` agent if needed.
7. If metrics changed vs the previous run (keep last results in `eval/results/latest.json`), show the delta.
