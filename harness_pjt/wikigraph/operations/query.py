"""RESPOND (foreground) and RETRIEVE/EVALUATE (background) operations.

RESPOND is the user-facing path: it takes the query already optimized by
ANALYZE and asks LightRAG for a synthesized answer. It is awaited directly
by ``WikiGraphAgent.query()`` and returned to the caller immediately.

RESPOND also does query-time document scoping: if ANALYZE flagged that the
question names specific document(s) (``mentioned_docs``), and structural
mining (evolve.py) has already mined a matching ``doc::`` node for at least
one of them, RESPOND seeds ``QueryParam.ll_keywords`` with the entities that
document CONTAINS. LightRAG skips its own keyword-extraction step whenever
``ll_keywords``/``hl_keywords`` are pre-set (see ``operate.py``, the
`kg_query` keyword-extraction guard), so this biases retrieval toward that
document's entities instead of searching the whole graph. Only applied to
RESPOND, not RETRIEVE below — RETRIEVE's job is broad discovery for EVOLVE
(finding gaps/co-retrieval/shortcuts across the *whole* graph), and scoping
it down would hide exactly the cross-document signal EVOLVE looks for. If
structural mining hasn't run yet (or the mention doesn't match any document
node), this silently falls back to an unscoped query — fail-safe, same as
ANALYZE's no-``llm_func`` passthrough.

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


async def _match_document_nodes(rag: LightRAG, mentioned_docs: list[str]) -> list[str]:
    """Fuzzy-match ANALYZE's raw doc mentions against real ``doc::`` nodes."""
    if not mentioned_docs:
        return []
    graph = rag.chunk_entity_relation_graph
    needles = [d.strip().lower() for d in mentioned_docs if d.strip()]
    if not needles:
        return []
    matched: list[str] = []
    for name in await graph.get_all_labels():
        if not name.startswith("doc::"):
            continue
        node = await graph.get_node(name)
        haystack = f"{name} {node.get('description', '') if node else ''}".lower()
        if any(needle in haystack for needle in needles):
            matched.append(name)
    return matched


async def _scoped_entities(rag: LightRAG, doc_node_ids: list[str], max_entities: int) -> list[str]:
    """Entities CONTAINS-linked to the matched document node(s), for ll_keywords."""
    if not doc_node_ids:
        return []
    graph = rag.chunk_entity_relation_graph
    entities: list[str] = []
    for doc_name in doc_node_ids:
        edges = await graph.get_node_edges(doc_name) or []
        for src, tgt in edges:
            other = tgt if src == doc_name else src
            if other.startswith("doc::") or other in entities:
                continue
            entities.append(other)
            if len(entities) >= max_entities:
                return entities
    return entities


async def respond_node(
    state: WikiGraphState, rag: LightRAG, config: WikiGraphConfig | None = None
) -> dict:
    """Foreground: answer the user using the query ANALYZE already optimized."""
    query = state.get("optimized_query") or state.get("current_query", "")
    if not query:
        return {"final_answer": "", "messages": ["RESPOND: empty query"]}

    param = QueryParam(mode="mix")
    messages: list[str] = []
    mentioned_docs = state.get("mentioned_docs") or []
    if mentioned_docs and config is not None:
        doc_nodes = await _match_document_nodes(rag, mentioned_docs)
        scoped_entities = await _scoped_entities(rag, doc_nodes, config.doc_scope_max_entities)
        if scoped_entities:
            param = QueryParam(mode="mix", ll_keywords=scoped_entities)
            messages.append(
                f"RESPOND: scoped to {len(doc_nodes)} document(s) "
                f"({len(scoped_entities)} entity keyword(s)) for {mentioned_docs}"
            )

    answer = await rag.aquery(query, param=param)
    return {
        "final_answer": answer,
        "messages": messages + [f"RESPOND: answered '{query[:60]}'"],
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
