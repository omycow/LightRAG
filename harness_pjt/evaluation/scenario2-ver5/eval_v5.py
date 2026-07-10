"""
Scenario 2 ver5 — StructRAG two-track evolving evaluation.

Per iteration:
  1. evaluate All-data(106) + Val-A(180) + Val-B(90) through StructRetriever
     (inline learning ON: L2 reinforcement, bandit, plan cache)
  2. regression guard: if All@20 or ValA@20 dropped vs previous iteration,
     roll the KG back to the pre-evolve checkpoint and record why (D7)
  3. KG checkpoint → background evolver cycle (Track S rules/LLM, Track K rules/LLM)
  4. record metrics + evolution-visibility indicators, append DESIGN_HISTORY entry

Golden protection: harness_pjt/rag_data is copied, never modified (--fresh re-copies).

Usage:
  python3 eval_v5.py --fresh            # start from golden, wipe learned state
  python3 eval_v5.py                    # continue with accumulated state
  python3 eval_v5.py --iterations 3     # run N iterations back-to-back
  python3 eval_v5.py --no-llm           # rules-only (analyzer Tier1 + evolver LLM off)
"""

import argparse
import asyncio
import json
import os
import shutil
import statistics
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(REPO_ROOT))

GOLDEN_DIR = REPO_ROOT / "harness_pjt" / "rag_data"
WORK_DIR = SCRIPT_DIR / "rag_data"
RESULTS_DIR = SCRIPT_DIR / "results"
STATE_FILE = RESULTS_DIR / "v5_state.json"
HISTORY_FILE = REPO_ROOT / "harness_pjt" / "structrag" / "DESIGN_HISTORY.md"

DATA_ROOT = os.environ.get("DATA_ROOT", str(REPO_ROOT.parents[0] / "shs-poc-ragflow" / "data"))
TOP_K = 20
REGRESSION_TOLERANCE = 0.5  # percentage points


CLI_MODEL = os.environ.get("CLAUDE_CLI_MODEL", "claude-haiku-4-5-20251001")

# v5.2 C3: LLM health tracking. In the first validation run the CLI silently hit
# its usage limit from iteration 3 on — every analyze call failed (~2s) and fell
# back to rules, invisible in the reports. Track calls/failures, back off after
# a failure streak, and surface the counters in each iteration record.
LLM_HEALTH = {"calls": 0, "failures": 0, "consecutive_failures": 0, "backoff": False}
BACKOFF_AFTER = 8


async def claude_cli_llm(prompt: str, system_prompt: str | None = None, **kwargs) -> str:
    """Analyzer/evolver LLM via claude CLI (haiku): reliable JSON, ~4s/call.
    Used ONLY off the hot path or behind the analyzer's complexity gate."""
    import tempfile
    if LLM_HEALTH["consecutive_failures"] >= BACKOFF_AFTER:
        LLM_HEALTH["backoff"] = True
        raise RuntimeError("LLM backoff: consecutive failure streak")
    LLM_HEALTH["calls"] += 1
    text = (system_prompt + "\n\n" if system_prompt else "") + prompt
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tf:
        tf.write(text)
        tmp = tf.name
    try:
        proc = await asyncio.create_subprocess_shell(
            f"claude -p --output-format text --no-session-persistence "
            f"--disable-slash-commands --model {CLI_MODEL} < '{tmp}'",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=90)
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI rc={proc.returncode}: {err.decode()[:200]}")
        result = out.decode().strip()
        LLM_HEALTH["consecutive_failures"] = 0
        return result
    except Exception:
        LLM_HEALTH["failures"] += 1
        LLM_HEALTH["consecutive_failures"] += 1
        raise
    finally:
        os.unlink(tmp)


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"iterations": []}


def save_state(state: dict):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1))


def ensure_working_copy(fresh: bool):
    if fresh and WORK_DIR.exists():
        print(f"[setup] --fresh: removing {WORK_DIR}")
        shutil.rmtree(WORK_DIR)
    if not WORK_DIR.exists():
        print("[setup] copying golden → working copy")
        shutil.copytree(GOLDEN_DIR, WORK_DIR)
    if fresh:
        meta = WORK_DIR / "structrag_meta"
        if meta.exists():
            shutil.rmtree(meta)
        if STATE_FILE.exists():
            STATE_FILE.unlink()


def pct(hits: int, total: int) -> float:
    return round(hits / total * 100, 1) if total else 0.0


async def eval_set(retriever, questions, co_metric=False):
    from harness_pjt.benchmark.questions import source_hit, co_retrieval_hit

    hits20 = 0
    scored = 0
    co_hits = {5: 0, 10: 0, 20: 0}
    latencies, qualities, tiers = [], [], {"cache": 0, "llm": 0, "rules": 0}
    sg_expands = 0
    for q in questions:
        ret = await retriever.retrieve(q["question"], top_k=TOP_K)
        srcs = ret["retrieved_docs"]
        latencies.append(ret["latency_ms"])
        qualities.append(ret["quality"])
        tiers[ret["plan"].source] = tiers.get(ret["plan"].source, 0) + 1
        sg_expands += ret["sg_expand"]
        if co_metric:
            for k in co_hits:
                co = co_retrieval_hit(srcs[:k], q["expected_card"], q.get("expected_linked", ""))
                co_hits[k] += bool(co["co_hit"])
        if q.get("no_context") or not q.get("expected_sources"):
            continue  # no-context probes aren't retrieval-recall questions
        scored += 1
        if source_hit(srcs[:TOP_K], q.get("expected_sources", [])):
            hits20 += 1
    n = len(questions)
    out = {
        "n": n,
        "hit@20": pct(hits20, scored),
        "lat_p50": round(statistics.median(latencies), 1) if latencies else 0,
        "lat_p95": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 1) if latencies else 0,
        "q_mean": round(statistics.mean(qualities), 3) if qualities else 0,
        "tiers": tiers,
        "sg_expand_total": sg_expands,
    }
    if co_metric:
        out["co"] = {str(k): pct(v, n) for k, v in co_hits.items()}  # str keys: JSON round-trip safe
    return out


async def run_iteration(use_llm: bool, state: dict) -> dict:
    from harness_pjt.benchmark.run_benchmark import build_rag
    from harness_pjt.benchmark.questions import (
        load_all_data_questions, load_validation_set_a, load_validation_set_b,
    )
    from harness_pjt.structrag.retriever import StructRetriever
    from harness_pjt.structrag.evolver import Evolver

    rag, _ = await build_rag(str(WORK_DIR))
    # fresh backoff state each iteration — the provider may have recovered
    LLM_HEALTH["consecutive_failures"] = 0
    LLM_HEALTH["backoff"] = False
    llm = claude_cli_llm if use_llm else None
    retriever = StructRetriever(rag, str(WORK_DIR), llm_func=llm)
    evolver = Evolver(rag, retriever, llm_func=llm, llm_budget=10)

    sg_stats0 = retriever.sg.build_l1(rag.text_chunks._data)
    print(f"[sg] {retriever.sg.stats}")

    q_all = load_all_data_questions(DATA_ROOT)
    q_va = load_validation_set_a(DATA_ROOT)
    q_vb = load_validation_set_b(DATA_ROOT)
    print(f"[eval] all={len(q_all)} valA={len(q_va)} valB={len(q_vb)}")

    t0 = time.time()
    m_all = await eval_set(retriever, q_all)
    print(f"  all-data:  hit@20={m_all['hit@20']}%  p50={m_all['lat_p50']}ms")
    m_va = await eval_set(retriever, q_va)
    print(f"  val-A:     hit@20={m_va['hit@20']}%  p50={m_va['lat_p50']}ms")
    m_vb = await eval_set(retriever, q_vb, co_metric=True)
    print(f"  val-B co:  @5={m_vb['co']['5']}% @10={m_vb['co']['10']}% @20={m_vb['co']['20']}%")

    # ── regression guard vs previous iteration ────────────────────────────────
    rolled_back = False
    prev = state["iterations"][-1] if state["iterations"] else None
    if prev:
        drop_all = prev["all"]["hit@20"] - m_all["hit@20"]
        drop_va = prev["valA"]["hit@20"] - m_va["hit@20"]
        if drop_all > REGRESSION_TOLERANCE or drop_va > REGRESSION_TOLERANCE:
            evolver.rollback_kg(
                reason=f"regression guard: All@20 {prev['all']['hit@20']}→{m_all['hit@20']}, "
                       f"ValA@20 {prev['valA']['hit@20']}→{m_va['hit@20']}")
            rolled_back = True
            print("[guard] REGRESSION → KG rolled back to previous checkpoint")

    # ── evolve cycle (checkpoint first) ───────────────────────────────────────
    evolver.checkpoint_kg()
    evo = await evolver.run_cycle()
    print(f"[evolve] {evo}")

    retriever.save()
    await rag.finalize_storages()

    record = {
        "iter": len(state["iterations"]) + 1,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "use_llm": use_llm,
        "llm_health": dict(LLM_HEALTH),
        "all": m_all, "valA": m_va, "valB": m_vb,
        "sg": retriever.sg.stats,
        "plan_cache": retriever.analyzer.cache.stats,
        "strategy_memory": retriever.analyzer.memory.stats,
        "evolve": evo,
        "rolled_back": rolled_back,
        "elapsed_s": round(time.time() - t0, 1),
    }
    return record


def append_history(record: dict):
    lines = [
        f"\n### {record['ts']} — ver5 iteration {record['iter']} "
        f"({'LLM on' if record['use_llm'] else 'rules only'})",
        f"- All@20 {record['all']['hit@20']}% · ValA@20 {record['valA']['hit@20']}% · "
        f"ValB co@20/10/5 {record['valB']['co']['20']}/{record['valB']['co']['10']}/{record['valB']['co']['5']}%",
        f"- latency p50/p95: all={record['all']['lat_p50']}/{record['all']['lat_p95']}ms · "
        f"tiers(valB)={record['valB']['tiers']}",
        f"- SG: {record['sg']} · plan_cache: {record['plan_cache']}",
        f"- evolve: {record['evolve']}",
    ]
    if record["rolled_back"]:
        lines.append("- **REGRESSION GUARD fired → KG rolled back** (사유는 evolution_log 참조)")
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def write_summary(state: dict):
    rows = ["| Iter | All@20 | ValA@20 | co@20 | co@10 | co@5 | p50ms | cache hit | SG L2/L3 |",
            "|---|---|---|---|---|---|---|---|---|"]
    for r in state["iterations"]:
        rows.append(
            f"| {r['iter']} | {r['all']['hit@20']} | {r['valA']['hit@20']} | "
            f"{r['valB']['co']['20']} | {r['valB']['co']['10']} | {r['valB']['co']['5']} | "
            f"{r['valB']['lat_p50']} | {r['plan_cache'].get('hit_rate', 0)} | "
            f"{r['sg']['l2_co_retrieval']}/{r['sg']['l3_llm_curated']} |")
    baseline = ("\n기준선 (ver4 iter1): All@20 99.1 / ValA 99.4 / ValB co@20 54.4, co@10 37.8, co@5 20.0\n")
    text = ("# Scenario 2 ver5 — StructRAG two-track evolution\n\n"
            f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n" + baseline +
            "\n" + "\n".join(rows) + "\n")
    (RESULTS_DIR / "summary_report.md").write_text(text, encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--iterations", type=int, default=1)
    p.add_argument("--no-llm", action="store_true")
    args = p.parse_args()

    ensure_working_copy(args.fresh)
    state = load_state()
    for _ in range(args.iterations):
        record = asyncio.run(run_iteration(use_llm=not args.no_llm, state=state))
        state["iterations"].append(record)
        save_state(state)
        append_history(record)
        write_summary(state)
        print(f"[done] iteration {record['iter']} in {record['elapsed_s']}s "
              f"{'(ROLLED BACK)' if record['rolled_back'] else ''}")


if __name__ == "__main__":
    main()
