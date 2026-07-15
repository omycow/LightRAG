"""
Adaptive Retriever — Agentic RAG with runtime graph learning.

Retrieval pipeline:
  1. [Scope]      QSM lookup → known-relevant docs → prepend DOC: hints to query
  2. [Retrieve]   LightRAG mix mode with scope-enhanced query
  3. [Supplement] DRG lookup → structurally related docs → fetch chunks
  4. [Learn]      Update QSM + DRG with this query's results
  5. [Online KG]  If DRG edge hits online threshold → inject DCSG edge into KG live
  6. [Return]     Base results + supplemented chunks

"Gets better with use":
  - QSM accumulates query→doc routing patterns → future similar queries scoped
  - DRG accumulates doc co-retrieval edges → hub docs discovered via supplement
  - Online DCSG bakes DRG patterns into the KG → even base retrieval improves
  - More queries → stronger patterns → better retrieval → higher co-hit score

Each mechanism is independent and complementary:
  QSM  → query-time scope restriction (pre-retrieval)
  DRG  → structural supplement (post-retrieval)
  DCSG → permanent KG improvement (async, in background)
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lightrag import LightRAG
    from wikigraph.qsm import QueryStructuralMemory
    from wikigraph.drg import DocRelationGraph


# ── Main agentic retrieve ─────────────────────────────────────────────────────

async def agentic_retrieve(
    rag: "LightRAG",
    query: str,
    qsm: "QueryStructuralMemory",
    drg: "DocRelationGraph | None" = None,
    top_k: int = 20,
    qsm_min_count: int = 2,
    drg_min_weight: float = 0.3,
    drg_supplement_k: int = 3,
    online_dcsg_threshold: int = 5,
    update_qsm: bool = True,
    update_drg: bool = True,
) -> list[dict]:
    """
    QSM + DRG augmented retrieval with online learning.

    Query flow:
      1. QSM scope: prepend known-relevant DOC:filename entities to query
         → entity VDB naturally ranks those DOC entities higher (DA mechanism)
      2. Mix-mode retrieval with (optionally enhanced) query
      3. DRG supplement: add chunks from structurally related docs not yet retrieved
         → key mechanism for co-retrieval (hub docs discovered via DRG edges)
      4. QSM update: record (query, retrieved_docs) pattern
      5. DRG update: record (retrieved_docs) co-occurrence, strengthen edges
      6. Online DCSG: if DRG edge count crosses threshold → inject KG edge live

    Args:
      rag:                  LightRAG instance
      query:                original user query
      qsm:                  QueryStructuralMemory (accumulates query→doc patterns)
      drg:                  DocRelationGraph (accumulates doc co-retrieval edges)
      top_k:                base retrieval window
      qsm_min_count:        min QSM pattern count to use as scope hint
      drg_min_weight:       min DRG edge weight to use for supplement (0.3 = 3 co-retrievals)
      drg_supplement_k:     chunks to fetch per related doc
      online_dcsg_threshold: DRG count threshold to trigger live KG edge injection
      update_qsm:           update QSM after retrieval (False for eval-only)
      update_drg:           update DRG after retrieval (False for eval-only)

    Returns:
      list of chunk dicts (base + supplement)
    """
    from harness_pjt.benchmark.retrieval import query_mix_mode, query_hybrid

    # ── Step 1: QSM scope lookup ──────────────────────────────────────────────
    scope_docs = qsm.get_scope_docs(query, min_count=qsm_min_count)

    enhanced_query = query
    use_hybrid = getattr(rag, '_fast_mode', False)

    if not use_hybrid:
        if scope_docs:
            # Prepend DOC:filename tokens — entity VDB ranks matching DOC entities higher.
            # DA must have created these entities for this to work.
            doc_prefix = " ".join(f"DOC:{d}" for d in scope_docs[:4])
            enhanced_query = f"{doc_prefix} {query}"

    # ── Step 2: Retrieval ─────────────────────────────────────────────────────
    try:
        if use_hybrid:
            # Fast path: hybrid (vector+BM25 RRF), no LLM call
            mix_results = await query_hybrid(rag, query, top_k=top_k)
        else:
            # Full path: mix mode (entity VDB + relation VDB + KG traversal + BM25)
            mix_results = await query_mix_mode(rag, enhanced_query, top_k=top_k)
    except Exception:
        mix_results = []

    # Collect retrieved file basenames (filtered to corpus files)
    from wikigraph.drg import _is_corpus_file
    retrieved_files: set[str] = {
        os.path.basename(c.get("file_path", ""))
        for c in mix_results
        if c.get("file_path") and _is_corpus_file(os.path.basename(c.get("file_path", "")))
    }

    # ── Step 3: QSM forced-include (fast path only) ───────────────────────────
    # In fast path, QSM scope doesn't enhance entity VDB search (no mix mode).
    # Instead, directly inject chunks from QSM-known docs into results.
    qsm_forced: list[dict] = []
    if use_hybrid and scope_docs and drg is not None and drg.is_indexed():
        already_ids_qsm: set[str] = {
            c.get("chunk_id", c.get("id", "")) for c in mix_results
        }
        for doc_bn in scope_docs[:4]:
            chunk_ids = drg.get_doc_chunks(doc_bn)
            added = 0
            for cid in chunk_ids:
                if added >= drg_supplement_k:
                    break
                if cid in already_ids_qsm:
                    continue
                try:
                    cdata = rag.text_chunks._data.get(cid)
                    if cdata:
                        qsm_forced.append({
                            **cdata,
                            "chunk_id": cid,
                            "_qsm_forced": True,
                            "_qsm_doc": doc_bn,
                        })
                        already_ids_qsm.add(cid)
                        added += 1
                except Exception:
                    pass

    # ── Step 4: DRG supplement ────────────────────────────────────────────────
    supplement: list[dict] = []
    if drg is not None and drg.is_indexed() and retrieved_files:
        related = drg.get_related_docs(
            retrieved_files,
            top_n=5,
            min_weight=drg_min_weight,
        )
        already_ids: set[str] = {
            c.get("chunk_id", c.get("id", "")) for c in mix_results
        }
        for rel_doc, weight in related:
            chunk_ids = drg.get_doc_chunks(rel_doc)
            added = 0
            for cid in chunk_ids:
                if added >= drg_supplement_k:
                    break
                if cid in already_ids:
                    continue
                try:
                    cdata = rag.text_chunks._data.get(cid)
                    if cdata:
                        supplement.append({
                            **cdata,
                            "chunk_id": cid,
                            "_drg_supplement": True,
                            "_drg_weight": weight,
                            "_drg_source": rel_doc,
                        })
                        already_ids.add(cid)
                        added += 1
                except Exception:
                    pass

    # ── Step 5: QSM update ────────────────────────────────────────────────────
    all_retrieved = retrieved_files | {
        os.path.basename(c.get("file_path", ""))
        for c in supplement + qsm_forced
        if c.get("file_path") and _is_corpus_file(os.path.basename(c.get("file_path", "")))
    }

    if update_qsm:
        qsm.update(query, list(all_retrieved))

    # ── Step 6: DRG update ────────────────────────────────────────────────────
    if update_drg and drg is not None:
        drg.update(retrieved_files)  # update from BASE retrieval only (not supplement)

        # Online DCSG: inject KG edge for pairs that cross the threshold
        # Disabled in fast mode (use batch_evolve instead)
        if not use_hybrid and online_dcsg_threshold > 0:
            pending = drg_get_pending_online(drg, min_count=online_dcsg_threshold)
            if pending:
                asyncio.ensure_future(
                    _inject_online_dcsg(rag, drg, qsm, pending[:20])
                )

    return mix_results + qsm_forced + supplement


# ── Online DCSG injection ─────────────────────────────────────────────────────

def drg_get_pending_online(
    drg: "DocRelationGraph",
    min_count: int = 5,
    max_pairs: int = 50,
) -> list[tuple[str, str, int]]:
    """Return DRG edge pairs that exceed the online injection threshold."""
    pairs = []
    for key, data in drg._data["edges"].items():
        if data.get("count", 0) >= min_count and not data.get("dcsg_injected", False):
            parts = key.split("||", 1)
            if len(parts) == 2:
                pairs.append((parts[0], parts[1], data["count"]))
    return sorted(pairs, key=lambda x: -x[2])[:max_pairs]


async def _inject_online_dcsg(
    rag: "LightRAG",
    drg: "DocRelationGraph",
    qsm: "QueryStructuralMemory",
    pairs: list[tuple[str, str, int]],
):
    """
    Inject DCSG edges into KG live for DRG pairs that crossed the threshold.
    Fire-and-forget: doesn't block retrieval.

    Also marks pairs as dcsg_injected so they're not re-processed.
    """
    import time as _time
    from lightrag.utils import compute_mdhash_id

    GRAPH_FIELD_SEP = "<SEP>"
    graph = rag.chunk_entity_relation_graph

    doc_a_names = [f"DOC:{a}" for a, _, _ in pairs]
    doc_b_names = [f"DOC:{b}" for _, b, _ in pairs]
    all_doc_names = list(set(doc_a_names + doc_b_names))

    try:
        existing_nodes, existing_edges_map = await asyncio.gather(
            graph.get_nodes_batch(all_doc_names),
            graph.get_nodes_edges_batch(all_doc_names),
        )
    except Exception:
        return

    existing_pairs: set[tuple[str, str]] = set()
    for edges in existing_edges_map.values():
        for src, tgt in edges:
            existing_pairs.add(tuple(sorted((str(src), str(tgt)))))

    edge_list = []
    vdb_rel: dict[str, dict] = {}
    injected: list[tuple[str, str]] = []

    for a, b, cnt in pairs:
        doc_a = f"DOC:{a}"
        doc_b = f"DOC:{b}"
        pair_key = tuple(sorted((doc_a, doc_b)))
        if pair_key in existing_pairs:
            injected.append((a, b))
            continue

        node_a = existing_nodes.get(doc_a) or {}
        node_b = existing_nodes.get(doc_b) or {}
        src_combined = GRAPH_FIELD_SEP.join(
            s for s in [node_a.get("source_id", ""), node_b.get("source_id", "")]
            if s
        )
        description = (
            f"Online DRG→DCSG: documents co-retrieved {cnt} times. "
            f"Injected live during query session."
        )
        edge_list.append((doc_a, doc_b, {
            "weight": 0.65,
            "description": description,
            "keywords": "document-structure,drg-online",
            "source_id": src_combined or f"drg_online_{a}_{b}",
            "file_path": "wikigraph_drg_online",
            "created_at": int(_time.time()),
        }))
        rel_id = compute_mdhash_id(doc_a + doc_b, prefix="rel-")
        vdb_rel[rel_id] = {
            "src_id": doc_a,
            "tgt_id": doc_b,
            "source_id": src_combined or f"drg_online_{a}_{b}",
            "content": f"document-structure\t{doc_a}\n{doc_b}\n{description}",
            "keywords": "document-structure,drg-online",
            "description": description,
            "weight": 0.65,
            "file_path": "wikigraph_drg_online",
        }
        injected.append((a, b))

    if edge_list:
        try:
            await graph.upsert_edges_batch(edge_list)
            await graph.index_done_callback()
            if vdb_rel:
                await rag.relationships_vdb.upsert(vdb_rel)
        except Exception:
            pass

    # Mark injected in DRG so we don't re-inject
    for a, b in injected:
        key = f"{min(a, b)}||{max(a, b)}"
        if key in drg._data["edges"]:
            drg._data["edges"][key]["dcsg_injected"] = True

    if injected:
        qsm.mark_cooccurrences_confirmed(injected)
        qsm.save()
        drg.save()


# ── Batch warm-up functions ───────────────────────────────────────────────────

async def batch_update_qsm_drg(
    rag: "LightRAG",
    questions: list[dict],
    qsm: "QueryStructuralMemory",
    drg: "DocRelationGraph",
    top_k: int = 20,
) -> dict:
    """
    Run all questions through retrieval and update both QSM and DRG.

    Use this to warm up QSM + DRG before the first evaluation iteration.
    Also indexes doc chunks in DRG if not already done.

    Returns: {"queries": int, "qsm_patterns": int, "drg_edges": int, "drg_strong_edges": int}
    """
    from harness_pjt.benchmark.retrieval import query_mix_mode
    from wikigraph.drg import _is_corpus_file

    # Ensure DRG chunk index is built
    if not drg.is_indexed():
        print("[drg] Building document chunk index...")
        drg.index_doc_chunks(rag)
        print(f"[drg] Indexed {len(drg._data['doc_index'])} documents")

    completed = 0

    async def _one(q: dict):
        nonlocal completed
        q_text = q.get("question", "")
        try:
            results = await query_mix_mode(rag, q_text, top_k=top_k)
        except Exception:
            results = []
        docs = [
            os.path.basename(c.get("file_path", ""))
            for c in results
            if c.get("file_path") and _is_corpus_file(os.path.basename(c.get("file_path", "")))
        ]
        qsm.update(q_text, docs)
        drg.update(docs)
        completed += 1

    await asyncio.gather(*[_one(q) for q in questions])
    qsm.save()
    drg.save()

    stats = drg.stats
    return {
        "queries": completed,
        "qsm_patterns": len(qsm._data["query_routing"]),
        "drg_edges": stats["total_edges"],
        "drg_strong_edges": stats["edges_w05"],
    }


# Legacy compat: batch_update_qsm (used by old iterative_eval.py)
async def batch_update_qsm(
    rag: "LightRAG",
    questions: list[dict],
    qsm: "QueryStructuralMemory",
    top_k: int = 20,
) -> dict:
    """Backward-compatible wrapper: update QSM only (no DRG)."""
    from harness_pjt.benchmark.retrieval import query_mix_mode
    from wikigraph.drg import _is_corpus_file

    completed = 0

    async def _one(q: dict):
        nonlocal completed
        q_text = q.get("question", "")
        try:
            results = await query_mix_mode(rag, q_text, top_k=top_k)
        except Exception:
            results = []
        docs = [
            os.path.basename(c.get("file_path", ""))
            for c in results
            if c.get("file_path") and _is_corpus_file(os.path.basename(c.get("file_path", "")))
        ]
        qsm.update(q_text, docs)
        completed += 1

    await asyncio.gather(*[_one(q) for q in questions])
    qsm.save()
    return {
        "queries": completed,
        "patterns": len(qsm._data["query_routing"]),
        "pending_pairs": len(qsm.get_pending_cooccurrences(min_count=2)),
    }


# ── Sequential QSM+DRG evaluation ────────────────────────────────────────────

async def qsm_drg_evaluate(
    rag: "LightRAG",
    questions: list[dict],
    qsm: "QueryStructuralMemory",
    drg: "DocRelationGraph",
    top_k: int = 20,
    qsm_min_count: int = 2,
    drg_min_weight: float = 0.3,
) -> list[dict]:
    """
    Sequential evaluation using full agentic_retrieve (QSM + DRG).

    Runs questions one at a time so QSM+DRG warm up during the batch.
    Later questions benefit from earlier ones — demonstrates the progressive
    improvement property of the Agentic RAG system.

    Returns:
      list of {"id", "srcs", "chunks"} dicts (srcs = ordered file basenames)
    """
    results = []
    for q in questions:
        q_text = q.get("question", "")
        chunks = await agentic_retrieve(
            rag, q_text, qsm, drg,
            top_k=top_k,
            qsm_min_count=qsm_min_count,
            drg_min_weight=drg_min_weight,
            online_dcsg_threshold=5,
            update_qsm=True,
            update_drg=True,
        )
        srcs = list(dict.fromkeys(
            os.path.basename(c.get("file_path", ""))
            for c in chunks
            if c.get("file_path")
        ))
        results.append({
            "id": q.get("id", ""),
            "srcs": srcs,
            "chunks": chunks,
        })
    return results


# ── Legacy compat ─────────────────────────────────────────────────────────────

async def qsm_scoped_evaluate(
    rag: "LightRAG",
    questions: list[dict],
    qsm: "QueryStructuralMemory",
    top_k: int = 20,
    qsm_min_count: int = 2,
) -> list[dict]:
    """Legacy: evaluate with QSM scope only (no DRG supplement)."""
    results = []
    for q in questions:
        q_text = q.get("question", "")
        chunks = await agentic_retrieve(
            rag, q_text, qsm, drg=None,
            top_k=top_k,
            qsm_min_count=qsm_min_count,
            online_dcsg_threshold=5,
            update_qsm=True,
            update_drg=False,
        )
        srcs = list(dict.fromkeys(
            os.path.basename(c.get("file_path", ""))
            for c in chunks
            if c.get("file_path")
        ))
        results.append({
            "id": q.get("id", ""),
            "srcs": srcs,
            "chunks": chunks,
        })
    return results
