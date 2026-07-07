"""STRUCTURAL operation: document-document and chunk-chunk relations.

LightRAG's graph captures *semantic* relations between entities extracted
from text. It has no notion of document-to-document or chunk-to-chunk
structure: two chunks sitting next to each other in the same document, or
two documents that keep getting pulled into the same answers, have no edge
at all unless an LLM happened to extract one. This is the fundamental gap
this module addresses — mining structural relations from data LightRAG
already has (chunk metadata: ``full_doc_id``, ``chunk_order_index``) rather
than from LLM extraction.

Runs alongside the tier-2 batch EVOLVE pass (see evolve.py) since both need
the same corpus-wide scope. See the ``keywords`` tagging note at the top of
evolve.py — the same "UNKNOWN source_id" caveat applies here, so every edge
this module injects carries ``"wikigraph_structural"`` in ``keywords``.
"""

from __future__ import annotations

from collections import defaultdict

from lightrag import LightRAG
from wikigraph.config import WikiGraphConfig
from wikigraph.state import WikiGraphState


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

    rels = [m["relationship"] for m in mutations if "relationship" in m]
    ents = [m["entity"] for m in mutations if "entity" in m]
    applied = 0
    if rels or ents:
        await rag.ainsert_custom_kg({"chunks": [], "entities": ents, "relationships": rels})
        applied = len(rels) + len(ents)
        messages.append(f"STRUCTURAL: injected {applied} mutation(s)")
    if not mutations:
        messages.append("STRUCTURAL: no new structural relations found")

    return {
        "structural_mutations": mutations,
        "structural_applied": applied,
        "messages": messages,
    }
