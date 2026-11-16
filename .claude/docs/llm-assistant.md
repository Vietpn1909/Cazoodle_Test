# Domain LLM Assistant

## Status
LLM provider **not chosen** (ADR-004). Everything must work with `LLM_PROVIDER=none`. Real providers are added later as adapters only.

## Layering
```
backend/app/assistant/
  orchestrator.py   # loop: provider decides → run tools → provider writes answer; emits SSE events
  tools.py          # tool registry: neutral JSON-schema definitions + Python implementations
  prompts.py        # system prompt + answer formatting rules (provider-neutral)
  providers/
    base.py         # LLMProvider protocol
    none.py         # deterministic router + templated answers (no network, no key)
    # later: anthropic.py / openai_compat.py (covers Ollama, vLLM, LM Studio) / gemini.py
```
`LLMProvider` protocol (sketch):
```python
class LLMProvider(Protocol):
    name: str

    async def step(
        self, messages: list[Msg], tools: list[ToolSpec]
    ) -> AsyncIterator[ProviderEvent]:
        """Yield text tokens and/or tool-call requests for one model turn."""
```
- Tool schemas are defined once (neutral JSON Schema); each adapter translates to its vendor format.
- Vendor SDK imports live ONLY inside their adapter module; adapters are lazily imported by `LLM_PROVIDER`.
- Provider config: `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`.

## `none` provider (default)
- Intent router (rules + embedding similarity to intent exemplars): `faculty_search` if query mentions faculty/professor/researcher/"who works on"; else `paper_search`.
- Extract params: `sort_by=date` if "recent/latest/new"; `sort_by=cited` if "top/most cited/notable"; year/date hints → `date_from`.
- Strip stop phrases ("what are", "UIUC papers on", "which faculty members work on") to form the topical query.
- Answer = templated markdown list with citations. Good enough to pass both required queries and serves as a test oracle.

## Tools
| Tool | Args | Returns |
|---|---|---|
| `search_papers` | `query: str`, `sort_by: relevance\|date\|cited = relevance`, `date_from?: date`, `date_to?: date`, `field?: str`, `limit: int = 10` | works: id, title, date, venue, uiuc_authors, doi/url, snippet, score |
| `search_faculty` | `query: str`, `limit: int = 10` | faculty: id, name, position, matched keywords, evidence works (≤3), score |
| `get_faculty_profile` | `faculty_id` | profile + recent works |
| `get_stats` | `metric: overview\|fields\|rising_topics\|collaborators` | precomputed stats |

## Retrieval
- **Hybrid:** Postgres FTS (`websearch_to_tsquery`, `ts_rank_cd`) top 100 + pgvector cosine top 100 → Reciprocal Rank Fusion (k=60).
- **Relevance gate before date sort:** for `sort_by=date`, keep candidates with fused score ≥ threshold (or top-N by relevance, N=30) THEN sort by `publication_date desc`. Never sort the whole corpus by date.
- Query expansion (optional, cheap): small synonym map for domain terms (e.g. "war conflict" → military, warfare, armed conflict, defense, autonomous weapons).
- Optional reranker (`BAAI/bge-reranker-base`) if time permits.
- **Faculty score** = 0.5·cos(query, faculty embedding) + 0.5·mean(top-3 cos(query, their UIUC works)); return evidence works.

## Answer rules (all providers)
- Use only tool results; every paper/faculty mentioned must carry a citation id that exists in results.
- If nothing relevant passes the gate, say so and suggest nearby topics.
- Show dates for "recent" questions; show matched keywords/evidence papers for faculty questions.
- Keep answers concise; markdown lists.

## Evaluation (`eval/golden.yaml`, `make eval`)
- 15–20 questions: the 2 required + variants (recent/top/faculty/stats/out-of-scope).
- Per question: expected tool + params, and expected-relevant ids or keywords (judged once manually).
- Metrics: tool/param accuracy, recall@10 of retrieval, citation validity (100% required), latency.
- Both required queries must pass on `none` provider before any real LLM is added.
