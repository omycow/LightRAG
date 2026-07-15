"""
Scenario 2 ver4 — Agentic RAG: Document Anchoring + Structural Graph EVOLVE
===========================================================================
Philosophy: EVOLVE learns only from accumulated query-retrieval patterns.
No ground truth. No document structure parsing. No data-type assumptions.

Algorithm: Three-layer Agentic EVOLVE
--------------------------------------
Problem: LightRAG builds entity-centric KGs from individual documents.
         Cross-document structural relationships are absent — only present
         in query patterns where multiple documents co-occur.

Strategy 8 — Document Anchoring (DA):
  Creates DOC:filename entities for EVERY document in the corpus.
  Each DOC entity has source_id = all chunks from that file and is indexed
  in entity VDB. Queries about document names/topics retrieve ALL doc chunks
  directly — independent of which entities LightRAG originally extracted.
  General: no filename convention, no hub/card taxonomy assumed.

Strategy 9 — Document Co-Structure Graph (DCSG):
  Builds document-level structural edges from query-retrieval patterns.
  Documents co-retrieved for the same queries share topical relevance.
  Edges: DOC:file_A -[CO_RETRIEVED_WITH]-> DOC:file_B
  Enables multi-hop structural traversal at document granularity.

Strategy 6 — Cross-Document Retrieval Bridge (CDRB):
  Entity-level complement to DCSG. Mines specific entity co-occurrence
  patterns for fine-grained cross-document connections.

Why WEIGHT chunk pick mode (not VECTOR):
  VECTOR mode filters entity-connected chunks by cosine similarity to the
  query. Document-level and cross-document edges link entities whose chunks
  may have low similarity to the query — VECTOR mode would discard them.
  WEIGHT mode allocates chunks by entity rank with min 1 chunk per entity,
  ensuring every reachable entity contributes to retrieval.
"""

import asyncio
import json
import os
import shutil
import sys
import time
import argparse
from collections import defaultdict, Counter
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR    = Path(__file__).resolve().parent
REPO_ROOT     = SCRIPT_DIR.parents[2]
GOLDEN_DIR    = REPO_ROOT / "harness_pjt" / "rag_data"
WORK_DIR      = SCRIPT_DIR / "rag_data"
RESULTS_DIR   = SCRIPT_DIR / "results"
STATE_FILE    = RESULTS_DIR / "state.json"

SERVICE_K     = 20   # top-k for scoring (actual service)
EVOLVE_K      = 50   # top-k for EVOLVE analysis (wider observation window)
TOP_K_LIST    = [5, 10, 15, 20]
CO_OCCUR_MIN  = 2    # minimum queries for entity pair co-occurrence to qualify

DATA_ROOT = os.environ.get(
    "DATA_ROOT",
    str(REPO_ROOT.parents[0] / "shs-poc-ragflow" / "data")
)

sys.path.insert(0, str(REPO_ROOT))


# ── State I/O ─────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"iterations": []}


def save_state(state: dict):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def ensure_working_copy():
    if WORK_DIR.exists():
        print(f"[setup] Working copy exists: {WORK_DIR}")
        return
    print(f"[setup] Copying golden RAG → {WORK_DIR}...")
    shutil.copytree(str(GOLDEN_DIR), str(WORK_DIR))
    print("[setup] Done.")


# ── CDRB EVOLVE ──────────────────────────────────────────────────────────────

GRAPH_FIELD_SEP = "<SEP>"  # from lightrag.constants


def _chunk_file_path(rag, chunk_id: str) -> str:
    """Look up file_path for a chunk from the text_chunks KV store."""
    try:
        entry = rag.text_chunks._data.get(chunk_id)
        if entry and isinstance(entry, dict):
            return entry.get("file_path", "")
    except Exception:
        pass
    return ""


async def _build_entity_index(
    rag,
) -> tuple[dict[str, list[str]], dict[str, str], dict[str, int]]:
    """
    Scan KG nodes and return:
      chunk_to_ents  : chunk_id → [entity_name, ...]
      entity_to_doc  : entity_name → primary source document file_path
      entity_doc_cnt : entity_name → # distinct documents it appears in

    entity_doc_cnt is used for specificity weighting:
      score(A,B) = count(A,B) / (doc_cnt(A) × doc_cnt(B))
    Entities specific to 1-2 documents score higher than hub entities
    that appear across dozens of documents.
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
                fp = _chunk_file_path(rag, cid)
                if fp:
                    entity_doc_set[eid].add(fp)
                    if eid not in entity_to_doc:
                        entity_to_doc[eid] = fp
    except Exception as e:
        print(f"  [WARN] entity index: {e}")
    entity_doc_cnt = {eid: len(docs) for eid, docs in entity_doc_set.items()}
    return dict(chunk_to_ents), entity_to_doc, entity_doc_cnt


async def cdrb_evolve(rag, questions: list, iter_num: int) -> int:
    """
    Cross-Document Retrieval Bridge (CDRB) EVOLVE.

    Principle:
      Queries that retrieve chunks from multiple documents reveal which
      documents are jointly relevant to a topic. CDRB mines these patterns
      to discover missing cross-document entity connections and adds them
      as KG edges.

    Only cross-document entity pairs are counted: if entities A and B both
    originate from the same source document, that edge is already present
    from LightRAG ingestion. CDRB targets only the inter-document connections
    that query patterns reveal but document-level ingestion cannot produce.

    Retrieval observation: service-equivalent (mix mode + service BM25), with
    a wider window (EVOLVE_K >> SERVICE_K) to capture weaker co-occurrence
    signals that would be cut off at the service top-K.

    After injection, graph traversal carries cross-document edges:
      entity A (found by VDB) → CDRB edge → entity B → B's source chunks
      → B's document appears in relation-retrieval results → top-K hit
    """
    import time as _time
    from harness_pjt.benchmark.retrieval import query_mix_mode, query_bm25

    graph = rag.chunk_entity_relation_graph

    print(f"  [cdrb] Building entity index (chunk→entity, entity→doc, entity→doc_count)...")
    chunk_to_ents, entity_to_doc, entity_doc_cnt = await _build_entity_index(rag)
    print(f"  [cdrb] {len(chunk_to_ents)} indexed chunks, {len(entity_to_doc)} entities with doc mapping")

    # Cross-document co-occurrence counter
    pair_counts: Counter = Counter()
    completed = 0

    async def _collect_one(q: dict):
        nonlocal completed
        q_text = q.get("question", "")

        # Service-equivalent retrieval with wider window
        try:
            mix_chunks = await query_mix_mode(rag, q_text, top_k=EVOLVE_K)
        except Exception as e:
            print(f"  [WARN] mix: {e}")
            mix_chunks = []
        bm25_chunks = query_bm25(rag, q_text, top_k=EVOLVE_K)

        # Map retrieved chunks → entities, grouped by source document
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
        if completed % 30 == 0 or completed == len(questions):
            print(f"  [cdrb] {completed}/{len(questions)} queries analyzed")

    print(f"  [cdrb] Collecting cross-document co-retrieval patterns (top-{EVOLVE_K})...")
    await asyncio.gather(*[_collect_one(q) for q in questions])

    # Specificity-weighted scoring: score = count / (doc_cnt_A × doc_cnt_B)
    # Entities specific to few documents score higher than hub entities
    # that appear across many documents (prevents hub-entity domination).
    candidate_pairs = []
    for (a, b), cnt in pair_counts.items():
        if cnt < CO_OCCUR_MIN:
            continue
        da = entity_doc_cnt.get(a, 1)
        db = entity_doc_cnt.get(b, 1)
        score = cnt / (da * db)
        candidate_pairs.append((a, b, cnt, score))

    cross_doc_count = len(pair_counts)
    print(f"  [cdrb] Cross-doc pairs observed: {cross_doc_count}, "
          f"qualifying (≥{CO_OCCUR_MIN}): {len(candidate_pairs)}")

    if not candidate_pairs:
        print("  [cdrb] No qualifying cross-document pairs found.")
        return 0

    # Sort by specificity-weighted score, take top-500
    sorted_pairs = sorted(candidate_pairs, key=lambda x: -x[3])[:500]

    all_entity_ids: set[str] = set()
    for a, b, _cnt, _score in sorted_pairs:
        all_entity_ids.add(a)
        all_entity_ids.add(b)

    print(f"  [cdrb] Fetching {len(all_entity_ids)} entity nodes + existing edges...")
    entity_nodes, existing_edges_by_node = await asyncio.gather(
        graph.get_nodes_batch(list(all_entity_ids)),
        graph.get_nodes_edges_batch(list(all_entity_ids)),
    )

    existing_pairs: set[tuple[str, str]] = set()
    for edges in existing_edges_by_node.values():
        for src, tgt in edges:
            existing_pairs.add(tuple(sorted((src, tgt))))

    edge_list: list[tuple[str, str, dict]] = []
    for a, b, cnt, score in sorted_pairs:
        if tuple(sorted((a, b))) in existing_pairs:
            continue

        node_a = entity_nodes.get(a) or {}
        node_b = entity_nodes.get(b) or {}
        a_src = node_a.get("source_id", "")
        b_src = node_b.get("source_id", "")
        # Both entity source chunks form the edge source_id.
        # During retrieval: entity A found → traverse edge → fetch A+B chunks.
        # A's chunks are already in entity_chunks (deduped); B's chunks are net-new.
        real_source_id = GRAPH_FIELD_SEP.join(s for s in [a_src, b_src] if s)

        doc_a = entity_to_doc.get(a, "?")
        doc_b = entity_to_doc.get(b, "?")
        edge_list.append((a, b, {
            "weight":      0.6,
            "description": (
                f"Cross-document co-retrieval: {cnt} queries retrieved both "
                f"{os.path.basename(doc_a)} and {os.path.basename(doc_b)}"
            ),
            "keywords":    "cross-document,cdrb-evolve",
            "source_id":   real_source_id or f"cdrb_iter{iter_num}",
            "file_path":   "wikigraph_cdrb",
            "created_at":  int(_time.time()),
        }))

    print(f"  [cdrb] New cross-document edges to inject: {len(edge_list)}")

    if edge_list:
        try:
            await graph.upsert_edges_batch(edge_list)
            await graph.index_done_callback()
            print(f"  [cdrb] Injected {len(edge_list)} CDRB edges (flushed to disk).")
        except Exception as e:
            print(f"  [ERROR] cdrb inject: {e}")
            return 0

    return len(edge_list)


# ── Multi-top_k evaluation ─────────────────────────────────────────────────────

async def evaluate_multi_topk(
    rag, questions: list, top_k_list: list, label: str
) -> dict:
    from harness_pjt.benchmark.retrieval import (
        query_vector, query_bm25, query_hybrid, query_mix_mode, get_retrieved_sources
    )
    from harness_pjt.benchmark.questions import source_hit

    max_k = max(top_k_list)
    per_topk: dict[int, list] = {tk: [] for tk in top_k_list}
    completed = 0

    async def _eval_one(q: dict):
        nonlocal completed
        q_text   = q["question"]
        expected = q.get("expected_sources", [])
        is_no_context = q.get("no_context", False)
        is_co         = q.get("category") == "co_retrieval"
        exp_card      = q.get("expected_card", "")
        exp_linked    = q.get("expected_linked", "")

        def _hit(srcs):
            if is_no_context:
                return len(srcs) == 0
            return source_hit(srcs, expected)

        def _co_hit(srcs):
            if not is_co or not exp_card:
                return None
            c_bn = os.path.basename(exp_card).lower()
            l_bn = os.path.basename(exp_linked).lower() if exp_linked else ""
            return (
                any(c_bn in s.lower() for s in srcs) and
                (any(l_bn in s.lower() for s in srcs) if l_bn else False)
            )

        try:
            vec_res      = await query_vector(rag, q_text, top_k=max_k)
            bm25_res     = query_bm25(rag, q_text, top_k=max_k)
            hyb_res      = await query_hybrid(rag, q_text, top_k=max_k)
            mix_bm25_raw = await query_mix_mode(rag, q_text, top_k=max_k)

            vec_srcs  = get_retrieved_sources(vec_res)
            bm25_srcs = get_retrieved_sources(bm25_res)
            hyb_srcs  = get_retrieved_sources(hyb_res)
            _graph    = [c["file_path"] for c in mix_bm25_raw if c.get("file_path")]
            mix_bm25_srcs = list(dict.fromkeys(_graph + bm25_srcs))

            rows = []
            for tk in top_k_list:
                mb = mix_bm25_srcs[:tk]
                rows.append((tk, {
                    "id":          q.get("id", ""),
                    "hybrid":      {"hit": _hit(hyb_srcs[:tk])},
                    "mix_bm25":    {"hit": _hit(mb)},
                    "vector":      {"hit": _hit(vec_srcs[:tk])},
                    "bm25":        {"hit": _hit(bm25_srcs[:tk])},
                    "mix_bm25_co": {"hit": _co_hit(mb)},
                }))
        except Exception as e:
            print(f"  [WARN] {q.get('id','')}: {e}")
            rows = [(tk, {
                "id": q.get("id",""),
                "hybrid": {"hit": False}, "mix_bm25": {"hit": False},
                "vector": {"hit": False}, "bm25": {"hit": False},
                "mix_bm25_co": {"hit": None},
            }) for tk in top_k_list]

        completed += 1
        if completed % 30 == 0 or completed == len(questions):
            print(f"  [{label}] {completed}/{len(questions)}")
        return rows

    all_rows = await asyncio.gather(*[_eval_one(q) for q in questions])
    for rows in all_rows:
        for tk, entry in rows:
            per_topk[tk].append(entry)

    scores = {}
    for tk in top_k_list:
        scores[f"top_k_{tk}"] = _metrics(per_topk[tk])
    return scores


def _metrics(results: list) -> dict:
    def _recall(mode):
        hits = sum(1 for r in results if r.get(mode, {}).get("hit", False))
        return round(hits / max(len(results), 1) * 100, 1)

    def _co_recall(mode):
        co = [r for r in results if r.get(mode, {}).get("hit") is not None]
        if not co:
            return None
        hits = sum(1 for r in co if r[mode]["hit"])
        return round(hits / len(co) * 100, 1)

    return {
        "hybrid":      _recall("hybrid"),
        "mix_bm25":    _recall("mix_bm25"),
        "mix_bm25_co": _co_recall("mix_bm25_co"),
        "vector":      _recall("vector"),
        "bm25":        _recall("bm25"),
    }


# ── Core iteration ─────────────────────────────────────────────────────────────

async def evaluate_agentic_val_b(rag, questions: list, qsm, drg=None, top_k: int = 20) -> dict:
    """
    Sequential Agentic RAG evaluation for val_B co_hit metric.

    Uses agentic_retrieve (QSM scope + DRG supplement) running questions one-by-one.
    QSM and DRG update after each question, so later questions benefit from earlier ones.
    This shows the progressive improvement ("gets better with use") property.

    Returns:
      {f"top_k_{tk}": {"mix_bm25_co_qsm": float}} per top_k value
    """
    from harness_pjt.wikigraph.adaptive_retriever import agentic_retrieve

    co_hits_at = {tk: 0 for tk in TOP_K_LIST}
    co_total = 0

    for q in questions:
        q_text   = q["question"]
        exp_card = q.get("expected_card", "")
        exp_link = q.get("expected_linked", "")
        is_co    = q.get("category") == "co_retrieval" and exp_card

        chunks = await agentic_retrieve(
            rag, q_text, qsm, drg,
            top_k=max(TOP_K_LIST),
            qsm_min_count=2,
            drg_min_weight=0.3,
            drg_supplement_k=3,
            online_dcsg_threshold=5,
            update_qsm=True,
            update_drg=(drg is not None),
        )
        # srcs ordered by first-seen file (preserves retrieval rank)
        srcs = list(dict.fromkeys(
            os.path.basename(c.get("file_path", "")) for c in chunks if c.get("file_path")
        ))

        if is_co:
            co_total += 1
            c_bn = os.path.basename(exp_card).lower()
            l_bn = os.path.basename(exp_link).lower() if exp_link else ""
            for tk in TOP_K_LIST:
                tk_srcs = srcs[:tk]
                hit = (
                    any(c_bn in s.lower() for s in tk_srcs) and
                    (any(l_bn in s.lower() for s in tk_srcs) if l_bn else False)
                )
                if hit:
                    co_hits_at[tk] += 1

    result = {}
    for tk in TOP_K_LIST:
        co_hit = round(co_hits_at[tk] / max(co_total, 1) * 100, 1) if co_total else None
        result[f"top_k_{tk}"] = {"mix_bm25_co_qsm": co_hit}

    return result


async def run_iteration(iter_num: int, enable_llm_cache: bool = True):
    from harness_pjt.benchmark.run_benchmark import build_rag
    from harness_pjt.benchmark.questions import (
        load_all_data_questions, load_validation_set_a, load_validation_set_b
    )

    cache_str = "ON" if enable_llm_cache else "OFF"
    print(f"\n{'='*60}")
    print(f"  Iteration {iter_num}  |  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Mode: kg_chunk_pick_method=WEIGHT  |  LLM cache: {cache_str}")
    print(f"{'='*60}")

    rag, _ = await build_rag(
        str(WORK_DIR), kg_chunk_pick_method="WEIGHT",
        enable_llm_cache=enable_llm_cache,
    )

    q_all = load_all_data_questions(DATA_ROOT)
    q_va  = load_validation_set_a(DATA_ROOT)
    q_vb  = load_validation_set_b(DATA_ROOT)
    print(f"[eval] Questions: all={len(q_all)} val_A={len(q_va)} val_B={len(q_vb)}")

    # ── Load QSM + DRG (accumulated from previous iterations) ─────────────────
    from harness_pjt.wikigraph.qsm import QueryStructuralMemory
    from harness_pjt.wikigraph.drg import DocRelationGraph
    qsm = QueryStructuralMemory(str(WORK_DIR))
    drg = DocRelationGraph(str(WORK_DIR))
    qsm_stats = qsm.stats
    drg_stats = drg.stats
    print(f"[qsm] Loaded: patterns={qsm_stats['query_patterns']} "
          f"docs={qsm_stats['docs_indexed']} pending_pairs={qsm_stats['pending_cooc_pairs']}")
    print(f"[drg] Loaded: total_edges={drg_stats['total_edges']} "
          f"strong(≥0.5)={drg_stats['edges_w05']} indexed_docs={drg_stats['docs_indexed']}")

    # Ensure DRG chunk index is populated
    if not drg.is_indexed():
        print("[drg] Building document chunk index (first run)...")
        drg.index_doc_chunks(rag)
        drg.save()
        print(f"[drg] Indexed {drg.stats['docs_indexed']} documents")

    # ── 1. Service evaluation (top-20) ────────────────────────────────────────
    print(f"\n[eval] Service evaluation (top_k ∈ {TOP_K_LIST})...")
    s_all = await evaluate_multi_topk(rag, q_all, TOP_K_LIST, "all_data")
    s_va  = await evaluate_multi_topk(rag, q_va,  TOP_K_LIST, "val_A")
    s_vb  = await evaluate_multi_topk(rag, q_vb,  TOP_K_LIST, "val_B")

    scores = {}
    for tk in TOP_K_LIST:
        key = f"top_k_{tk}"
        scores[key] = {
            "all_data": s_all[key],
            "val_A":    s_va[key],
            "val_B":    s_vb[key],
        }
        m  = scores[key]["all_data"]
        co = scores[key]["val_B"].get("mix_bm25_co")
        co_str = f"  val_B_co={co}%" if co is not None else ""
        print(f"  top_k={tk:2d}: mix_bm25={m['mix_bm25']}% bm25={m['bm25']}%{co_str}")

    # ── 1b. Agentic val_B evaluation (sequential QSM+DRG, shows warm-up effect) ─
    qsm_val_b_scores = {}
    has_patterns = qsm_stats["query_patterns"] > 0 or drg_stats["total_edges"] > 0
    if has_patterns:
        mode = "QSM+DRG" if drg_stats["edges_w05"] > 0 else "QSM"
        print(f"\n[agentic-eval] {mode} val_B (sequential, top-{SERVICE_K})...")
        try:
            qsm_res = await evaluate_agentic_val_b(
                rag, q_vb, qsm, drg, top_k=SERVICE_K
            )
            for tk in TOP_K_LIST:
                key = f"top_k_{tk}"
                qsm_co = qsm_res.get(key, {}).get("mix_bm25_co_qsm")
                qsm_val_b_scores[key] = qsm_co
                baseline_co = scores[key]["val_B"].get("mix_bm25_co")
                if qsm_co is not None and baseline_co is not None:
                    delta = round(qsm_co - baseline_co, 1)
                    sign = "+" if delta >= 0 else ""
                    drg_note = f" [DRG:{drg_stats['edges_w05']}strong]" if drg_stats["edges_w05"] else ""
                    print(f"  top_k={tk:2d}: agentic co={qsm_co}% (baseline={baseline_co}%, Δ={sign}{delta}%){drg_note}")
        except Exception as e:
            print(f"  [WARN] agentic eval: {e}")
            import traceback; traceback.print_exc()
    else:
        print("\n[agentic-eval] Skipped (QSM+DRG empty, will populate after EVOLVE)")

    # ── 2. Agentic EVOLVE (DA + DCSG + CDRB + DRG bootstrap) ────────────────
    print(f"\n[evolve] Agentic EVOLVE: DA + DCSG + CDRB + DRG (top-{EVOLVE_K})...")
    total_mutations = 0
    da_updates = 0
    dcsg_edges = 0
    cdrb_edges = 0
    drg_edges_added = 0
    try:
        from harness_pjt.wikigraph.agent import WikiGraphAgent
        agent = WikiGraphAgent(rag)
        result = await agent.batch_evolve(
            q_all,
            evolve_k=EVOLVE_K,
            co_occur_min=CO_OCCUR_MIN,
            max_edges=500,
            qsm=qsm,
            drg=drg,
        )
        total_mutations   = result["edges_added"]
        da_updates        = result.get("da_updates", 0)
        dcsg_edges        = result.get("dcsg_edges", 0)
        cdrb_edges        = result.get("cdrb_edges", 0)
        drg_edges_added   = result.get("drg_edges", 0)
        for msg in result["messages"]:
            print(f"  {msg}")
        print(f"  [evolve] DA={da_updates} | DCSG={dcsg_edges} | CDRB={cdrb_edges} | DRG_new={drg_edges_added}")
    except Exception as e:
        import traceback
        print(f"  [ERROR] evolve: {e}")
        traceback.print_exc()

    # ── 3. QSM+DRG batch update — warm up for next iteration ─────────────────
    print(f"\n[qsm+drg] Batch update: recording query-retrieval patterns...")
    try:
        from harness_pjt.wikigraph.adaptive_retriever import batch_update_qsm_drg
        upd = await batch_update_qsm_drg(rag, q_all, qsm, drg, top_k=SERVICE_K)
        print(f"  [qsm+drg] queries={upd['queries']} "
              f"qsm_patterns={upd['qsm_patterns']} "
              f"drg_edges={upd['drg_edges']} drg_strong={upd['drg_strong_edges']}")
    except Exception as e:
        print(f"  [WARN] QSM+DRG update: {e}")

    await rag.finalize_storages()

    drg_final = drg.stats
    return {
        "iter":              iter_num,
        "timestamp":         time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_mutations":   total_mutations,
        "da_updates":        da_updates,
        "dcsg_edges":        dcsg_edges,
        "cdrb_edges":        cdrb_edges,
        "drg_edges":         drg_final["total_edges"],
        "drg_strong_edges":  drg_final["edges_w05"],
        "agentic_val_b_co20": qsm_val_b_scores.get("top_k_20"),
        "scores":            scores,
    }


# ── Graph generation ───────────────────────────────────────────────────────────

def make_graphs(state: dict):
    iters = state["iterations"]
    if not iters:
        return

    iter_nums = [x["iter"] for x in iters]

    def _series(dataset, metric):
        return [
            (it["scores"].get("top_k_20", {}).get(dataset, {}).get(metric) or 0)
            for it in iters
        ]

    # Graph A: Score vs iteration
    fig, ax = plt.subplots(figsize=(10, 5))
    for (ds, met, color, label, ls) in [
        ("all_data", "mix_bm25",    "#2196F3", "All-Data mix+BM25",          "-"),
        ("val_A",    "mix_bm25",    "#4CAF50", "Val-A mix+BM25",             "-"),
        ("val_B",    "mix_bm25_co", "#FF9800", "Val-B co-retrieval (AND)",   "--"),
    ]:
        vals = _series(ds, met)
        ax.plot(iter_nums, vals, marker="o", color=color, linewidth=2,
                label=label, linestyle=ls)
        for x, y in zip(iter_nums, vals):
            if y:
                ax.annotate(f"{y}%", (x, y), textcoords="offset points",
                            xytext=(0, 8), ha="center", fontsize=8, color=color)

    # Also plot agentic co_hit if available
    agentic_vals = [
        it.get("agentic_val_b_co20") for it in iters
    ]
    if any(v is not None for v in agentic_vals):
        valid_x = [n for n, v in zip(iter_nums, agentic_vals) if v is not None]
        valid_y = [v for v in agentic_vals if v is not None]
        ax.plot(valid_x, valid_y, marker="*", color="#9C27B0", linewidth=2,
                linestyle=":", markersize=10, label="Val-B Agentic (QSM+DRG)")
        for x, y in zip(valid_x, valid_y):
            ax.annotate(f"{y}%", (x, y), textcoords="offset points",
                        xytext=(5, 0), ha="left", fontsize=8, color="#9C27B0")

    ax.set_xlabel("Iteration", fontsize=11)
    ax.set_ylabel("Recall (%)", fontsize=11)
    ax.set_title(
        "A. Score vs EVOLVE iterations (Agentic RAG: DA + DCSG + CDRB + DRG)\n"
        "Document Anchoring → Structural Graph → Entity Bridges → DRG Supplement",
        fontsize=11
    )
    ax.legend(fontsize=9)
    ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.set_ylim(0, 105)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(str(RESULTS_DIR / "graph_A_score_vs_iteration.png"), dpi=150)
    plt.close(fig)
    print("[graph] graph_A_score_vs_iteration.png saved")

    # Graph B: Token efficiency
    fig, ax = plt.subplots(figsize=(9, 5))
    n = max(len(iters), 1)
    cmap_vals = [0.35 + 0.55 * i / max(n - 1, 1) for i in range(n)]
    colors_b = plt.cm.Blues(cmap_vals)

    for i, it in enumerate(iters):
        co_hits = []
        for tk in TOP_K_LIST:
            s = it["scores"].get(f"top_k_{tk}", {}).get("val_B", {})
            co = s.get("mix_bm25_co")
            co_hits.append(co if co is not None else 0)
        label = f"Iter {it['iter']} ({it['total_mutations']} edges)"
        ax.plot(TOP_K_LIST, co_hits, marker="o", label=label,
                color=colors_b[i], linewidth=2)
        for x, y in zip(TOP_K_LIST, co_hits):
            if y:
                ax.annotate(f"{y}%", (x, y), textcoords="offset points",
                            xytext=(0, 7), ha="center", fontsize=7)

    ax.set_xlabel("top_k  (∝ retrieval tokens)", fontsize=11)
    ax.set_ylabel("Val-B co-retrieval recall (%)", fontsize=11)
    ax.set_title(
        f"B. Co-retrieval efficiency vs top_k per iteration (WEIGHT mode)\n"
        f"Agentic EVOLVE: Document Anchoring + Structural Graph + Entity Bridges",
    )
    ax.legend(fontsize=9)
    ax.set_ylim(0, 105)
    ax.set_xticks(TOP_K_LIST)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(str(RESULTS_DIR / "graph_B_topk_efficiency.png"), dpi=150)
    plt.close(fig)
    print("[graph] graph_B_topk_efficiency.png saved")


# ── Markdown report ────────────────────────────────────────────────────────────

def make_report(state: dict):
    iters = state["iterations"]
    lines = [
        "# Scenario 2 — Evolving RAG Benchmark (ver4)",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}  |  Iterations: {len(iters)}",
        "",
        "## EVOLVE Strategy: Agentic RAG (DA + DCSG + CDRB) + WEIGHT Chunk Pick Mode",
        "",
        f"- **Service evaluation**: top-{SERVICE_K} (actual retrieval)",
        f"- **EVOLVE analysis**: top-{EVOLVE_K} (wider observation window)",
        f"- **Evidence threshold**: {CO_OCCUR_MIN}+ queries for co-occurrence to qualify",
        f"- **Chunk pick mode**: WEIGHT (rank-based, min 1 chunk per entity)",
        "",
        "### EVOLVE Architecture",
        "**Strategy 8 — Document Anchoring (DA)**:",
        "Creates DOC:filename entities for ALL documents with source_id = all chunks.",
        "Inserted into entity VDB for direct document-level retrieval.",
        "General: no filename convention or hub/card taxonomy assumed.",
        "",
        "**Strategy 9 — Document Co-Structure Graph (DCSG)**:",
        "Adds DOC-level structural edges from query-retrieval co-occurrence patterns.",
        "DOC:file_A ↔ DOC:file_B edges enable document-level multi-hop traversal.",
        "",
        "**Strategy 6 — Cross-Document Retrieval Bridge (CDRB)**:",
        "Entity-level complement: specific entity-pair cross-document connections.",
        "",
        "## Graphs",
        "![Graph A](graph_A_score_vs_iteration.png)",
        "![Graph B](graph_B_topk_efficiency.png)",
        "",
        "## Iteration Results",
        "",
        "| Iter | Edges | All@20 | ValA@20 | ValB co@20 | ValB co@10 | ValB co@5 |",
        "|------|-------|--------|---------|------------|------------|-----------|",
    ]
    for it in iters:
        s = it["scores"]
        def r(tk, ds, metric="mix_bm25"):
            v = s.get(f"top_k_{tk}", {}).get(ds, {}).get(metric)
            return f"{v}%" if v is not None else "-"
        lines.append(
            f"| {it['iter']} | {it['total_mutations']} "
            f"| {r(20,'all_data')} | {r(20,'val_A')} "
            f"| {r(20,'val_B','mix_bm25_co')} "
            f"| {r(10,'val_B','mix_bm25_co')} "
            f"| {r(5,'val_B','mix_bm25_co')} |"
        )

    if len(iters) >= 2:
        first = iters[0]["scores"].get("top_k_20", {}).get("val_B", {}).get("mix_bm25_co", 0) or 0
        last  = iters[-1]["scores"].get("top_k_20", {}).get("val_B", {}).get("mix_bm25_co", 0) or 0
        delta = round(last - first, 1)
        sign  = "+" if delta >= 0 else ""
        total = sum(it["total_mutations"] for it in iters)
        total_da = sum(it.get("da_updates", 0) for it in iters)
        total_dcsg = sum(it.get("dcsg_edges", 0) for it in iters)
        total_cdrb = sum(it.get("cdrb_edges", 0) for it in iters)
        lines += [
            "",
            "## Summary",
            f"- DA doc-anchors: **{total_da}** | DCSG doc-edges: **{total_dcsg}** | CDRB entity-edges: **{total_cdrb}**",
            f"- Total edges injected: **{total}**",
            f"- Val-B co@20: **{first}% → {last}% ({sign}{delta}%)**",
        ]

    (RESULTS_DIR / "summary_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("[report] summary_report.md saved")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--graphs-only", action="store_true")
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Disable LLM keyword-extraction cache so fresh keywords are used after EVOLVE",
    )
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()

    if args.graphs_only:
        make_graphs(state)
        make_report(state)
        return

    ensure_working_copy()

    iter_num = len(state["iterations"]) + 1
    print(f"\n[start] Iteration {iter_num} | work_dir={WORK_DIR} | mode=WEIGHT")

    result = asyncio.run(run_iteration(iter_num, enable_llm_cache=not args.no_cache))

    state["iterations"].append(result)
    save_state(state)

    make_graphs(state)
    make_report(state)

    print(f"\n[done] Iteration {iter_num} complete.")
    print(f"  State:  {STATE_FILE}")
    print(f"  Report: {RESULTS_DIR}/summary_report.md")


if __name__ == "__main__":
    main()
