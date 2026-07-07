"""EVOLVE operation: query-driven knowledge-graph improvement.

Tagging note: ``rag.ainsert_custom_kg()`` resolves a relationship's
``source_id`` field by looking it up in a map built from the ``chunks`` we
pass alongside it. Since EVOLVE injects no chunks, that lookup always misses
and every injected edge's stored ``source_id`` ends up ``"UNKNOWN"`` — not
the tag we set. ``keywords`` has no such remapping, so it is the reliable
place to mark an edge's origin: every EVOLVE-created edge carries
``"wikigraph_evolve"`` as the first keyword, and every structural edge (see
``structural.py``) carries ``"wikigraph_structural"``. Source verification
and LINT's stale-evolved-edge check both key off ``keywords``, not
``source_id``, for exactly this reason.

Two tiers, both part of the background evolving path:

- Tier 1 (``evolve_light_node``): runs after every query. Cheap and
  incremental — co-retrieval strengthening, gap filling, shortcut paths —
  scoped to the current query log.
- Tier 2 (``batch_evolve_node``): runs every ``batch_evolve_interval``
  queries (default 50). Comprehensive — re-runs the tier 1 strategies with
  a wider net and a larger mutation budget, plus the corpus-wide checks
  (source verification, contradiction resolution) that scan every entity
  and are too costly to run on every single query.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from lightrag import LightRAG
from wikigraph.config import WikiGraphConfig
from wikigraph.state import WikiGraphState


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


def _find_shortcut_paths(
    log_entries: list[dict], min_count: int
) -> list[tuple[str, str, str]]:
    """Find A-B-C patterns where A-B and B-C are co-retrieved but A-C is not."""
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
    """Parse LLM response: ENTITY: name|type|desc / RELATIONSHIP: src|tgt|desc."""
    entity = None
    rel = None
    for line in response.strip().split("\n"):
        line = line.strip()
        if line.upper().startswith("ENTITY:"):
            parts = line[len("ENTITY:") :].strip().split("|")
            if len(parts) >= 3:
                entity = {
                    "entity_name": parts[0].strip(),
                    "entity_type": parts[1].strip(),
                    "description": parts[2].strip(),
                    "source_id": "wikigraph_evolve",
                }
        elif line.upper().startswith("RELATIONSHIP:"):
            parts = line[len("RELATIONSHIP:") :].strip().split("|")
            if len(parts) >= 3:
                rel = {
                    "src_id": parts[0].strip(),
                    "tgt_id": parts[1].strip(),
                    "description": parts[2].strip(),
                    "keywords": "wikigraph_evolve,gap-fill",
                    "weight": 0.5,
                    "source_id": "wikigraph_evolve",
                }
    return entity, rel


async def _run_incremental_strategies(
    query_log: list[dict],
    rag: LightRAG,
    config: WikiGraphConfig,
    min_co_count: int,
    min_shortcut_count: int,
    max_mutations: int,
    llm_func,
) -> tuple[list[dict], list[str]]:
    """Co-retrieval strengthening + gap filling + shortcut paths.

    Shared by tier 1 (per-query, tight thresholds) and tier 2 (batch, wider
    net) — only the thresholds and budget differ between tiers.
    """
    graph = rag.chunk_entity_relation_graph
    mutations: list[dict] = []
    messages: list[str] = []

    # Strategy: strengthen co-retrieved connections
    pairs = _find_co_retrieved_pairs(query_log, min_co_count)
    for a, b, count in pairs:
        if len(mutations) >= max_mutations:
            break
        has_ab = await graph.has_edge(a, b)
        has_ba = await graph.has_edge(b, a)
        if has_ab or has_ba:
            continue
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
                "keywords": "wikigraph_evolve,co-retrieved",
                "weight": config.inferred_edge_weight,
                "source_id": "wikigraph_evolve",
            },
        })
        messages.append(f"EVOLVE: co-retrieval edge {a} -> {b} (count={count})")

    # Strategy: fill knowledge gaps — only when raw chunks have evidence.
    # Trigger condition is checked directly (chunks retrieved, no entities),
    # not via the quality score, so it never depends on quality thresholds.
    gap_entries = [
        e for e in query_log
        if e.get("retrieved_chunks") and not e.get("retrieved_entities")
    ]
    empty_entries = [
        e for e in query_log
        if not e.get("retrieved_chunks") and not e.get("retrieved_entities")
        and e.get("result_quality") is not None
        and e["result_quality"] < config.gap_quality_threshold
    ]
    for entry in empty_entries[:2]:
        messages.append(
            f"EVOLVE: gap detected for '{entry.get('query', '')[:40]}' — "
            f"no raw data found, consider adding relevant documents"
        )
    if gap_entries and llm_func and len(mutations) < max_mutations:
        text_chunks_data = getattr(rag.text_chunks, "_data", {}) if hasattr(rag, "text_chunks") else {}
        for entry in gap_entries[:2]:
            if len(mutations) >= max_mutations:
                break
            fq = entry.get("query", "")
            chunks_found = entry.get("retrieved_chunks", [])
            chunk_texts = []
            for cid in chunks_found[:3]:
                cval = text_chunks_data.get(cid, {})
                if isinstance(cval, dict) and cval.get("content"):
                    chunk_texts.append(cval["content"][:500])

            if chunk_texts:
                context = "\n---\n".join(chunk_texts)
                prompt = (
                    f"The following text chunks are relevant to the query '{fq}' "
                    f"but no entities/relationships were extracted from them.\n\n"
                    f"Text:\n{context}\n\n"
                    f"Extract one entity and one relationship from this text.\n"
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
                    messages.append(
                        f"EVOLVE: gap-fill from chunks for '{fq[:40]}' "
                        f"-> entity={bool(entity)}, rel={bool(rel)}"
                    )
                except Exception:
                    pass

    # Strategy: shortcut edges for frequent multi-hop paths
    shortcuts = _find_shortcut_paths(query_log, min_shortcut_count)
    for a, b, c in shortcuts:
        if len(mutations) >= max_mutations:
            break
        has_ac = await graph.has_edge(a, c) or await graph.has_edge(c, a)
        if has_ac:
            continue
        edge_ab = await graph.get_edge(a, b) or await graph.get_edge(b, a)
        edge_bc = await graph.get_edge(b, c) or await graph.get_edge(c, b)
        desc_parts = []
        if edge_ab:
            desc_parts.append(edge_ab.get("description", "")[:100])
        if edge_bc:
            desc_parts.append(edge_bc.get("description", "")[:100])
        desc = f"Shortcut via {b}: {' -> '.join(desc_parts)}" if desc_parts else f"Shortcut via {b}"
        mutations.append({
            "type": "shortcut",
            "relationship": {
                "src_id": a,
                "tgt_id": c,
                "description": desc,
                "keywords": f"wikigraph_evolve,shortcut,via_{b}",
                "weight": config.inferred_edge_weight,
                "source_id": "wikigraph_evolve",
            },
        })
        messages.append(f"EVOLVE: shortcut {a} -> {c} (via {b})")

    return mutations, messages


async def _source_verification(
    rag: LightRAG, config: WikiGraphConfig
) -> tuple[int, list[str]]:
    """Remove relations whose source chunks no longer exist in text_chunks."""
    graph = rag.chunk_entity_relation_graph
    messages: list[str] = []
    removed_edges = 0
    try:
        all_labels = await graph.get_all_labels()
        text_chunks_data = getattr(rag.text_chunks, "_data", {}) if hasattr(rag, "text_chunks") else {}
        if text_chunks_data:
            for node_id in all_labels:
                edges = await graph.get_node_edges(node_id)
                if not edges:
                    continue
                for src, tgt in edges:
                    if src != node_id:
                        continue
                    edge = await graph.get_edge(src, tgt)
                    if not edge:
                        continue
                    keywords = edge.get("keywords", "")
                    if "wikigraph_evolve" in keywords or "wikigraph_structural" in keywords:
                        continue  # injected edge, no real source chunk to verify
                    source_id = edge.get("source_id", "")
                    if not source_id or source_id == "UNKNOWN":
                        continue  # nothing to verify against
                    chunk_ids = [s.strip() for s in source_id.split("<SEP>") if s.strip()]
                    all_missing = all(cid not in text_chunks_data for cid in chunk_ids)
                    if chunk_ids and all_missing:
                        await graph.remove_edges([(src, tgt)])
                        removed_edges += 1
                        if removed_edges >= config.batch_evolve_max_mutations:
                            break
                if removed_edges >= config.batch_evolve_max_mutations:
                    break
        if removed_edges:
            messages.append(f"EVOLVE(batch): removed {removed_edges} edge(s) with missing source chunks")
    except Exception as e:
        messages.append(f"EVOLVE(batch): source verification failed: {e}")
    return removed_edges, messages


async def _contradiction_resolution(
    rag: LightRAG, config: WikiGraphConfig, llm_func
) -> tuple[int, list[str]]:
    """Merge conflicting <SEP>-joined descriptions on the same entity."""
    graph = rag.chunk_entity_relation_graph
    messages: list[str] = []
    resolved = 0
    if not llm_func:
        return resolved, messages
    try:
        FIELD_SEP = "<SEP>"
        for node_id in (await graph.get_all_labels())[:50]:
            node = await graph.get_node(node_id)
            if not node:
                continue
            desc = node.get("description", "")
            if FIELD_SEP not in desc:
                continue
            parts = [p.strip() for p in desc.split(FIELD_SEP) if p.strip()]
            if len(parts) < 2:
                continue
            prompt = (
                f"Entity: {node_id}\n"
                f"Multiple descriptions from different sources:\n"
                + "\n".join(f"- {p[:200]}" for p in parts[:5])
                + "\n\nWrite a single, accurate, consolidated description (1-2 sentences)."
            )
            try:
                merged = await llm_func(prompt)
                if merged and len(merged.strip()) > 10:
                    node["description"] = merged.strip()
                    await graph.upsert_node(node_id, node)
                    resolved += 1
                    messages.append(f"EVOLVE(batch): merged {len(parts)} descriptions for '{node_id}'")
            except Exception:
                pass
            if resolved >= 3:
                break
    except Exception as e:
        messages.append(f"EVOLVE(batch): contradiction resolution failed: {e}")
    return resolved, messages


async def _inject(rag: LightRAG, mutations: list[dict]) -> int:
    rels = [m["relationship"] for m in mutations if "relationship" in m]
    ents = [m["entity"] for m in mutations if "entity" in m]
    if not rels and not ents:
        return 0
    await rag.ainsert_custom_kg({"chunks": [], "entities": ents, "relationships": rels})
    return len(rels) + len(ents)


async def evolve_light_node(
    state: WikiGraphState, rag: LightRAG, config: WikiGraphConfig, llm_func=None
) -> dict:
    """Tier 1: cheap, incremental improvement — runs after every query."""
    query_log = state.get("query_log", [])
    mutations, messages = await _run_incremental_strategies(
        query_log,
        rag,
        config,
        min_co_count=config.co_retrieval_min_count,
        min_shortcut_count=config.shortcut_path_min_count,
        max_mutations=config.evolve_max_mutations,
        llm_func=llm_func,
    )
    applied = await _inject(rag, mutations)
    if applied:
        messages.append(f"EVOLVE(light): injected {applied} mutation(s)")
    return {
        "evolve_mutations": mutations,
        "evolve_applied": applied,
        "messages": messages,
    }


def should_run_batch_evolve(state: WikiGraphState, config: WikiGraphConfig) -> bool:
    total = len(state.get("query_log", []))
    return (
        config.batch_evolve_interval > 0
        and total > 0
        and total % config.batch_evolve_interval == 0
    )


async def batch_evolve_node(
    state: WikiGraphState, rag: LightRAG, config: WikiGraphConfig, llm_func=None
) -> dict:
    """Tier 2: comprehensive pass over the accumulated query log.

    Triggered every ``batch_evolve_interval`` queries. Re-runs the tier 1
    strategies with a wider net and larger budget, then runs the two
    corpus-wide cleanup checks (source verification, contradiction
    resolution) that need to scan every entity and are too expensive to run
    on every query.
    """
    if not should_run_batch_evolve(state, config):
        return {"batch_triggered": False}

    total = len(state.get("query_log", []))
    query_log = state.get("query_log", [])
    messages = [f"EVOLVE(batch): triggered at query #{total}"]

    mutations, strat_messages = await _run_incremental_strategies(
        query_log,
        rag,
        config,
        min_co_count=max(1, config.co_retrieval_min_count - 1),
        min_shortcut_count=max(1, config.shortcut_path_min_count - 1),
        max_mutations=config.batch_evolve_max_mutations,
        llm_func=llm_func,
    )
    messages += strat_messages

    _, verify_messages = await _source_verification(rag, config)
    messages += verify_messages

    _, resolve_messages = await _contradiction_resolution(rag, config, llm_func)
    messages += resolve_messages

    applied = await _inject(rag, mutations)
    if applied:
        messages.append(f"EVOLVE(batch): injected {applied} mutation(s)")

    return {
        "batch_triggered": True,
        "batch_evolve_mutations": mutations,
        "batch_evolve_applied": applied,
        "messages": messages,
    }
