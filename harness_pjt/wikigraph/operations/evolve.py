"""3. 그래프 개선 (graph improvement): query-log-driven mutation + structural mining.

This module holds all of "그래프 개선" — the single category README.md and
ARCHITECTURE.md describe. It used to be split across two files (evolve.py for
log-driven mutation, structural.py for corpus-metadata mining) mirroring an
older doc structure that treated them as separate pillars; now that the docs
treat them as one category, the code lives in one module too. Two kinds of
work happen here, both part of the same background pipeline:

- Log-driven mutation (``evolve_light_node`` / ``batch_evolve_node``):
  co-retrieval strengthening, gap filling, shortcut paths, source
  verification, contradiction resolution — all reading the accumulated
  ``query_log``.
- Structural mining (``structural_evolve_node``): document/chunk relations
  mined from corpus metadata (``full_doc_id``, ``chunk_order_index``), no
  query log or LLM call needed.

Tagging note: ``rag.ainsert_custom_kg()`` resolves a relationship's
``source_id`` field by looking it up in a map built from the ``chunks`` we
pass alongside it. Since this module injects no chunks, that lookup always
misses and every injected edge's stored ``source_id`` ends up ``"UNKNOWN"``
— not the tag we set. ``keywords`` has no such remapping, so it is the
reliable place to mark an edge's origin: every log-driven mutation carries
``"wikigraph_evolve"`` as the first keyword, every structural-mining edge
carries ``"wikigraph_structural"``. Source verification and LINT's
stale-evolved-edge check both key off ``keywords``, not ``source_id``, for
exactly this reason.

Two tiers, both part of the background evolving path:

- Tier 1 (``evolve_light_node``): runs after every query. Cheap and
  incremental — co-retrieval strengthening, gap filling, shortcut paths —
  scoped to the current query log.
- Tier 2 (``batch_evolve_node``): runs every ``batch_evolve_interval``
  queries (default 50). Comprehensive — re-runs the tier 1 strategies with
  a wider net and a larger mutation budget, plus the corpus-wide checks
  (source verification, contradiction resolution) that scan every entity,
  and triggers structural mining — all too costly to run on every query.
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


# ---------------------------------------------------------------------------
# Structural mining: document/chunk relations from corpus metadata.
#
# LightRAG's graph captures *semantic* relations between entities extracted
# from text. It has no notion of document-to-document or chunk-to-chunk
# structure: two chunks sitting next to each other in the same document, or
# two documents that keep getting pulled into the same answers, have no edge
# at all unless an LLM happened to extract one. This is the gap this section
# addresses — mining structural relations from data LightRAG already has
# (chunk metadata: ``full_doc_id``, ``chunk_order_index``) rather than from
# LLM extraction. Runs alongside the tier-2 batch pass above since both need
# the same corpus-wide scope.
# ---------------------------------------------------------------------------


def _doc_node_id(full_doc_id: str) -> str:
    return f"doc::{full_doc_id}"


async def _entity_chunk_map(rag: LightRAG) -> dict[str, list[str]]:
    """entity_name -> [chunk_id, ...] parsed from each node's source_id."""
    graph = rag.chunk_entity_relation_graph
    mapping: dict[str, list[str]] = {}
    for name in await graph.get_all_labels():
        node = await graph.get_node(name)
        if not node:
            continue
        source_id = node.get("source_id", "")
        mapping[name] = [s.strip() for s in source_id.split("<SEP>") if s.strip()]
    return mapping


async def structural_evolve_node(
    state: WikiGraphState, rag: LightRAG, config: WikiGraphConfig
) -> dict:
    """Mine and inject document-document and chunk-chunk structural edges."""
    chunk_meta: dict[str, dict] = getattr(rag.text_chunks, "_data", {}) if hasattr(rag, "text_chunks") else {}
    if not chunk_meta:
        return {"structural_mutations": [], "structural_applied": 0, "messages": ["STRUCTURAL: no chunk metadata available"]}

    graph = rag.chunk_entity_relation_graph
    entity_chunks = await _entity_chunk_map(rag)
    budget = config.structural_max_mutations

    doc_entities: dict[str, set[str]] = defaultdict(set)
    for ename, cids in entity_chunks.items():
        for cid in cids:
            meta = chunk_meta.get(cid)
            if isinstance(meta, dict) and meta.get("full_doc_id"):
                doc_entities[meta["full_doc_id"]].add(ename)

    mutations: list[dict] = []
    messages: list[str] = []
    existing_labels = set(await graph.get_all_labels())

    # A. document pseudo-node + CONTAINS edge to each entity extracted from it
    contains_added = 0
    for doc_id, entities in doc_entities.items():
        if len(mutations) >= budget:
            break
        doc_name = _doc_node_id(doc_id)
        if doc_name not in existing_labels:
            file_path = next(
                (
                    m.get("file_path")
                    for m in chunk_meta.values()
                    if isinstance(m, dict) and m.get("full_doc_id") == doc_id and m.get("file_path")
                ),
                doc_id,
            )
            mutations.append({
                "type": "structural_doc_node",
                "entity": {
                    "entity_name": doc_name,
                    "entity_type": "document",
                    "description": f"Source document: {file_path}",
                    "source_id": "wikigraph_structural",
                },
            })
            existing_labels.add(doc_name)
        for ename in entities:
            if len(mutations) >= budget:
                break
            if await graph.has_edge(doc_name, ename) or await graph.has_edge(ename, doc_name):
                continue
            mutations.append({
                "type": "structural_contains",
                "relationship": {
                    "src_id": doc_name,
                    "tgt_id": ename,
                    "description": "Document contains this entity",
                    "keywords": "wikigraph_structural,contains",
                    "weight": config.structural_edge_weight,
                    "source_id": "wikigraph_structural",
                },
            })
            contains_added += 1
    if contains_added:
        messages.append(f"STRUCTURAL: {contains_added} document-CONTAINS-entity edge(s) queued")

    # B. document <-> document, related when they share enough entities
    doc_ids = list(doc_entities)
    doc_doc_added = 0
    for i in range(len(doc_ids)):
        if len(mutations) >= budget:
            break
        for j in range(i + 1, len(doc_ids)):
            if len(mutations) >= budget:
                break
            a, b = doc_ids[i], doc_ids[j]
            shared = doc_entities[a] & doc_entities[b]
            if len(shared) < config.structural_doc_shared_entity_min:
                continue
            doc_a, doc_b = _doc_node_id(a), _doc_node_id(b)
            if await graph.has_edge(doc_a, doc_b) or await graph.has_edge(doc_b, doc_a):
                continue
            mutations.append({
                "type": "structural_doc_doc",
                "relationship": {
                    "src_id": doc_a,
                    "tgt_id": doc_b,
                    "description": f"Documents share {len(shared)} entities: {', '.join(sorted(shared)[:5])}",
                    "keywords": "wikigraph_structural,shares_entities",
                    "weight": config.structural_edge_weight,
                    "source_id": "wikigraph_structural",
                },
            })
            doc_doc_added += 1
    if doc_doc_added:
        messages.append(f"STRUCTURAL: {doc_doc_added} document-document edge(s) queued")

    # C. chunk adjacency, projected onto entities (chunks aren't graph nodes):
    # entities extracted from neighboring chunks of the same document get a
    # lightweight NEAR edge if they aren't already connected.
    chunk_entities: dict[str, set[str]] = defaultdict(set)
    for ename, cids in entity_chunks.items():
        for cid in cids:
            chunk_entities[cid].add(ename)

    by_doc: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for cid, meta in chunk_meta.items():
        if isinstance(meta, dict) and meta.get("full_doc_id") is not None:
            by_doc[meta["full_doc_id"]].append((meta.get("chunk_order_index", 0), cid))

    near_added = 0
    max_gap = config.structural_chunk_adjacency_max_gap
    for doc_id, ordered in by_doc.items():
        if len(mutations) >= budget:
            break
        ordered.sort(key=lambda x: x[0])
        for idx in range(len(ordered) - 1):
            if len(mutations) >= budget:
                break
            order_a, cid_a = ordered[idx]
            order_b, cid_b = ordered[idx + 1]
            if order_b - order_a > max_gap:
                continue
            ents_a = chunk_entities.get(cid_a, set())
            ents_b = chunk_entities.get(cid_b, set())
            added_for_pair = False
            for ea in ents_a:
                if added_for_pair or len(mutations) >= budget:
                    break
                for eb in ents_b:
                    if ea == eb:
                        continue
                    if await graph.has_edge(ea, eb) or await graph.has_edge(eb, ea):
                        continue
                    mutations.append({
                        "type": "structural_chunk_near",
                        "relationship": {
                            "src_id": ea,
                            "tgt_id": eb,
                            "description": "Entities appear in adjacent chunks of the same document",
                            "keywords": "wikigraph_structural,chunk_adjacency",
                            "weight": config.structural_edge_weight,
                            "source_id": "wikigraph_structural",
                        },
                    })
                    near_added += 1
                    added_for_pair = True  # one edge per chunk pair caps growth
                    break
    if near_added:
        messages.append(f"STRUCTURAL: {near_added} chunk-adjacency edge(s) queued")

    applied = await _inject(rag, mutations)
    if applied:
        messages.append(f"STRUCTURAL: injected {applied} mutation(s)")
    if not mutations:
        messages.append("STRUCTURAL: no new structural relations found")

    return {
        "structural_mutations": mutations,
        "structural_applied": applied,
        "messages": messages,
    }
