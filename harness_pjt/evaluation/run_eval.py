#!/usr/bin/env python3
"""
Unified Evaluation Runner — Evolving LightRAG
==============================================
Scenario 1: Golden RAG baseline, all retrieval modes (vector/BM25/hybrid/mix)
Scenario 2: Iterative agentic evaluation showing progressive graph improvement

All-data questions  → source_hit  (any expected_source in top-K)
Validation-A (180)  → card_hit   (single expected_card in top-K)
Validation-B  (90)  → co_hit     (BOTH expected_card AND expected_linked in top-K)

Usage:
  python3 run_eval.py --scenario 1
  python3 run_eval.py --scenario 2 --n-runs 3 --fresh
  python3 run_eval.py --scenario all --n-runs 3 --fresh
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).resolve().parent
REPO_ROOT    = SCRIPT_DIR.parent
GOLDEN_DIR   = REPO_ROOT / "rag_data"            # NEVER write to this
S1_WORK_DIR  = SCRIPT_DIR / "s1_rag_data"        # Scenario 1 working copy
S2_WORK_DIR  = SCRIPT_DIR / "rag_data"           # Scenario 2 working copy
RESULTS_DIR  = SCRIPT_DIR / "results"

TOP_K_LIST  = [5, 10, 15, 20]
SERVICE_K   = 20

DATA_ROOT = os.environ.get(
    "DATA_ROOT",
    str(REPO_ROOT.parents[1] / "shs-poc-ragflow" / "data"),
)

sys.path.insert(0, str(REPO_ROOT.parent))

# ── Helper: build RAG ─────────────────────────────────────────────────────────

async def build_rag(work_dir: Path, *, chunk_pick: str = "WEIGHT"):
    from harness_pjt.benchmark.run_benchmark import build_rag as _build
    rag, _ = await _build(str(work_dir), llm_provider="claude-cli",
                          kg_chunk_pick_method=chunk_pick)
    return rag


# ── Helper: source extraction ─────────────────────────────────────────────────

def extract_sources(chunks: list[dict]) -> list[str]:
    """Unique file_path basenames from chunks (in order)."""
    seen: dict[str, bool] = {}
    for c in chunks:
        fp = c.get("file_path", "") or c.get("source_id", "")
        bn = Path(fp).name if fp else ""
        if bn and bn not in seen:
            seen[bn] = True
    return list(seen.keys())


# ── Helper: hit metrics ───────────────────────────────────────────────────────

def _bn(path: str) -> str:
    return Path(path).name.lower() if path else ""


def source_hit(top_srcs: list[str], expected: list[str]) -> bool:
    """True if any expected source appears in retrieved sources."""
    if not expected:
        return True   # no-context question: trivially true
    for exp in expected:
        exp_bn = _bn(exp)
        if any(exp_bn in s.lower() for s in top_srcs):
            return True
    return False


def co_hit(top_srcs: list[str], card: str, linked: str) -> bool:
    """True if BOTH card AND linked appear in top_srcs."""
    if not linked:
        return False
    card_ok   = any(_bn(card)   in s.lower() for s in top_srcs)
    linked_ok = any(_bn(linked) in s.lower() for s in top_srcs)
    return card_ok and linked_ok


def compute_all_scores(results: list[dict]) -> dict:
    """
    Compute scores from per-question result dicts.
    Each dict must have 'source_hit', 'card_hit', 'co_hit' fields,
    each of which is a dict mapping k → bool.
    """
    n = len(results)
    scores: dict[str, float] = {}
    for metric in ("source_hit", "card_hit", "co_hit"):
        for k in TOP_K_LIST:
            hits = sum(1 for r in results if r.get(metric, {}).get(k, False))
            scores[f"{metric}@{k}"] = hits / n if n else 0.0
    return scores


# ── Scenario 1 helpers ────────────────────────────────────────────────────────

def _ensure_s1_copy():
    """Copy golden → s1_rag_data so Scenario 1 never touches the original."""
    if not S1_WORK_DIR.exists():
        print(f"  Copying golden RAG → {S1_WORK_DIR.name} …")
        shutil.copytree(GOLDEN_DIR, S1_WORK_DIR)
        print("  Done.")


async def _eval_one_question(rag, mode_fn, q: dict, *, is_bm25: bool = False) -> dict:
    """Run one question through one mode and return hit dict."""
    if is_bm25:
        from harness_pjt.benchmark.retrieval import query_bm25
        chunks = query_bm25(rag, q["question"], top_k=SERVICE_K)
    else:
        chunks = await mode_fn(rag, q["question"], top_k=SERVICE_K)

    srcs = extract_sources(chunks)

    card   = q.get("expected_card", "")
    linked = q.get("expected_linked", "")
    exp_src = q.get("expected_sources", [])
    if card and card not in exp_src:
        exp_src = [card] + exp_src

    result: dict = {
        "q_id": q.get("id", ""),
        "source_hit": {k: source_hit(srcs[:k], exp_src) for k in TOP_K_LIST},
        "card_hit":   ({k: source_hit(srcs[:k], [card]) for k in TOP_K_LIST}
                       if card else {k: False for k in TOP_K_LIST}),
        "co_hit":     {k: co_hit(srcs[:k], card, linked) for k in TOP_K_LIST},
        "sources": srcs[:8],
    }
    return result


# ── Scenario 1 ────────────────────────────────────────────────────────────────

async def run_scenario1(
    all_qs: list[dict],
    val_a_qs: list[dict],
    val_b_qs: list[dict],
    *,
    verbose: bool = True,
) -> dict:
    print("\n" + "="*70)
    print("SCENARIO 1 — Golden RAG baseline (all retrieval modes)")
    print(f"  All-data: {len(all_qs)}q  Val-A: {len(val_a_qs)}q  Val-B: {len(val_b_qs)}q")
    print("="*70)

    from harness_pjt.benchmark.retrieval import (
        query_vector, query_bm25, query_hybrid, query_mix_mode,
    )

    _ensure_s1_copy()
    rag = await build_rag(S1_WORK_DIR)

    mode_cfg = {
        "vector":  (query_vector,  False),
        "bm25":    (None,          True),
        "hybrid":  (query_hybrid,  False),
        "mix":     (query_mix_mode, False),
    }

    # Collect results per mode for each question set
    mode_all_scores: dict[str, dict] = {}

    for mode, (fn, is_bm) in mode_cfg.items():
        print(f"\n  [{mode}] evaluating …", flush=True)

        def _run_q(q):
            return _eval_one_question(rag, fn, q, is_bm25=is_bm)

        # Combine all question sets
        combined = []
        for i, q in enumerate(all_qs + val_a_qs + val_b_qs, 1):
            r = await _run_q(q)
            r["qset"] = "all" if i <= len(all_qs) else (
                "val_a" if i <= len(all_qs) + len(val_a_qs) else "val_b")
            combined.append(r)
            if verbose and i % 30 == 0:
                print(f"    Q{i:3d}/{len(all_qs)+len(val_a_qs)+len(val_b_qs)}", flush=True)

        # Split back
        n_all = len(all_qs)
        n_va  = len(val_a_qs)
        r_all  = combined[:n_all]
        r_va   = combined[n_all:n_all + n_va]
        r_vb   = combined[n_all + n_va:]

        mode_all_scores[mode] = {
            "all_data": compute_all_scores(r_all),
            "val_a":    compute_all_scores(r_va),
            "val_b":    compute_all_scores(r_vb),
        }

    # Print summary table
    print()
    def _print_table(label: str, metric: str, score_key: str):
        print(f"\n  [{label}] {metric}")
        print(f"    {'Mode':<10}", end="")
        for k in TOP_K_LIST:
            print(f"  @{k:2d}", end="")
        print()
        print("    " + "-" * (10 + 6 * len(TOP_K_LIST)))
        for mode in mode_cfg:
            print(f"    {mode:<10}", end="")
            for k in TOP_K_LIST:
                v = mode_all_scores[mode][score_key].get(f"{metric}@{k}", 0.0)
                print(f"  {v:.0%}", end="")
            print()

    if all_qs:
        _print_table("All-data", "source_hit", "all_data")
    if val_a_qs:
        _print_table("Val-A (card recall)", "card_hit", "val_a")
    if val_b_qs:
        _print_table("Val-B (co-retrieval)", "co_hit", "val_b")

    return {
        "scenario": 1,
        "modes": mode_all_scores,
        "n_all": len(all_qs),
        "n_val_a": len(val_a_qs),
        "n_val_b": len(val_b_qs),
    }


# ── Scenario 2 ────────────────────────────────────────────────────────────────

def ensure_working_copy(fresh: bool):
    if fresh and S2_WORK_DIR.exists():
        shutil.rmtree(S2_WORK_DIR)
    if not S2_WORK_DIR.exists():
        print(f"  Copying golden RAG → {S2_WORK_DIR.name} …")
        shutil.copytree(GOLDEN_DIR, S2_WORK_DIR)
        print("  Done.")


def clear_memories(work_dir: Path):
    for fname in ["qsm_data.json", "drg_data.json"]:
        p = work_dir / fname
        if p.exists():
            p.unlink()


async def _agentic_eval_questions(agent, questions: list[dict], run_idx: int) -> list[dict]:
    """Run all questions through agent.agentic_query, return per-question dicts."""
    results = []
    t0 = time.time()
    for i, q in enumerate(questions, 1):
        card   = q.get("expected_card", "")
        linked = q.get("expected_linked", "")
        exp_src = list(q.get("expected_sources", []))
        if card and card not in exp_src:
            exp_src = [card] + exp_src

        out = await agent.agentic_query(
            q["question"],
            top_k=SERVICE_K,
            qsm_min_count=1,
            drg_min_weight=0.1,
            drg_supplement_k=5,
            online_dcsg_threshold=3,
            evolve_co_occur_min=1,
        )
        chunks = out["chunks"]
        srcs = extract_sources(chunks)

        results.append({
            "q_id": q.get("id", f"q{i}"),
            "source_hit": {k: source_hit(srcs[:k], exp_src)  for k in TOP_K_LIST},
            "card_hit":   {k: source_hit(srcs[:k], [card])    for k in TOP_K_LIST} if card else {k: False for k in TOP_K_LIST},
            "co_hit":     {k: co_hit(srcs[:k], card, linked)  for k in TOP_K_LIST},
            "drg_supplement": out["drg_supplement"],
            "evolve_fired":   out["evolve_fired"],
            "sources": srcs[:5],
        })

        if i % 10 == 0:
            elapsed = time.time() - t0
            co20 = sum(1 for r in results if r["co_hit"].get(20, False)) / i
            print(
                f"  Run {run_idx}  Q{i:3d}/{len(questions)}"
                f"  co@20={co20:.0%}"
                f"  drg_supp={sum(r['drg_supplement'] for r in results)}"
                f"  {elapsed:.0f}s",
                flush=True,
            )
    return results


async def run_scenario2(
    all_qs: list[dict],
    val_a_qs: list[dict],
    val_b_qs: list[dict],
    n_runs: int,
    fresh: bool,
) -> dict:
    print("\n" + "="*70)
    print(f"SCENARIO 2 — Iterative Agentic Evaluation ({n_runs} runs)")
    print(f"  All-data: {len(all_qs)}q  Val-A: {len(val_a_qs)}q  Val-B: {len(val_b_qs)}q")
    print("="*70)

    ensure_working_copy(fresh)
    if fresh:
        clear_memories(S2_WORK_DIR)

    rag = await build_rag(S2_WORK_DIR)

    from harness_pjt.wikigraph.qsm import QueryStructuralMemory
    from harness_pjt.wikigraph.drg import DocRelationGraph
    from harness_pjt.wikigraph.agent import WikiGraphAgent

    qsm = QueryStructuralMemory(str(S2_WORK_DIR))
    drg = DocRelationGraph(str(S2_WORK_DIR))
    drg.index_doc_chunks(rag)

    # fast_mode=True: hybrid retrieval (no LLM per query), ~0.5s per query
    agent = WikiGraphAgent(rag, evolve_every=0, fast_mode=True)
    agent.attach_memories(qsm, drg)

    run_results: list[dict] = []   # per-run summary

    # Questions used in each iteration (all three sets)
    eval_qs = all_qs + val_a_qs + val_b_qs

    for run_idx in range(1, n_runs + 1):
        print(f"\n─── Run {run_idx}/{n_runs} ───────────────────────────────────────")

        all_r  = await _agentic_eval_questions(agent, all_qs,   run_idx) if all_qs  else []
        val_a_r = await _agentic_eval_questions(agent, val_a_qs, run_idx) if val_a_qs else []
        val_b_r = await _agentic_eval_questions(agent, val_b_qs, run_idx)

        all_scores   = compute_all_scores(all_r)   if all_r   else {}
        val_a_scores = compute_all_scores(val_a_r) if val_a_r else {}
        val_b_scores = compute_all_scores(val_b_r)

        run_results.append({
            "run": run_idx,
            "all_data": all_scores,
            "val_a":    val_a_scores,
            "val_b":    val_b_scores,
        })

        print(f"\n  Run {run_idx} summary (Val-B co-retrieval):")
        for k in TOP_K_LIST:
            n_hit = sum(1 for r in val_b_r if r["co_hit"].get(k, False))
            print(f"    co@{k:2d} = {n_hit}/{len(val_b_qs)} = {val_b_scores.get(f'co_hit@{k}', 0):.1%}")
        if val_a_r:
            print(f"  Val-A card_hit@20 = {val_a_scores.get('card_hit@20', 0):.1%}")
        if all_r:
            print(f"  All-data source_hit@20 = {all_scores.get('source_hit@20', 0):.1%}")

        # EVOLVE after each run
        all_seen = [{"question": q["question"]} for q in eval_qs]
        print(f"\n  Firing EVOLVE (DA+DCSG+CDRB) on {len(all_seen)} queries …")
        try:
            ev = await agent.batch_evolve(
                all_seen, evolve_k=50, co_occur_min=1, max_edges=600,
                qsm=qsm, drg=drg,
            )
            agent._evolve_count += 1
            print(
                f"  EVOLVE #{agent._evolve_count}:"
                f"  DA={ev['da_updates']} DCSG={ev['dcsg_edges']} CDRB={ev['cdrb_edges']}"
            )
        except Exception as e:
            print(f"  EVOLVE error: {e}")

    # ── Progressive improvement table ─────────────────────────────────────────
    print("\n" + "="*70)
    print("SCENARIO 2 — Progressive improvement (Val-B co-hit)")
    print("="*70)
    print(f"\n  {'Run':<5}", end="")
    for k in TOP_K_LIST:
        print(f"  co@{k:2d}   ", end="")
    print()
    print("  " + "-" * (5 + 11 * len(TOP_K_LIST)))
    for i, rr in enumerate(run_results, 1):
        print(f"  {i:<5}", end="")
        for k in TOP_K_LIST:
            v = rr["val_b"].get(f"co_hit@{k}", 0)
            diff = ""
            if i > 1:
                prev = run_results[i - 2]["val_b"].get(f"co_hit@{k}", 0)
                d = v - prev
                diff = f"({d:+.0%})" if abs(d) > 0.001 else "(=)"
            print(f"  {v:.0%}{diff:>7}", end="")
        print()

    if val_a_qs:
        print(f"\n  Val-A card_hit@20:", end="")
        for rr in run_results:
            v = rr["val_a"].get("card_hit@20", 0)
            print(f"  {v:.0%}", end="")
        print()

    if all_qs:
        print(f"  All-data src@20: ", end="")
        for rr in run_results:
            v = rr["all_data"].get("source_hit@20", 0)
            print(f"  {v:.0%}", end="")
        print()

    return {"scenario": 2, "n_runs": n_runs, "run_results": run_results}


# ── Plots ──────────────────────────────────────────────────────────────────────

def _plot_s1(data: dict):
    """Grouped bar chart: modes × top_k for each metric."""
    mode_data = data["modes"]
    modes     = list(mode_data.keys())
    colors    = ["#4C72B0", "#DD8452", "#55A868", "#C44E52"]

    metrics = [
        ("Val-B co-retrieval",   "val_b",   "co_hit"),
        ("Val-A card recall",    "val_a",   "card_hit"),
        ("All-data source_hit",  "all_data","source_hit"),
    ]
    # Only include non-empty metrics
    active = [(lbl, sk, mk) for lbl, sk, mk in metrics
              if any(mode_data[m][sk] for m in modes)]

    n_panels = len(active)
    if n_panels == 0:
        return
    fig, axes = plt.subplots(1, n_panels, figsize=(7 * n_panels, 5), squeeze=False)
    fig.suptitle("Scenario 1 — Golden RAG retrieval modes comparison", fontsize=13)

    for ax, (lbl, score_key, metric) in zip(axes[0], active):
        x = range(len(TOP_K_LIST))
        width = 0.18
        offsets = [-1.5, -0.5, 0.5, 1.5]
        for j, (mode, color) in enumerate(zip(modes, colors)):
            vals = [mode_data[mode][score_key].get(f"{metric}@{k}", 0) for k in TOP_K_LIST]
            rects = ax.bar([xi + offsets[j] * width for xi in x], vals, width,
                           label=mode, color=color, alpha=0.85)
            for r, v in zip(rects, vals):
                ax.text(r.get_x() + r.get_width() / 2, r.get_height() + 0.005,
                        f"{v:.0%}", ha="center", va="bottom", fontsize=7)
        ax.set_xticks(list(x))
        ax.set_xticklabels([f"@{k}" for k in TOP_K_LIST])
        ax.yaxis.set_major_formatter(mticker.PercentFormatter(1.0))
        ax.set_ylim(0, 1.0)
        ax.set_title(lbl)
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / "scenario1_modes.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    print(f"  Plot saved: {path}")


def _plot_s2(data: dict):
    """Line chart showing progressive improvement across runs."""
    rrs = data["run_results"]
    n   = len(rrs)
    fig, axes = plt.subplots(1, 3, figsize=(18, 5), squeeze=False)
    fig.suptitle("Scenario 2 — Progressive improvement across agentic runs", fontsize=13)

    panel_cfg = [
        ("Val-B co-hit", "val_b",    "co_hit"),
        ("Val-A card",   "val_a",    "card_hit"),
        ("All-data",     "all_data", "source_hit"),
    ]
    colors = ["#4C72B0", "#DD8452", "#55A868", "#C44E52"]

    for ax, (lbl, score_key, metric) in zip(axes[0], panel_cfg):
        for k, color in zip(TOP_K_LIST, colors):
            vals = [rr[score_key].get(f"{metric}@{k}", 0) for rr in rrs]
            if any(vals):
                ax.plot(range(1, n + 1), vals, "o-", label=f"@{k}",
                        color=color, lw=2, markersize=5)
        ax.set_xticks(range(1, n + 1))
        ax.set_xticklabels([f"Run {i}" for i in range(1, n + 1)])
        ax.yaxis.set_major_formatter(mticker.PercentFormatter(1.0))
        ax.set_ylim(0, 1.0)
        ax.set_title(lbl)
        ax.legend(loc="lower right", fontsize=8)
        ax.grid(alpha=0.3)

    fig.tight_layout()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / "scenario2_evolution.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    print(f"  Plot saved: {path}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=["1", "2", "all"], default="all")
    parser.add_argument("--n-runs",   type=int, default=3, help="Scenario 2 iterations")
    parser.add_argument("--fresh",    action="store_true", help="Wipe working copy first")
    parser.add_argument("--data-root", default=DATA_ROOT)
    parser.add_argument("--skip-all-data", action="store_true",
                        help="Skip all-data questions (faster)")
    parser.add_argument("--skip-val-a", action="store_true",
                        help="Skip Val-A questions (faster)")
    args = parser.parse_args()

    from harness_pjt.benchmark.questions import (
        load_all_data_questions, load_validation_set_a, load_validation_set_b
    )

    all_qs   = [] if args.skip_all_data else load_all_data_questions(args.data_root)
    val_a_qs = [] if args.skip_val_a   else load_validation_set_a(args.data_root)
    val_b_qs = load_validation_set_b(args.data_root)

    if not val_b_qs:
        print(f"ERROR: No Val-B questions in {args.data_root}", file=sys.stderr)
        sys.exit(1)

    print(f"Questions loaded — All-data: {len(all_qs)}  Val-A: {len(val_a_qs)}  Val-B: {len(val_b_qs)}")

    all_data: dict = {
        "generated": datetime.now().isoformat(),
        "n_all": len(all_qs),
        "n_val_a": len(val_a_qs),
        "n_val_b": len(val_b_qs),
    }

    if args.scenario in ("1", "all"):
        s1 = await run_scenario1(all_qs, val_a_qs, val_b_qs)
        all_data["scenario1"] = s1
        _plot_s1(s1)

    if args.scenario in ("2", "all"):
        s2 = await run_scenario2(all_qs, val_a_qs, val_b_qs, n_runs=args.n_runs, fresh=args.fresh)
        all_data["scenario2"] = s2
        _plot_s2(s2)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "eval_results.json"
    out_path.write_text(json.dumps(all_data, ensure_ascii=False, indent=2))
    print(f"\nFull results saved → {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
