---
paths:
  - "backend/app/assistant/**"
  - "eval/**"
---

# Assistant rules

- Read `.claude/docs/llm-assistant.md` first.
- LLM provider is undecided (ADR-004): code only against `LLMProvider`; vendor SDK imports are allowed ONLY in `providers/<vendor>.py`, imported lazily.
- `none` provider must keep working and keep passing `make eval` — it is the default and the test oracle.
- Tool schemas are neutral JSON Schema defined once in `tools.py`; adapters translate.
- Answers may only reference ids returned by tools in the same turn; validate citations before streaming the `citations` event.
- Any change to retrieval/ranking must be followed by `make eval`; report metric deltas.
