"""QUERY and EVALUATE operations."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from lightrag import LightRAG, QueryParam
from wikigraph.config import WikiGraphConfig
from wikigraph.state import EntityMeta, QueryLogEntry, WikiGraphState


async def query_node(state: WikiGraphState, rag: LightRAG) -> dict:
    """Query the knowledge graph and log retrieved entities/relations."""
    query = state.get("current_query", "")
    if not query:
        return {"messages": ["QUERY: empty query"]}

    result = await rag.aquery_data(query, param=QueryParam(mode="mix"))

    entities = []
    relations = []
    chunks = []
    if result.get("status") == "success":
        data = result.get("data", {})
        entities = [e.get("entity_name", "") for e in data.get("entities", [])]
        relations = [
            (r.get("src_id", ""), r.get("tgt_id", ""))
            for r in data.get("relationships", [])
        ]
        chunks = [c.get("chunk_id", "") for c in data.get("chunks", []) if c.get("chunk_id")]

    now = datetime.now(timezone.utc).isoformat()
    entry = QueryLogEntry(
        query=query,
        timestamp=now,
        retrieved_entities=entities,
        retrieved_relations=relations,
        retrieved_chunks=chunks,
        mode="mix",
    )

    query_log = state.get("query_log", [])
    query_log.append(asdict(entry))

    entity_meta = state.get("entity_metadata", {})
    for ename in entities:
        if ename not in entity_meta:
            entity_meta[ename] = asdict(EntityMeta(entity_name=ename))
        entity_meta[ename]["access_count"] = entity_meta[ename].get("access_count", 0) + 1
        entity_meta[ename]["last_accessed"] = now

    msg = f"QUERY: '{query}' → {len(entities)} entities, {len(relations)} relations, {len(chunks)} chunks"
    return {
        "query_result": result,
        "query_log": query_log,
        "entity_metadata": entity_meta,
        "messages": [msg],
    }


def evaluate_node(state: WikiGraphState, config: WikiGraphConfig) -> dict:
    """Evaluate query result quality using heuristics (no LLM call)."""
    result = state.get("query_result")
    if not result or result.get("status") != "success":
        return {
            "should_evolve": True,
            "messages": ["EVALUATE: no result, triggering EVOLVE"],
        }

    data = result.get("data", {})
    n_entities = len(data.get("entities", []))
    n_relations = len(data.get("relationships", []))
    n_chunks = len(data.get("chunks", []))

    if n_entities == 0 and n_chunks == 0:
        quality = 0.0
    elif n_entities > 0 and n_relations == 0:
        quality = 0.3
    elif n_chunks > 0 and n_entities == 0:
        quality = 0.4
    else:
        quality = min(1.0, 0.5 + 0.1 * n_entities + 0.05 * n_relations)

    # Update quality in last query log entry
    query_log = state.get("query_log", [])
    if query_log:
        query_log[-1]["result_quality"] = quality

    already_flagged = state.get("should_evolve", False)
    should_evolve = quality < config.quality_evolve_threshold or already_flagged
    reason = "low quality" if quality < config.quality_evolve_threshold else ("scheduled" if already_flagged else "no")
    msg = f"EVALUATE: quality={quality:.2f}, evolve={reason.upper()}"
    return {
        "should_evolve": should_evolve,
        "query_log": query_log,
        "messages": [msg],
    }
