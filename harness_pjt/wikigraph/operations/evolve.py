"""EVOLVE operation: query-driven knowledge evolution."""

from __future__ import annotations

from collections import Counter

from lightrag import LightRAG
from wikigraph.config import WikiGraphConfig
from wikigraph.state import QueryLogEntry, WikiGraphState


def _find_co_retrieved_pairs(
    log_entries: list[dict], min_count: int
) -> list[tuple[str, str, int]]:
    """Find entity pairs that appear together in min_count+ queries."""
    pair_counts: Counter = Counter()
    for entry in log_entries:
        entities = entry.get("retrieved_entities", [])
        for i in range(len(entities)):
            for j in range(i + 1, len(entities)):
                pair = tuple(sorted((entities[i], entities[j])))
                pair_counts[pair] += 1
    return [(a, b, c) for (a, b), c in pair_counts.items() if c >= min_count]


def _find_failed_queries(log_entries: list[dict], threshold: float) -> list[str]:
    """Find queries with quality below threshold."""
    return [
        e["query"]
        for e in log_entries
        if e.get("result_quality") is not None and e["result_quality"] < threshold
    ]


def _find_shortcut_paths(
    log_entries: list[dict], min_count: int
) -> list[tuple[str, str, str]]:
    """Find A-B-C patterns where A→B and B→C are co-retrieved but A→C is not."""
    from collections import defaultdict

    path_counts: Counter = Counter()
    for entry in log_entries:
        rels = entry.get("retrieved_relations", [])
        adj: dict[str, set[str]] = defaultdict(set)
        for src, tgt in rels:
            adj[src].add(tgt)
            adj[tgt].add(src)
        rel_set = {(s, t) for s, t in rels} | {(t, s) for s, t in rels}
        for b in adj:
            neighbors = list(adj[b])
            for i in range(len(neighbors)):
                for j in range(i + 1, len(neighbors)):
                    a, c = tuple(sorted((neighbors[i], neighbors[j])))
                    if (a, c) not in rel_set and (c, a) not in rel_set:
                        path_counts[(a, b, c)] += 1
    return [(a, b, c) for (a, b, c), cnt in path_counts.items() if cnt >= min_count]


def _parse_gap_fill_response(response: str) -> tuple[dict | None, dict | None]:
    """Parse LLM response for gap-fill: ENTITY: name|type|desc / RELATIONSHIP: src|tgt|desc."""
    entity = None
    rel = None
    for line in response.strip().split("\n"):
        line = line.strip()
        if line.upper().startswith("ENTITY:"):
            parts = line[len("ENTITY:"):].strip().split("|")
            if len(parts) >= 3:
                entity = {
                    "entity_name": parts[0].strip(),
                    "entity_type": parts[1].strip(),
                    "description": parts[2].strip(),
                    "source_id": "wikigraph_evolve",
                }
        elif line.upper().startswith("RELATIONSHIP:"):
            parts = line[len("RELATIONSHIP:"):].strip().split("|")
            if len(parts) >= 3:
                rel = {
                    "src_id": parts[0].strip(),
                    "tgt_id": parts[1].strip(),
                    "description": parts[2].strip(),
                    "keywords": "gap-fill",
                    "weight": 0.5,
                    "source_id": "wikigraph_evolve",
                }
    return entity, rel


async def evolve_node(
    state: WikiGraphState,
    rag: LightRAG,
    config: WikiGraphConfig,
    llm_func=None,
) -> dict:
    """Analyze query logs and inject evolved knowledge."""
    query_log = state.get("query_log", [])
    mutations: list[dict] = []
    messages: list[str] = []
    graph = rag.chunk_entity_relation_graph

    # Strategy 1: Strengthen co-retrieved connections
    pairs = _find_co_retrieved_pairs(query_log, config.co_retrieval_min_count)
    for a, b, count in pairs[: config.evolve_max_mutations]:
        has = await graph.has_edge(a, b)
        if not has:
            desc = f"Frequently co-retrieved ({count} times)"
            if llm_func:
                node_a = await graph.get_node(a)
                node_b = await graph.get_node(b)
                if node_a and node_b:
                    prompt = (
                        f"Entity A: {a} - {node_a.get('description', '')[:200]}\n"
                        f"Entity B: {b} - {node_b.get('description', '')[:200]}\n"
                        f"These entities are frequently retrieved together. "
                        f"Describe their relationship in one sentence."
                    )
                    try:
                        desc = await llm_func(prompt)
                    except Exception:
                        pass
            mutations.append({
                "type": "co_retrieval",
                "relationship": {
                    "src_id": a,
                    "tgt_id": b,
                    "description": desc,
                    "keywords": "co-retrieved",
                    "weight": config.inferred_edge_weight,
                    "source_id": "wikigraph_evolve",
                },
            })
            messages.append(f"EVOLVE: co-retrieval edge {a} → {b} (count={count})")

    # Strategy 2: Fill knowledge gaps from failed queries
    failed = _find_failed_queries(query_log, config.gap_quality_threshold)
    if failed and llm_func and len(mutations) < config.evolve_max_mutations:
        for fq in failed[:2]:
            prompt = (
                f"The query '{fq}' returned poor results from our knowledge graph. "
                f"Suggest one entity (name + type + description) and one relationship "
                f"that should exist to answer this query. "
                f"Format exactly:\n"
                f"ENTITY: name | type | description\n"
                f"RELATIONSHIP: src | tgt | description"
            )
            try:
                response = await llm_func(prompt)
                entity, rel = _parse_gap_fill_response(response)
                if entity:
                    mutations.append({"type": "gap_fill", "entity": entity})
                if rel:
                    mutations.append({"type": "gap_fill", "relationship": rel})
                messages.append(f"EVOLVE: gap-fill for '{fq[:50]}' → entity={bool(entity)}, rel={bool(rel)}")
            except Exception:
                pass

    # Strategy 3: Create shortcut edges for frequent multi-hop paths
    shortcuts = _find_shortcut_paths(query_log, config.shortcut_path_min_count)
    for a, b, c in shortcuts[: config.evolve_max_mutations - len(mutations)]:
        edge_ab = await graph.get_edge(a, b)
        edge_bc = await graph.get_edge(b, c)
        desc_parts = []
        if edge_ab:
            desc_parts.append(edge_ab.get("description", "")[:100])
        if edge_bc:
            desc_parts.append(edge_bc.get("description", "")[:100])
        desc = f"Shortcut via {b}: {' → '.join(desc_parts)}" if desc_parts else f"Shortcut via {b}"

        mutations.append({
            "type": "shortcut",
            "relationship": {
                "src_id": a,
                "tgt_id": c,
                "description": desc,
                "keywords": f"shortcut,via_{b}",
                "weight": config.inferred_edge_weight,
                "source_id": "wikigraph_evolve",
            },
        })
        messages.append(f"EVOLVE: shortcut {a} → {c} (via {b})")

    # Apply mutations via ainsert_custom_kg
    rels_to_inject = [m["relationship"] for m in mutations if "relationship" in m]
    ents_to_inject = [m["entity"] for m in mutations if "entity" in m]
    total_injected = 0
    if rels_to_inject or ents_to_inject:
        try:
            await rag.ainsert_custom_kg({
                "chunks": [],
                "entities": ents_to_inject,
                "relationships": rels_to_inject,
            })
            total_injected = len(rels_to_inject) + len(ents_to_inject)
            messages.append(
                f"EVOLVE: injected {len(ents_to_inject)} entities + {len(rels_to_inject)} relationships"
            )
        except Exception as e:
            messages.append(f"EVOLVE: injection failed: {e}")

    return {
        "evolve_mutations": mutations,
        "evolve_applied": total_injected,
        "messages": messages,
    }
