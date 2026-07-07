"""ANALYZE operation: query improvement via rewrite + decomposition.

This is the one step of the evolving pipeline that runs inline (not in the
background), because its output feeds both the foreground response path
(the rewritten `optimized_query` is sent to LightRAG for an immediate
answer) and the background evolving path (`sub_queries` are retrieved and
analyzed individually to improve the graph).

It also flags explicit document/file mentions (`mentioned_docs`) in the
same LLM call. RESPOND (query.py) uses this to scope retrieval to the
matching document node(s) mined by structural mining (evolve.py) — see
the "전략 선택" section of README.md.
"""

from __future__ import annotations

from wikigraph.config import WikiGraphConfig
from wikigraph.state import WikiGraphState

_PROMPT_TEMPLATE = """Given a user question, do three things:

1. Rewrite it as a single, self-contained search query optimized for retrieval \
(resolve ambiguity, expand abbreviations, drop conversational filler). If it is \
already optimal, repeat it unchanged.
2. If the question actually bundles multiple distinct information needs, split \
it into up to {max_subqueries} independent sub-queries that can be retrieved \
separately. If it is a single focused need, just repeat the rewritten query as \
the only sub-query.
3. If the question explicitly names specific document(s)/file(s) (by title, \
filename, or a clear reference like "the Q3 report"), list them comma-separated. \
Otherwise write NONE.

Respond in exactly this format, one item per line, no extra commentary:
OPTIMIZED: <rewritten single query>
SUB1: <sub-query 1>
SUB2: <sub-query 2> (omit if not needed)
SUB3: <sub-query 3> (omit if not needed)
DOCS: <comma-separated document/file names, or NONE>

Question: {question}
"""


def _build_prompt(question: str, max_subqueries: int) -> str:
    return _PROMPT_TEMPLATE.format(question=question, max_subqueries=max_subqueries)


def _parse_analysis(
    response: str, fallback_query: str, max_subqueries: int
) -> tuple[str, list[str], list[str]]:
    optimized = fallback_query
    sub_queries: list[str] = []
    mentioned_docs: list[str] = []
    for line in response.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("OPTIMIZED:"):
            value = line.split(":", 1)[1].strip()
            if value:
                optimized = value
        elif upper.startswith("DOCS:"):
            value = line.split(":", 1)[1].strip()
            if value and value.upper() != "NONE":
                mentioned_docs = [d.strip() for d in value.split(",") if d.strip()]
        elif upper.startswith("SUB"):
            _, _, rest = line.partition(":")
            value = rest.strip()
            if value:
                sub_queries.append(value)
    sub_queries = sub_queries[:max_subqueries]
    if not sub_queries:
        sub_queries = [optimized]
    return optimized, sub_queries, mentioned_docs


async def analyze_node(
    state: WikiGraphState, config: WikiGraphConfig, llm_func=None
) -> dict:
    """Rewrite the query for retrieval and split it if it bundles multiple needs."""
    query = state.get("current_query", "")
    if not query:
        return {"messages": ["ANALYZE: empty query"]}

    if not llm_func:
        return {
            "optimized_query": query,
            "sub_queries": [query],
            "mentioned_docs": [],
            "messages": ["ANALYZE: no llm_func configured, passthrough"],
        }

    prompt = _build_prompt(query, config.query_decompose_max_subqueries)
    try:
        response = await llm_func(prompt)
        optimized, sub_queries, mentioned_docs = _parse_analysis(
            response, query, config.query_decompose_max_subqueries
        )
    except Exception:
        optimized, sub_queries, mentioned_docs = query, [query], []

    plural = "y" if len(sub_queries) == 1 else "ies"
    msg = f"ANALYZE: optimized query + {len(sub_queries)} sub-quer{plural}"
    if mentioned_docs:
        msg += f", references {len(mentioned_docs)} document(s)"
    return {
        "optimized_query": optimized,
        "sub_queries": sub_queries,
        "mentioned_docs": mentioned_docs,
        "messages": [msg],
    }
