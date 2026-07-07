"""RESPOND (foreground) and RETRIEVE/EVALUATE (background) operations.

RESPOND is the user-facing path: it takes the query already optimized by
ANALYZE and asks LightRAG for a synthesized answer. It is awaited directly
by ``WikiGraphAgent.query()`` and returned to the caller immediately.

RETRIEVE/EVALUATE are the first two steps of the background evolving path:
each sub-query from ANALYZE is retrieved and logged individually so that
EVOLVE can later mine patterns (co-retrieval, gaps, shortcuts) across them.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from lightrag import LightRAG, QueryParam
from wikigraph.config import WikiGraphConfig
from wikigraph.state import EntityMeta, QueryLogEntry, WikiGraphState


async def respond_node(state: WikiGraphState, rag: LightRAG) -> dict:
    """Foreground: answer the user using the query ANALYZE already optimized."""
    query = state.get("optimized_query") or state.get("current_query", "")
    if not query:
        return {"final_answer": "", "messages": ["RESPOND: empty query"]}

    answer = await rag.aquery(query, param=QueryParam(mode="mix"))
    return {
        "final_answer": answer,
        "messages": [f"RESPOND: answered '{query[:60]}'"],
    }


async def retrieve_node(state: WikiGraphState, rag: LightRAG) -> dict:
    """Background: retrieve + log each sub-query for graph-evolution analysis."""
    parent_query = state.get("current_query", "")
    sub_queries = state.get("sub_queries") or ([parent_query] if parent_query else [])
    if not sub_queries:
        return {"messages": ["RETRIEVE: no sub-queries"]}

    now = datetime.now(timezone.utc).isoformat()
    query_log = state.get("query_log", [])
    entity_meta = state.get("entity_metadata", {})
    sub_query_results: list[dict] = []
    messages: list[str] = []

    for sub_query in sub_queries:
        result = await rag.aquery_data(sub_query, param=QueryParam(mode="mix"))

        entities: list[str] = []
        relations: list[tuple[str, str]] = []
        chunks: list[str] = []
        if result.get("status") == "success":
            data = result.get("data", {})
            entities = [e.get("entity_name", "") for e in data.get("entities", [])]
            relations = [
                (r.get("src_id", ""), r.get("tgt_id", ""))
                for r in data.get("relationships", [])
            ]
            chunks = [
                c.get("chunk_id", "") for c in data.get("chunks", []) if c.get("chunk_id")
            ]

        entry = QueryLogEntry(
            query=sub_query,
            parent_query=parent_query,
            timestamp=now,
            retrieved_entities=entities,
            retrieved_relations=relations,
            retrieved_chunks=chunks,
            mode="mix",
        )
        query_log.append(asdict(entry))
        sub_query_results.append(asdict(entry))

        for ename in entities:
            if ename not in entity_meta:
                entity_meta[ename] = asdict(EntityMeta(entity_name=ename))
            entity_meta[ename]["access_count"] = entity_meta[ename].get("access_count", 0) + 1
            entity_meta[ename]["last_accessed"] = now

        messages.append(
            f"RETRIEVE: '{sub_query[:40]}' → {len(entities)} entities, "
            f"{len(relations)} relations, {len(chunks)} chunks"
        )

    return {
        "sub_query_results": sub_query_results,
        "query_log": query_log,
        "entity_metadata": entity_meta,
        "messages": messages,
    }


def _score_quality(n_entities: int, n_relations: int, n_chunks: int) -> float:
    if n_entities == 0 and n_chunks == 0:
        return 0.0
    if n_entities > 0 and n_relations == 0:
        return 0.3
    if n_chunks > 0 and n_entities == 0:
        return 0.4
    return min(1.0, 0.5 + 0.1 * n_entities + 0.05 * n_relations)


def evaluate_node(state: WikiGraphState, config: WikiGraphConfig) -> dict:
    """Score this turn's sub-query results using heuristics (no LLM call)."""
    sub_query_results = state.get("sub_query_results", [])
    if not sub_query_results:
        return {"should_evolve": False, "messages": ["EVALUATE: nothing to score"]}

    query_log = state.get("query_log", [])
    n_new = len(sub_query_results)
    qualities: list[float] = []
    for entry in query_log[-n_new:]:
        quality = _score_quality(
            len(entry.get("retrieved_entities", [])),
            len(entry.get("retrieved_relations", [])),
            len(entry.get("retrieved_chunks", [])),
        )
        entry["result_quality"] = quality
        qualities.append(quality)

    avg_quality = sum(qualities) / len(qualities)
    should_evolve = avg_quality < config.quality_evolve_threshold
    msg = f"EVALUATE: avg quality={avg_quality:.2f} across {n_new} sub-quer{'y' if n_new == 1 else 'ies'}"
    return {
        "should_evolve": should_evolve,
        "query_log": query_log,
        "messages": [msg],
    }
