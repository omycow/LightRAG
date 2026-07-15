"""EVOLVE operation: query-driven knowledge evolution.

Strategies:
  1. Co-retrieval entity pair strengthening
  2. Knowledge gap fill (LLM-assisted)
  3. Multi-hop shortcut creation
  4. Source verification (remove stale edges)
  5. Contradiction resolution (LLM-assisted)
  6. Cross-Document Retrieval Bridge (CDRB) — entity-level, no ground truth
  7. [DEPRECATED] Entity-Document Alignment (EDA) — replaced by DA (Strategy 8)
  8. Document Anchoring (DA) — general document-level VDB entities
  9. Document Co-Structure Graph (DCSG) — document-level structural edges
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections import Counter, defaultdict
from typing import Any

from lightrag import LightRAG
from wikigraph.config import WikiGraphConfig
from wikigraph.state import QueryLogEntry, WikiGraphState

GRAPH_FIELD_SEP = "<SEP>"


# ── Strategy 8: Document Anchoring (DA) ────────────────────────────────────────

async def da_strategy(rag: LightRAG) -> tuple[int, list[str]]:
    """
    Strategy 8: Document Anchoring (DA).

    For every document in the corpus, creates a DOC:filename entity that:
    - Has source_id = ALL chunks from that document
    - Is inserted into entity VDB for semantic search
    - Acts as a document-level structural hub in the KG

    Why this is general (no ground truth, no data-structure assumptions):
      LightRAG builds entity-centric graphs. Entities are extracted from chunk
      text, so a document is only reachable in retrieval if one of its extracted
      entities ranks high in entity VDB for the query.

      DA adds one DOC entity per document, searchable by filename terms + content
      excerpt. When a query matches a DOC entity (e.g., "AiM Macro RTL Hub" →
      DOC:37_PIM_AiM_Macro_RTL_Hub.html), ALL chunks from that document are
      returned — regardless of what entities LightRAG originally extracted.

      This directly fixes the structural retrieval gap: cross-referenced documents
      are now reachable without depending on hub-named entity extraction.

    Idempotent: safe to re-run. Only creates missing / updates stale DOC entities.
    """
    from lightrag.utils import compute_mdhash_id

    messages: list[str] = []
    graph = rag.chunk_entity_relation_graph

    # Build file → chunks + first-chunk excerpt
    file_to_chunks: dict[str, list[str]] = defaultdict(list)
    file_to_excerpt: dict[str, str] = {}

    for cid, cdata in rag.text_chunks._data.items():
        fp = cdata.get("file_path", "")
        if not fp:
            continue
        basename = os.path.basename(fp)
        file_to_chunks[basename].append(cid)
        if basename not in file_to_excerpt:
            content = cdata.get("content", "")
            file_to_excerpt[basename] = content[:300].replace("\n", " ")

    messages.append(f"DA: {len(file_to_chunks)} unique documents in corpus")

    created = 0
    updated = 0
    vdb_batch: dict[str, dict] = {}

    for fname, chunk_ids in file_to_chunks.items():
        entity_name = f"DOC:{fname}"

        # Description: filename terms only (no content excerpt).
        # Using ONLY filename terms avoids false-positive entity VDB matches:
        # hub/index files have generic first-chunk content ("see links below")
        # that would match too broadly. Filename terms are specific enough to
        # match queries that explicitly reference this document.
        stem = os.path.splitext(fname)[0]
        terms = [t for t in re.split(r"[_\-\s\.]+", stem) if len(t) > 1]
        description = (
            f"Document file: {fname}. "
            f"File name terms: {' '.join(terms[:12])}. "
            f"Contains {len(chunk_ids)} chunk(s). [DOCUMENT_ANCHOR]"
        )

        existing = await graph.get_node(entity_name)
        if existing:
            existing_chunks = {
                c.strip()
                for c in existing.get("source_id", "").split(GRAPH_FIELD_SEP)
                if c.strip()
            }
            chunks_ok = set(chunk_ids) <= existing_chunks
            # Also check description format: old format lacks [DOCUMENT_ANCHOR]
            # or still contains content excerpts. Force-update if format is stale.
            desc_ok = "[DOCUMENT_ANCHOR]" in existing.get("description", "")
            if chunks_ok and desc_ok:
                continue  # Truly up to date: same chunks AND new description format
            all_chunks = sorted(existing_chunks | set(chunk_ids))
            updated += 1
        else:
            all_chunks = sorted(chunk_ids)
            created += 1

        source_id = GRAPH_FIELD_SEP.join(all_chunks)
        await graph.upsert_node(entity_name, {
            "entity_type": "DOCUMENT",
            "description": description,
            "source_id": source_id,
        })

        vdb_id = compute_mdhash_id(entity_name, prefix="ent-")
        vdb_batch[vdb_id] = {
            "content": f"{entity_name}\n{description}",
            "entity_name": entity_name,
        }

    if vdb_batch:
        await graph.index_done_callback()
        await rag.entities_vdb.upsert(vdb_batch)
        messages.append(
            f"DA: created={created} updated={updated} DOC entities "
            f"→ KG graph + entity VDB ({len(vdb_batch)} embeddings queued)"
        )
    else:
        messages.append("DA: all DOC entities already current — no changes needed")

    return created + updated, messages


# ── Strategy 9: Document Co-Structure Graph (DCSG) ─────────────────────────────

async def dcsg_strategy(
    rag: LightRAG,
    questions: list[dict],
    evolve_k: int = 50,
    co_occur_min: int = 2,
    max_edges: int = 200,
) -> tuple[int, list[str]]:
    """
    Strategy 9: Document Co-Structure Graph (DCSG).

    Builds document-level structural edges from query-retrieval patterns.

    Principle:
      Documents co-retrieved across many queries are topically related.
      DCSG adds explicit edges between their DOC entities (created by DA),
      enabling structural multi-hop traversal at the document level.

    Traversal path enabled:
      entity VDB → DOC:file_A (DA anchor)
        → [CO_RETRIEVED_WITH] → DOC:file_B
          → DOC:file_B.source_id chunks → retrieved

    Why document-level edges are more impactful than entity-level (CDRB):
      CDRB adds one edge per entity pair — one of thousands of edges in a doc.
      DCSG adds one edge per document pair — all chunks of both docs are linked.
      Document-level connections create structural retrieval paths that survive
      across all entities within the document.

    General: observes only (queries, retrieval results) — no ground truth,
    no document taxonomy, no filename convention assumed.
    """
    from harness_pjt.benchmark.retrieval import query_bm25, query_mix_mode
    from lightrag.utils import compute_mdhash_id

    messages: list[str] = []
    graph = rag.chunk_entity_relation_graph

    # Build chunk → basename mapping
    chunk_to_file: dict[str, str] = {}
    for cid, cdata in rag.text_chunks._data.items():
        fp = cdata.get("file_path", "")
        if fp:
            chunk_to_file[cid] = os.path.basename(fp)

    n_files = len(set(chunk_to_file.values()))
    messages.append(f"DCSG: {n_files} documents mapped across {len(chunk_to_file)} chunks")

    # Collect document co-occurrence per query
    doc_cooccurrence: Counter = Counter()
    doc_query_kw: dict[tuple[str, str], list[str]] = defaultdict(list)

    async def _collect_doc_cooc(q: dict):
        q_text = q.get("question", "")
        try:
            mix_chunks = await query_mix_mode(rag, q_text, top_k=evolve_k)
        except Exception:
            mix_chunks = []
        bm25_chunks = query_bm25(rag, q_text, top_k=evolve_k)

        retrieved_files: set[str] = set()
        for chunk in mix_chunks + bm25_chunks:
            cid = chunk.get("chunk_id") or chunk.get("id", "")
            fp = chunk.get("file_path", "")
            if fp:
                retrieved_files.add(os.path.basename(fp))
            elif cid and cid in chunk_to_file:
                retrieved_files.add(chunk_to_file[cid])

        # Extract query keywords for edge description
        words = [w for w in q_text.split() if len(w) > 3][:6]
        files = sorted(retrieved_files)
        for i in range(len(files)):
            for j in range(i + 1, len(files)):
                pair = (files[i], files[j])
                doc_cooccurrence[pair] += 1
                doc_query_kw[pair].extend(words)

    await asyncio.gather(*[_collect_doc_cooc(q) for q in questions])

    messages.append(
        f"DCSG: {len(doc_cooccurrence)} unique doc pairs observed "
        f"across {len(questions)} queries"
    )

    # Filter to qualifying pairs, sort by co-occurrence count
    qualifying = [
        (a, b, cnt) for (a, b), cnt in doc_cooccurrence.items()
        if cnt >= co_occur_min
    ]
    qualifying.sort(key=lambda x: -x[2])
    qualifying = qualifying[:max_edges]

    if not qualifying:
        messages.append(f"DCSG: no doc pairs meet co_occur_min={co_occur_min}")
        return 0, messages

    messages.append(f"DCSG: {len(qualifying)} qualifying doc pairs → structural edges")

    # Look up existing DOC nodes and their edges
    all_doc_names = (
        {f"DOC:{a}" for a, _, _ in qualifying}
        | {f"DOC:{b}" for _, b, _ in qualifying}
    )
    existing_nodes, existing_edges_map = await asyncio.gather(
        graph.get_nodes_batch(list(all_doc_names)),
        graph.get_nodes_edges_batch(list(all_doc_names)),
    )

    existing_pairs: set[tuple[str, str]] = set()
    for edges in existing_edges_map.values():
        for src, tgt in edges:
            existing_pairs.add(tuple(sorted((src, tgt))))

    edge_list: list[tuple[str, str, dict]] = []
    vdb_rel_data: dict[str, dict] = {}

    for a, b, cnt in qualifying:
        doc_a = f"DOC:{a}"
        doc_b = f"DOC:{b}"
        pair_key = tuple(sorted((doc_a, doc_b)))
        if pair_key in existing_pairs:
            continue

        kw = list(dict.fromkeys(doc_query_kw.get((a, b), [])))[:6]
        description = (
            f"Documents co-retrieved in {cnt} queries. "
            f"Shared query topics: {', '.join(kw)}"
        )
        keywords = f"document-structure,dcsg-evolve,{','.join(kw[:3])}"

        # Edge source_id: union of both DOC entities' chunks for traversal
        node_a_data = existing_nodes.get(doc_a) or {}
        node_b_data = existing_nodes.get(doc_b) or {}
        src_combined = GRAPH_FIELD_SEP.join(
            s for s in [node_a_data.get("source_id", ""), node_b_data.get("source_id", "")]
            if s
        )

        edge_data = {
            "weight": 0.7,
            "description": description,
            "keywords": keywords,
            "source_id": src_combined or f"dcsg_{a}_{b}",
            "file_path": "wikigraph_dcsg",
            "created_at": int(time.time()),
        }
        edge_list.append((doc_a, doc_b, edge_data))

        # Relation VDB: enables mix mode relation retrieval to find DCSG edges
        rel_id = compute_mdhash_id(doc_a + doc_b, prefix="rel-")
        vdb_rel_data[rel_id] = {
            "src_id": doc_a,
            "tgt_id": doc_b,
            "source_id": src_combined or f"dcsg_{a}_{b}",
            "content": f"document-structure\t{doc_a}\n{doc_b}\n{description}",
            "keywords": keywords,
            "description": description,
            "weight": 0.7,
            "file_path": "wikigraph_dcsg",
        }

    messages.append(f"DCSG: {len(edge_list)} new document-level structural edges")

    if not edge_list:
        return 0, messages

    try:
        await graph.upsert_edges_batch(edge_list)
        await graph.index_done_callback()
        if vdb_rel_data:
            await rag.relationships_vdb.upsert(vdb_rel_data)
        messages.append(
            f"DCSG: injected {len(edge_list)} structural edges (KG + relation VDB)"
        )
    except Exception as e:
        messages.append(f"DCSG: injection error: {e}")
        return 0, messages

    return len(edge_list), messages


async def dcsg_from_drg(
    rag: LightRAG,
    drg,
    co_occur_min: float = 0.1,
    max_edges: int = 300,
) -> tuple[int, list[str]]:
    """
    Fast DCSG from DRG co-retrieval weights — no queries needed.

    DRG already records which documents were co-retrieved.
    Convert DRG edges above co_occur_min weight into KG structural edges.
    This is the fast equivalent of dcsg_strategy() without running any queries.

    co_occur_min: DRG edge weight threshold (0.1 = 1 co-retrieval, 0.3 = 3)
    """
    from lightrag.utils import compute_mdhash_id

    messages: list[str] = []
    graph = rag.chunk_entity_relation_graph

    # Get all DRG edges above threshold
    drg_edges = [
        (a, b, data)
        for key, data in drg._data.get("edges", {}).items()
        for a, b in [key.split("||", 1)] if "||" in key
        if data.get("weight", 0) >= co_occur_min
    ]
    drg_edges.sort(key=lambda x: -x[2].get("weight", 0))
    drg_edges = drg_edges[:max_edges]

    if not drg_edges:
        messages.append(f"DCSG(DRG): no edges meet weight≥{co_occur_min}")
        return 0, messages

    messages.append(f"DCSG(DRG): {len(drg_edges)} DRG edges → structural KG edges")

    # Build chunk→file mapping for source_id construction
    chunk_to_file: dict[str, str] = {}
    for cid, cdata in rag.text_chunks._data.items():
        fp = cdata.get("file_path", "")
        if fp:
            chunk_to_file[cid] = os.path.basename(fp)

    # Look up existing DOC entities and edges
    all_doc_names = (
        {f"DOC:{a}" for a, _, _ in drg_edges}
        | {f"DOC:{b}" for _, b, _ in drg_edges}
    )
    try:
        existing_nodes, existing_edges_map = await asyncio.gather(
            graph.get_nodes_batch(list(all_doc_names)),
            graph.get_nodes_edges_batch(list(all_doc_names)),
        )
    except Exception as e:
        messages.append(f"DCSG(DRG): graph lookup error: {e}")
        return 0, messages

    existing_pairs: set[tuple[str, str]] = set()
    for edges in existing_edges_map.values():
        for src, tgt in edges:
            existing_pairs.add(tuple(sorted((str(src), str(tgt)))))

    edge_list: list[tuple[str, str, dict]] = []
    vdb_rel_data: dict[str, dict] = {}

    for a, b, edata in drg_edges:
        doc_a = f"DOC:{a}"
        doc_b = f"DOC:{b}"
        pair_key = tuple(sorted((doc_a, doc_b)))
        if pair_key in existing_pairs:
            continue

        weight = edata.get("weight", co_occur_min)
        cnt = edata.get("count", 1)

        node_a_data = existing_nodes.get(doc_a) or {}
        node_b_data = existing_nodes.get(doc_b) or {}
        src_combined = GRAPH_FIELD_SEP.join(
            s for s in [node_a_data.get("source_id", ""), node_b_data.get("source_id", "")]
            if s
        )

        description = f"Documents co-retrieved {cnt} times (DRG weight={weight:.2f})"
        edge_data = {
            "weight": min(0.9, 0.5 + weight),
            "description": description,
            "keywords": "document-structure,dcsg-drg",
            "source_id": src_combined or f"dcsg_{a}_{b}",
            "file_path": "wikigraph_dcsg",
            "created_at": int(time.time()),
        }
        edge_list.append((doc_a, doc_b, edge_data))

        rel_id = compute_mdhash_id(doc_a + doc_b, prefix="rel-")
        vdb_rel_data[rel_id] = {
            "src_id": doc_a, "tgt_id": doc_b,
            "source_id": src_combined or f"dcsg_{a}_{b}",
            "content": f"document-structure\t{doc_a}\n{doc_b}\n{description}",
            "keywords": "document-structure,dcsg-drg",
            "description": description, "weight": min(0.9, 0.5 + weight),
            "file_path": "wikigraph_dcsg",
        }

    if not edge_list:
        messages.append("DCSG(DRG): all edges already exist in KG")
        return 0, messages

    try:
        await graph.upsert_edges_batch(edge_list)
        await graph.index_done_callback()
        if vdb_rel_data:
            await rag.relationships_vdb.upsert(vdb_rel_data)
        messages.append(f"DCSG(DRG): injected {len(edge_list)} new structural edges")
    except Exception as e:
        messages.append(f"DCSG(DRG): injection error: {e}")
        return 0, messages

    return len(edge_list), messages


# ── Strategy 6: Cross-Document Retrieval Bridge (CDRB) ─────────────────────────

def _cdrb_chunk_file_path(rag: LightRAG, chunk_id: str) -> str:
    """Look up file_path for a chunk from the text_chunks KV store."""
    try:
        entry = rag.text_chunks._data.get(chunk_id)
        if entry and isinstance(entry, dict):
            return entry.get("file_path", "")
    except Exception:
        pass
    return ""


async def _cdrb_build_entity_index(
    rag: LightRAG,
) -> tuple[dict[str, list[str]], dict[str, str], dict[str, int]]:
    """
    Build:
      chunk_to_ents  : chunk_id → [entity_name, ...]
      entity_to_doc  : entity_name → primary source document file_path
      entity_doc_cnt : entity_name → # distinct documents it appears in

    entity_doc_cnt enables specificity-weighted scoring:
      score(A,B) = count(A,B) / (doc_cnt(A) × doc_cnt(B))
    Document-specific entities score higher than hub entities.
    """
    chunk_to_ents: dict[str, list[str]] = defaultdict(list)
    entity_to_doc: dict[str, str] = {}
    entity_doc_set: dict[str, set] = defaultdict(set)
    try:
        all_nodes = await rag.chunk_entity_relation_graph.get_all_nodes()
        for node in all_nodes:
            eid = node.get("id") or node.get("entity_name", "")
            if not eid:
                continue
            src = node.get("source_id", "")
            for cid in (c.strip() for c in src.split(GRAPH_FIELD_SEP) if c.strip()):
                chunk_to_ents[cid].append(eid)
                fp = _cdrb_chunk_file_path(rag, cid)
                if fp:
                    entity_doc_set[eid].add(fp)
                    if eid not in entity_to_doc:
                        entity_to_doc[eid] = fp
    except Exception:
        pass
    entity_doc_cnt = {eid: max(1, len(docs)) for eid, docs in entity_doc_set.items()}
    return dict(chunk_to_ents), entity_to_doc, entity_doc_cnt


async def cdrb_strategy(
    rag: LightRAG,
    questions: list[dict],
    evolve_k: int = 50,
    co_occur_min: int = 2,
    max_edges: int = 500,
) -> tuple[int, list[str]]:
    """
    Strategy 6: Cross-Document Retrieval Bridge (CDRB).

    Principle:
      Queries that retrieve chunks from multiple documents reveal which
      documents are jointly relevant to a topic. CDRB mines accumulated
      query-retrieval patterns to discover missing cross-document entity
      connections, then adds them as KG edges.

    Only cross-document entity pairs are counted (entities from DIFFERENT
    source documents). Intra-document edges already exist from LightRAG
    ingestion; CDRB targets only the missing inter-document connections.

    Specificity weighting prevents hub-entity domination:
      score(A, B) = count(A,B) / (doc_cnt(A) × doc_cnt(B))
    Entities specific to 1-2 documents rank higher than common hub entities.

    After injection, graph traversal carries cross-document edges:
      entity A (found by VDB) → CDRB edge → entity B → B's document chunks
      → B's document appears in relation-retrieval results → top-K hit

    Critical: edges are added to BOTH the KG (upsert_edges_batch) AND the
    relation VDB (relationships_vdb.upsert) so that mix mode relation retrieval
    can discover them via semantic search.

    Returns:
      (edges_added, messages)
    """
    from harness_pjt.benchmark.retrieval import query_bm25, query_mix_mode
    from lightrag.utils import compute_mdhash_id

    messages: list[str] = []
    graph = rag.chunk_entity_relation_graph

    chunk_to_ents, entity_to_doc, entity_doc_cnt = await _cdrb_build_entity_index(rag)
    messages.append(
        f"CDRB: indexed {len(chunk_to_ents)} chunks, "
        f"{len(entity_to_doc)} entities with doc mapping"
    )

    pair_counts: Counter = Counter()
    completed = 0

    async def _collect_one(q: dict):
        nonlocal completed
        q_text = q.get("question", "")
        try:
            mix_chunks = await query_mix_mode(rag, q_text, top_k=evolve_k)
        except Exception:
            mix_chunks = []
        bm25_chunks = query_bm25(rag, q_text, top_k=evolve_k)

        # Group entities by source document for this query
        doc_entities: dict[str, set[str]] = defaultdict(set)
        for chunk in mix_chunks + bm25_chunks:
            cid = chunk.get("chunk_id") or chunk.get("id", "")
            if not cid:
                continue
            for eid in chunk_to_ents.get(cid, []):
                doc = entity_to_doc.get(eid, "")
                if doc:
                    doc_entities[doc].add(eid)

        # Count cross-document pairs only
        docs = list(doc_entities.keys())
        for i in range(len(docs)):
            for j in range(i + 1, len(docs)):
                for ea in doc_entities[docs[i]]:
                    for eb in doc_entities[docs[j]]:
                        pair_counts[tuple(sorted((ea, eb)))] += 1

        completed += 1

    await asyncio.gather(*[_collect_one(q) for q in questions])
    messages.append(
        f"CDRB: {len(pair_counts)} cross-doc pairs observed across {len(questions)} queries"
    )

    # Specificity-weighted scoring
    candidate_pairs = []
    for (a, b), cnt in pair_counts.items():
        if cnt < co_occur_min:
            continue
        score = cnt / (entity_doc_cnt.get(a, 1) * entity_doc_cnt.get(b, 1))
        candidate_pairs.append((a, b, cnt, score))

    if not candidate_pairs:
        messages.append("CDRB: no qualifying cross-document pairs found")
        return 0, messages

    sorted_pairs = sorted(candidate_pairs, key=lambda x: -x[3])[:max_edges]
    messages.append(
        f"CDRB: {len(candidate_pairs)} qualifying pairs (≥{co_occur_min}), "
        f"top-{len(sorted_pairs)} selected by specificity score"
    )

    all_entity_ids: set[str] = set()
    for a, b, _cnt, _score in sorted_pairs:
        all_entity_ids.add(a)
        all_entity_ids.add(b)

    entity_nodes, existing_edges_by_node = await asyncio.gather(
        graph.get_nodes_batch(list(all_entity_ids)),
        graph.get_nodes_edges_batch(list(all_entity_ids)),
    )

    existing_pairs: set[tuple[str, str]] = set()
    for edges in existing_edges_by_node.values():
        for src, tgt in edges:
            existing_pairs.add(tuple(sorted((src, tgt))))

    # Build new edges with real source chunk IDs
    edge_list: list[tuple[str, str, dict]] = []
    vdb_data: dict[str, dict] = {}

    for a, b, cnt, score in sorted_pairs:
        if tuple(sorted((a, b))) in existing_pairs:
            continue

        node_a = entity_nodes.get(a) or {}
        node_b = entity_nodes.get(b) or {}
        a_src = node_a.get("source_id", "")
        b_src = node_b.get("source_id", "")
        real_source_id = GRAPH_FIELD_SEP.join(s for s in [a_src, b_src] if s)

        doc_a = os.path.basename(entity_to_doc.get(a, "?"))
        doc_b = os.path.basename(entity_to_doc.get(b, "?"))
        description = (
            f"{a} and {b} co-retrieved in {cnt} queries "
            f"({doc_a} ↔ {doc_b})"
        )
        keywords = "cross-document,cdrb-evolve"
        weight = 0.6
        file_path = "wikigraph_cdrb"
        created_at = int(time.time())

        edge_data = {
            "weight":      weight,
            "description": description,
            "keywords":    keywords,
            "source_id":   real_source_id or f"cdrb_{a}_{b}",
            "file_path":   file_path,
            "created_at":  created_at,
        }
        edge_list.append((a, b, edge_data))

        rel_vdb_id = compute_mdhash_id(a + b, prefix="rel-")
        rel_content = f"{keywords}\t{a}\n{b}\n{description}"
        vdb_data[rel_vdb_id] = {
            "src_id":     a,
            "tgt_id":     b,
            "source_id":  real_source_id or f"cdrb_{a}_{b}",
            "content":    rel_content,
            "keywords":   keywords,
            "description": description,
            "weight":     weight,
            "file_path":  file_path,
        }

    messages.append(f"CDRB: {len(edge_list)} new cross-document edges to inject")

    if not edge_list:
        return 0, messages

    try:
        await graph.upsert_edges_batch(edge_list)
        await graph.index_done_callback()
        await rag.relationships_vdb.upsert(vdb_data)
        messages.append(
            f"CDRB: injected {len(edge_list)} edges "
            f"(KG + relation VDB, flushed to disk)"
        )
    except Exception as e:
        messages.append(f"CDRB: injection error: {e}")
        return 0, messages

    return len(edge_list), messages


# ── [DEPRECATED] Strategy 7: Entity-Document Alignment (EDA) ──────────────────

async def eda_strategy(rag: LightRAG) -> tuple[int, list[str]]:
    """
    [DEPRECATED] Strategy 7: Entity-Document Alignment (EDA).

    Replaced by Strategy 8 (DA — Document Anchoring) which is more general:
    - EDA: aligns entities whose names match filenames (data-type dependent)
    - DA: creates DOC entities for ALL documents regardless of entity names

    This function is kept for backward compatibility only. New code should
    use da_strategy() instead.
    """
    return await da_strategy(rag)


# ── Helper functions for legacy evolve_node ─────────────────────────────────────

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
        has_ab = await graph.has_edge(a, b)
        has_ba = await graph.has_edge(b, a)
        if not has_ab and not has_ba:
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

    # Strategy 2: Fill knowledge gaps
    gap_entries = [
        e for e in query_log
        if e.get("retrieved_chunks") and not e.get("retrieved_entities")
    ]
    empty_entries = [
        e for e in query_log
        if not e.get("retrieved_chunks") and not e.get("retrieved_entities")
        and e.get("result_quality") is not None and e["result_quality"] < config.gap_quality_threshold
    ]
    for entry in empty_entries[:2]:
        messages.append(
            f"EVOLVE: gap detected for '{entry.get('query', '')[:40]}' — "
            f"no raw data found, consider adding relevant documents"
        )
    if gap_entries and llm_func and len(mutations) < config.evolve_max_mutations:
        for entry in gap_entries[:2]:
            fq = entry.get("query", "")
            chunks_found = entry.get("retrieved_chunks", [])

            if chunks_found:
                chunk_texts = []
                if hasattr(rag, "text_chunks") and hasattr(rag.text_chunks, "_data"):
                    for cid in chunks_found[:3]:
                        cval = rag.text_chunks._data.get(cid, {})
                        if isinstance(cval, dict) and cval.get("content"):
                            chunk_texts.append(cval["content"][:500])

                if chunk_texts and llm_func:
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
                            f"→ entity={bool(entity)}, rel={bool(rel)}"
                        )
                    except Exception:
                        pass

    # Strategy 3: Create shortcut edges for frequent multi-hop paths
    shortcuts = _find_shortcut_paths(query_log, config.shortcut_path_min_count)
    for a, b, c in shortcuts[: config.evolve_max_mutations - len(mutations)]:
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

    # Strategy 4: Source verification — remove relations whose source chunks no longer exist
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
                    source_id = edge.get("source_id", "")
                    if not source_id or "wikigraph_evolve" in source_id:
                        continue
                    chunk_ids = [s.strip() for s in source_id.split("<SEP>") if s.strip()]
                    all_missing = all(cid not in text_chunks_data for cid in chunk_ids)
                    if chunk_ids and all_missing:
                        await graph.remove_edges([(src, tgt)])
                        removed_edges += 1
                        if removed_edges >= config.evolve_max_mutations:
                            break
                if removed_edges >= config.evolve_max_mutations:
                    break
        if removed_edges:
            messages.append(f"EVOLVE: removed {removed_edges} edge(s) with missing source chunks")
    except Exception as e:
        messages.append(f"EVOLVE: source verification failed: {e}")

    # Strategy 5: Contradiction resolution — merge conflicting descriptions
    resolved = 0
    try:
        if llm_func:
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
                        messages.append(f"EVOLVE: merged {len(parts)} descriptions for '{node_id}'")
                except Exception:
                    pass
                if resolved >= 3:
                    break
    except Exception as e:
        messages.append(f"EVOLVE: contradiction resolution failed: {e}")

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
