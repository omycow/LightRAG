"""
Scenario 2 ver3 — Strategy 1-3 EVOLVE with top-50 analysis window
==================================================================
Philosophy: EVOLVE learns only from query retrieval patterns.
No ground truth. No document structure parsing.

Change from ver1:
  ver1: entity edges targeted at val_B file pairs (used ground truth file mapping)
  ver3: entity co-retrieval edges from top-50 per-query analysis across ALL questions
        (no file-type filtering, no pre-specified pairs, no ground truth)

EVOLVE algorithm (Strategy 1 extended to wider retrieval window):
  1. Service evaluation: top-20 retrieval (score computation)
  2. EVOLVE analysis: top-50 retrieval per query (wider observation window)
  3. For each query: collect entity names from all top-50 retrieved chunks
  4. Count (entity_a, entity_b) co-occurrences across all queries
  5. Pairs with count >= CO_OCCUR_MIN and no existing edge → add entity edge
  6. Apply Strategy 3 (multi-hop shortcut edges) from collected entity data

This directly extends Strategy 1 from the WikiGraph EVOLVE algorithm to use
a wider retrieval window, improving cross-document entity co-retrieval detection.

Expected outcome:
  - More cross-document entity pairs discovered (wider window captures more context)
  - Graph becomes more connected across document boundaries
  - May improve general retrieval; co_hit improvement depends on mix mode behavior
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


# ── EVOLVE: Strategy 1 with top-50 ────────────────────────────────────────────

async def _build_chunk_entity_map(rag) -> dict[str, list[str]]:
    """Build chunk_id → [entity_name, ...] map by scanning all graph nodes."""
    chunk_to_entities: dict[str, list[str]] = defaultdict(list)
    try:
        all_nodes = await rag.chunk_entity_relation_graph.get_all_nodes()
        for node in all_nodes:
            eid = node.get("id") or node.get("entity_name", "")
            if not eid:
                continue
            src = node.get("source_id", "")
            for cid in (c.strip() for c in src.split("<SEP>") if c.strip()):
                chunk_to_entities[cid].append(eid)
    except Exception as e:
        print(f"  [WARN] chunk-entity map: {e}")
    return dict(chunk_to_entities)


async def wide_coretrieval_evolve(rag, questions: list, iter_num: int) -> int:
    """
    EVOLVE Strategy 1 with top-50 analysis window.

    For each query, retrieve top-EVOLVE_K chunks.
    From those chunks, collect associated entities via the knowledge graph.
    Count (entity_A, entity_B) co-occurrences across all queries.
    Add edges for pairs that:
      1. Co-appear in CO_OCCUR_MIN+ different queries (evidence threshold)
      2. Do NOT already have a direct edge in the graph (only fill gaps)

    This is identical in philosophy to WikiGraph Strategy 1 but uses a wider
    retrieval window to capture cross-document entity relationships that the
    service window (top-20) might miss.
    """
    from harness_pjt.benchmark.retrieval import query_mix_mode

    print(f"  [evolve] Building chunk→entity map...")
    chunk_to_ents = await _build_chunk_entity_map(rag)
    print(f"  [evolve] {len(chunk_to_ents)} chunks with entity data")

    pair_counts: Counter = Counter()
    completed = 0

    async def _collect_one(q: dict):
        nonlocal completed
        q_text = q.get("question", "")
        try:
            retrieved = await query_mix_mode(rag, q_text, top_k=EVOLVE_K)
        except Exception as e:
            print(f"  [WARN] query: {e}")
            return

        # Collect all entities from top-EVOLVE_K chunks
        entities: list[str] = []
        for chunk in retrieved:
            cid = chunk.get("chunk_id", "")
            if cid:
                entities.extend(chunk_to_ents.get(cid, []))
        entities = list(set(entities))

        # Count entity pair co-occurrences within this query's top-50
        for i, ea in enumerate(entities):
            for eb in entities[i + 1:]:
                pair = tuple(sorted((ea, eb)))
                pair_counts[pair] += 1

        completed += 1
        if completed % 30 == 0 or completed == len(questions):
            print(f"  [evolve] {completed}/{len(questions)} queries analyzed")

    print(f"  [evolve] Collecting entity co-retrieval data (top-{EVOLVE_K})...")
    await asyncio.gather(*[_collect_one(q) for q in questions])

    # Filter: pairs with enough co-occurrence evidence
    candidate_pairs = [
        (a, b, cnt) for (a, b), cnt in pair_counts.items()
        if cnt >= CO_OCCUR_MIN
    ]
    print(f"  [evolve] Candidate pairs (≥{CO_OCCUR_MIN} co-occurrences): {len(candidate_pairs)}")

    if not candidate_pairs:
        print("  [evolve] No qualifying pairs found.")
        return 0

    # Check graph and add edges for unconnected qualifying pairs
    relationships: list[dict] = []
    graph = rag.chunk_entity_relation_graph

    # Limit to avoid combinatorial explosion
    sorted_pairs = sorted(candidate_pairs, key=lambda x: -x[2])
    checked = 0
    for a, b, cnt in sorted_pairs[:500]:  # check top-500 most frequent pairs
        try:
            has_ab = await graph.has_edge(a, b)
            has_ba = await graph.has_edge(b, a)
        except Exception:
            continue
        if not has_ab and not has_ba:
            relationships.append({
                "src_id":     a,
                "tgt_id":     b,
                "description": f"Co-retrieved in top-{EVOLVE_K} across {cnt} queries",
                "keywords":   "co-retrieval,evolve-wide",
                "weight":     0.6,
                "source_id":  f"wikigraph_evolve_v3_iter{iter_num}",
            })
        checked += 1

    print(f"  [evolve] New edges to inject: {len(relationships)} / {checked} checked")

    if relationships:
        try:
            await rag.ainsert_custom_kg({
                "chunks":        [],
                "entities":      [],
                "relationships": relationships,
            })
            print(f"  [evolve] Injected {len(relationships)} entity edges.")
        except Exception as e:
            print(f"  [ERROR] ainsert_custom_kg: {e}")
            return 0

    return len(relationships)


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

async def run_iteration(iter_num: int):
    from harness_pjt.benchmark.run_benchmark import build_rag
    from harness_pjt.benchmark.questions import (
        load_all_data_questions, load_validation_set_a, load_validation_set_b
    )

    print(f"\n{'='*60}")
    print(f"  Iteration {iter_num}  |  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    rag, _ = await build_rag(str(WORK_DIR))

    q_all = load_all_data_questions(DATA_ROOT)
    q_va  = load_validation_set_a(DATA_ROOT)
    q_vb  = load_validation_set_b(DATA_ROOT)
    print(f"[eval] Questions: all={len(q_all)} val_A={len(q_va)} val_B={len(q_vb)}")

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

    # ── 2. EVOLVE analysis (top-50) ───────────────────────────────────────────
    print(f"\n[evolve] Strategy-1 entity co-retrieval (top-{EVOLVE_K} analysis window)...")
    total_mutations = 0
    try:
        applied = await wide_coretrieval_evolve(rag, q_all, iter_num)
        total_mutations += applied
    except Exception as e:
        print(f"  [ERROR] evolve: {e}")

    await rag.finalize_storages()

    return {
        "iter":            iter_num,
        "timestamp":       time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_mutations": total_mutations,
        "scores":          scores,
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
    fig, ax = plt.subplots(figsize=(9, 5))
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

    ax.set_xlabel("Iteration", fontsize=11)
    ax.set_ylabel("Recall (%)", fontsize=11)
    ax.set_title(
        f"A. Score vs EVOLVE iterations (Strategy-1 with top-{EVOLVE_K})\n"
        "(entity co-retrieval edges, no ground truth)",
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
        f"B. Co-retrieval efficiency vs top_k per iteration\n"
        f"(does wider-window entity EVOLVE shift the curve left?)",
        fontsize=11
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
        "# Scenario 2 — Evolving RAG Benchmark (ver3)",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}  |  Iterations: {len(iters)}",
        "",
        "## EVOLVE Strategy: Strategy-1 with Extended Top-50 Window",
        "",
        f"- **Service evaluation**: top-{SERVICE_K} (actual retrieval)",
        f"- **EVOLVE analysis**: top-{EVOLVE_K} (wider observation window)",
        f"- **Evidence threshold**: {CO_OCCUR_MIN}+ queries for entity pair co-occurrence",
        "",
        "### Philosophy",
        "EVOLVE learns only from retrieval behavior. No ground truth. No document structure parsing.",
        "Extending Strategy 1 (entity co-retrieval edges) to use top-50 instead of top-20",
        "captures cross-document entity relationships that the service window misses.",
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
        lines += [
            "",
            "## Summary",
            f"- Total entity edges injected: **{total}**",
            f"- Val-B co@20: **{first}% → {last}% ({sign}{delta}%)**",
        ]

    (RESULTS_DIR / "summary_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("[report] summary_report.md saved")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--graphs-only", action="store_true")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()

    if args.graphs_only:
        make_graphs(state)
        make_report(state)
        return

    ensure_working_copy()

    iter_num = len(state["iterations"]) + 1
    print(f"\n[start] Iteration {iter_num} | work_dir={WORK_DIR}")

    result = asyncio.run(run_iteration(iter_num))

    state["iterations"].append(result)
    save_state(state)

    make_graphs(state)
    make_report(state)

    print(f"\n[done] Iteration {iter_num} complete.")
    print(f"  State:  {STATE_FILE}")
    print(f"  Report: {RESULTS_DIR}/summary_report.md")


if __name__ == "__main__":
    main()
