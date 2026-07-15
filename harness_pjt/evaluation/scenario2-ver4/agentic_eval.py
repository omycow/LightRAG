"""
Agentic RAG Evaluation — Progressive graph improvement from scratch.
=====================================================================

Philosophy:
  Start from GOLDEN LightRAG (unmodified).
  Run benchmark questions sequentially through the Agentic RAG system.
  The system improves the graph in the background as queries accumulate.
  No pre-processing. No ground truth. No manual labeling.

What this script shows:
  - Window 1 (Q1-30):   Baseline retrieval, DRG learning begins
  - EVOLVE checkpoint:  After 50 queries → DA+DCSG+CDRB fires on accumulated patterns
  - Window 2 (Q31-60):  DRG stronger, KG structurally improved
  - Window 3 (Q61-90):  Full benefit — DRG mature, KG edges active

Across multiple runs:
  - Run 1: starts cold, improves during run
  - Run 2: DRG+QSM from Run 1 available → immediate improvement from Q1
  - Run N: graph is well-structured, high stable score

Usage:
  # First run (golden start, clears working copy)
  python3 agentic_eval.py --fresh

  # Subsequent run (use previously learned state)
  python3 agentic_eval.py

  # Just generate graphs from saved state
  python3 agentic_eval.py --graphs-only
"""

import asyncio
import json
import os
import shutil
import sys
import time
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).resolve().parent
REPO_ROOT   = SCRIPT_DIR.parents[2]
GOLDEN_DIR  = REPO_ROOT / "harness_pjt" / "rag_data"
WORK_DIR    = SCRIPT_DIR / "rag_data"
RESULTS_DIR = SCRIPT_DIR / "results"
STATE_FILE  = RESULTS_DIR / "agentic_state.json"

TOP_K_LIST  = [5, 10, 15, 20]
EVOLVE_EVERY = 50   # trigger EVOLVE after every N queries (0 = disable)
SERVICE_K   = 20

DATA_ROOT = os.environ.get(
    "DATA_ROOT",
    str(REPO_ROOT.parents[0] / "shs-poc-ragflow" / "data")
)

sys.path.insert(0, str(REPO_ROOT))


# ── State I/O ─────────────────────────────────────────────────────────────────

def load_agentic_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"runs": []}


def save_agentic_state(state: dict):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def ensure_working_copy(fresh: bool = False):
    """Copy golden RAG to working directory. If fresh=True, wipe first."""
    if fresh and WORK_DIR.exists():
        print(f"[setup] --fresh: removing working copy {WORK_DIR}...")
        shutil.rmtree(str(WORK_DIR))

    if not WORK_DIR.exists():
        print(f"[setup] Copying golden RAG → {WORK_DIR}...")
        shutil.copytree(str(GOLDEN_DIR), str(WORK_DIR))
        print("[setup] Done — fresh start from golden LightRAG")
    else:
        print(f"[setup] Reusing working copy {WORK_DIR}")


# ── Co-hit metric ──────────────────────────────────────────────────────────────

def compute_co_hit(srcs: list[str], exp_card: str, exp_linked: str, top_k: int) -> bool:
    tk_srcs = srcs[:top_k]
    c_bn = os.path.basename(exp_card).lower()
    l_bn = os.path.basename(exp_linked).lower() if exp_linked else ""
    return (
        any(c_bn in s.lower() for s in tk_srcs) and
        (any(l_bn in s.lower() for s in tk_srcs) if l_bn else False)
    )


# ── Main agentic evaluation loop ──────────────────────────────────────────────

async def run_agentic_eval():
    from harness_pjt.benchmark.run_benchmark import build_rag
    from harness_pjt.benchmark.questions import load_validation_set_b
    from harness_pjt.wikigraph.agent import WikiGraphAgent
    from harness_pjt.wikigraph.qsm import QueryStructuralMemory
    from harness_pjt.wikigraph.drg import DocRelationGraph

    rag, _ = await build_rag(str(WORK_DIR), kg_chunk_pick_method="WEIGHT")

    q_vb = load_validation_set_b(DATA_ROOT)
    co_questions = [q for q in q_vb if q.get("category") == "co_retrieval" and q.get("expected_card")]
    print(f"[eval] Val-B co-retrieval questions: {len(co_questions)}")

    # Load / initialize QSM + DRG
    qsm = QueryStructuralMemory(str(WORK_DIR))
    drg = DocRelationGraph(str(WORK_DIR))

    qsm_stats = qsm.stats
    drg_stats = drg.stats
    print(f"[qsm] patterns={qsm_stats['query_patterns']} "
          f"pending_pairs={qsm_stats['pending_cooc_pairs']}")
    print(f"[drg] edges={drg_stats['total_edges']} "
          f"strong(≥0.5)={drg_stats['edges_w05']} "
          f"indexed_docs={drg_stats['docs_indexed']}")

    # Ensure DRG chunk index is built
    if not drg.is_indexed():
        print("[drg] Building document chunk index...")
        drg.index_doc_chunks(rag)
        drg.save()
        print(f"[drg] Indexed {drg.stats['docs_indexed']} docs")

    # Create agent and attach memories
    agent = WikiGraphAgent(rag, evolve_every=EVOLVE_EVERY)
    agent.attach_memories(qsm, drg)

    # Per-question results
    results = []
    t0 = time.time()

    print(f"\n[eval] Running {len(co_questions)} val-B questions sequentially (Agentic RAG)...")
    print(f"       EVOLVE triggers every {EVOLVE_EVERY} queries\n")

    for i, q in enumerate(co_questions, 1):
        q_text   = q["question"]
        exp_card = q.get("expected_card", "")
        exp_link = q.get("expected_linked", "")

        ret = await agent.agentic_query(
            q_text,
            top_k=SERVICE_K,
            qsm_min_count=2,
            drg_min_weight=0.3,
        )

        chunks = ret["chunks"]
        srcs = list(dict.fromkeys(
            os.path.basename(c.get("file_path", ""))
            for c in chunks if c.get("file_path")
        ))

        co_hits = {}
        for tk in TOP_K_LIST:
            co_hits[tk] = compute_co_hit(srcs, exp_card, exp_link, tk)

        results.append({
            "idx":          i,
            "id":           q.get("id", ""),
            "q":            q_text[:60],
            "exp_card":     os.path.basename(exp_card),
            "exp_link":     os.path.basename(exp_link),
            "co_hits":      co_hits,
            "srcs":         srcs[:5],
            "drg_sup":      ret["drg_supplement"],
            "evolve_fired": ret["evolve_fired"],
            "evolve_count": ret["evolve_count"],
            "q_seen":       ret["queries_seen"],
        })

        if ret["evolve_fired"]:
            print(f"  Q{i:3d}: EVOLVE #{ret['evolve_count']} fired ── "
                  f"co@20={'✓' if co_hits[20] else '✗'}")
        elif i % 15 == 0 or i == len(co_questions):
            drg_s = drg.stats
            print(f"  Q{i:3d}: co@20={'✓' if co_hits[20] else '✗'} "
                  f"drg_sup={ret['drg_supplement']} "
                  f"drg_edges={drg_s['total_edges']}({drg_s['edges_w05']} strong)")

    elapsed = round(time.time() - t0, 1)
    print(f"\n[eval] Completed {len(co_questions)} questions in {elapsed}s")

    # Save QSM + DRG for next run
    qsm.save()
    drg.save()
    await rag.finalize_storages()

    # Compute windowed co-hit rates
    n = len(results)
    window_size = max(1, n // 3)
    windows = [
        results[:window_size],
        results[window_size:2*window_size],
        results[2*window_size:],
    ]
    window_labels = [
        f"Q1-{window_size} (cold start)",
        f"Q{window_size+1}-{2*window_size} (learning)",
        f"Q{2*window_size+1}-{n} (matured)",
    ]

    run_record = {
        "run_id":       len(load_agentic_state()["runs"]) + 1,
        "timestamp":    time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_questions":  n,
        "evolve_every": EVOLVE_EVERY,
        "elapsed_s":    elapsed,
        "drg_edges_end": drg.stats["total_edges"],
        "drg_strong_end": drg.stats["edges_w05"],
        "windows": [],
    }

    print(f"\n{'='*60}")
    print(f"  Progressive Co-Retrieval Improvement")
    print(f"{'='*60}")
    for window, label in zip(windows, window_labels):
        window_scores = {}
        for tk in TOP_K_LIST:
            hits = sum(1 for r in window if r["co_hits"].get(tk, False))
            window_scores[tk] = round(hits / max(len(window), 1) * 100, 1)
        run_record["windows"].append({"label": label, "scores": window_scores})
        print(f"  {label}:")
        for tk in TOP_K_LIST:
            bar = "█" * int(window_scores[tk] / 5)
            print(f"    co@{tk:2d}: {window_scores[tk]:5.1f}%  {bar}")

    print(f"\n  DRG: {drg.stats['total_edges']} edges "
          f"({drg.stats['edges_w05']} strong)")
    print(f"  QSM: {qsm.stats['query_patterns']} patterns")

    return run_record, results


# ── Graph generation ───────────────────────────────────────────────────────────

def make_graphs(state: dict, latest_results: list | None = None):
    runs = state["runs"]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Graph A: Co-hit trend across runs (for each top_k) ─────────────────
    if len(runs) >= 2:
        fig, ax = plt.subplots(figsize=(9, 5))
        colors = {"5": "#9E9E9E", "10": "#2196F3", "15": "#FF9800", "20": "#F44336"}
        for tk in TOP_K_LIST:
            scores_per_run = []
            for run in runs:
                # Average all windows for overall score
                ws = run.get("windows", [])
                if ws:
                    avg = sum(w["scores"].get(tk, 0) for w in ws) / len(ws)
                    scores_per_run.append(avg)
            run_ids = [r["run_id"] for r in runs]
            ax.plot(run_ids, scores_per_run, marker="o",
                    label=f"co@{tk}", color=colors.get(str(tk), "#333"))
            for x, y in zip(run_ids, scores_per_run):
                ax.annotate(f"{y:.1f}%", (x, y), textcoords="offset points",
                            xytext=(0, 7), ha="center", fontsize=8)

        ax.set_xlabel("Run #", fontsize=11)
        ax.set_ylabel("Val-B co-retrieval recall (%)", fontsize=11)
        ax.set_title(
            "A. Co-retrieval improvement across runs\n"
            "(Each run starts with accumulated QSM+DRG from previous run)",
            fontsize=11
        )
        ax.legend(fontsize=9)
        ax.set_ylim(0, 105)
        ax.grid(axis="y", alpha=0.3)
        fig.tight_layout()
        fig.savefig(str(RESULTS_DIR / "agentic_graph_A_across_runs.png"), dpi=150)
        plt.close(fig)
        print("[graph] agentic_graph_A_across_runs.png saved")

    # ── Graph B: Within-run progressive improvement (latest run) ───────────
    if runs:
        latest = runs[-1]
        windows = latest.get("windows", [])
        if windows:
            fig, ax = plt.subplots(figsize=(9, 5))
            x = range(len(windows))
            width = 0.2
            colors_b = ["#BBDEFB", "#64B5F6", "#1565C0"]
            for j, tk in enumerate([5, 10, 15, 20]):
                vals = [w["scores"].get(tk, 0) for w in windows]
                offset = (j - 1.5) * width
                bars = ax.bar(
                    [xi + offset for xi in x], vals,
                    width=width,
                    label=f"co@{tk}",
                    alpha=0.85,
                )
                for bar, v in zip(bars, vals):
                    if v:
                        ax.text(bar.get_x() + bar.get_width()/2, v + 0.5,
                                f"{v:.0f}%", ha="center", va="bottom", fontsize=7)

            ax.set_xticks(list(x))
            ax.set_xticklabels(
                [w["label"] for w in windows],
                fontsize=9, wrap=True
            )
            ax.set_ylabel("Val-B co-retrieval recall (%)", fontsize=11)
            ax.set_title(
                f"B. Within-run progressive improvement (Run #{latest['run_id']})\n"
                f"  EVOLVE every {latest['evolve_every']} queries — "
                f"DRG edges: 0 → {latest['drg_edges_end']} ({latest['drg_strong_end']} strong)",
                fontsize=10
            )
            ax.legend(fontsize=9)
            ax.set_ylim(0, 105)
            ax.grid(axis="y", alpha=0.3)
            fig.tight_layout()
            fig.savefig(str(RESULTS_DIR / "agentic_graph_B_within_run.png"), dpi=150)
            plt.close(fig)
            print("[graph] agentic_graph_B_within_run.png saved")

    # ── Graph C: Per-question co@20 trend (latest run, if results available) ─
    if latest_results:
        fig, ax = plt.subplots(figsize=(11, 4))
        idxs = [r["idx"] for r in latest_results]
        vals = [1 if r["co_hits"].get(20, False) else 0 for r in latest_results]

        # Moving average (window=10)
        window = 10
        moving_avg = []
        for i in range(len(vals)):
            start = max(0, i - window + 1)
            moving_avg.append(sum(vals[start:i+1]) / (i - start + 1) * 100)

        ax.bar(idxs, vals, color="#B0BEC5", alpha=0.5, label="co@20 per question")
        ax.plot(idxs, moving_avg, color="#F44336", linewidth=2,
                label=f"Moving avg (window={window})")

        # Mark EVOLVE events
        evolve_marks = [r["idx"] for r in latest_results if r.get("evolve_fired")]
        for em in evolve_marks:
            ax.axvline(x=em, color="#4CAF50", linestyle="--", alpha=0.7, linewidth=1.5,
                       label="EVOLVE fired" if em == evolve_marks[0] else "")

        ax.set_xlabel("Question index", fontsize=10)
        ax.set_ylabel("co@20 (1=hit, 0=miss) + moving avg %", fontsize=10)
        ax.set_title(
            "C. Per-question co@20 within run — shows progressive improvement",
            fontsize=11
        )
        ax.legend(fontsize=9)
        ax.set_ylim(-0.05, 1.15)
        ax.grid(axis="y", alpha=0.2)
        fig.tight_layout()
        fig.savefig(str(RESULTS_DIR / "agentic_graph_C_per_question.png"), dpi=150)
        plt.close(fig)
        print("[graph] agentic_graph_C_per_question.png saved")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true",
                        help="Start from golden LightRAG (clears working copy + DRG/QSM)")
    parser.add_argument("--graphs-only", action="store_true",
                        help="Regenerate graphs from saved state, no new queries")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    state = load_agentic_state()

    if args.graphs_only:
        make_graphs(state)
        return

    if args.fresh:
        ensure_working_copy(fresh=True)
        # Clear ALL learned state: QSM + DRG (full reset — basic graph only)
        meta_dir = WORK_DIR / "wikigraph_meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        for f in ["qsm.json", "drg.json"]:
            p = meta_dir / f
            if p.exists():
                p.unlink()
                print(f"[setup] Cleared {f}")
        print("[setup] Fresh start: DRG + QSM empty, will learn from queries")
    else:
        ensure_working_copy(fresh=False)

    run_record, results = asyncio.run(run_agentic_eval())
    state["runs"].append(run_record)
    save_agentic_state(state)

    make_graphs(state, latest_results=results)

    print(f"\n[done] Run #{run_record['run_id']} complete.")
    print(f"  State:  {STATE_FILE}")
    print(f"  Graphs: {RESULTS_DIR}/agentic_graph_*.png")


if __name__ == "__main__":
    main()
