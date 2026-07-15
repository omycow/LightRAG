"""Fast Scenario 1 benchmark — vector vs BM25 vs Hybrid source_hit@20.

Uses a stub LLM that skips entity extraction to finish ingest in minutes,
not hours. This gives clean retrieval metrics without needing a real LLM.

For Scenario 2 (EVOLVE), you still need a real LLM — use Groq free API or Claude.

Usage:
    python3 harness_pjt/benchmark/run_fast_benchmark.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # harness_pjt/
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # LightRAG/

DATA_ROOT  = os.environ.get("DATA_ROOT",
                str(Path(__file__).resolve().parents[3] / "shs-poc-ragflow" / "data"))
WORK_DIR   = os.environ.get("WORK_DIR", "/tmp/wikigraph_fast_bench")
OUT_DIR    = os.environ.get("OUT_DIR", str(Path(__file__).resolve().parent / "results"))
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM   = 384
TOP_K       = 20
BATCH_SIZE  = 10   # more chunks per batch since LLM is instant


async def stub_llm(prompt, system_prompt=None, history_messages=None, **kwargs) -> str:
    """Stub LLM that returns empty entity JSON immediately — skips entity extraction."""
    return json.dumps({
        "entities": [],
        "relationships": [],
        "content_keywords": "",
        "high_level_keywords": "",
        "low_level_keywords": "",
    })


async def build_rag():
    from sentence_transformers import SentenceTransformer
    from lightrag import LightRAG
    from lightrag.utils import EmbeddingFunc

    print(f"[fast-bench] Loading embedding model...")
    embed_model = SentenceTransformer(EMBED_MODEL)

    async def embed_func(texts):
        return embed_model.encode(texts, normalize_embeddings=True)

    os.makedirs(WORK_DIR, exist_ok=True)
    rag = LightRAG(
        working_dir=WORK_DIR,
        llm_model_func=stub_llm,
        llm_model_max_async=1,
        default_llm_timeout=5,
        embedding_func=EmbeddingFunc(
            embedding_dim=EMBED_DIM,
            max_token_size=8192,
            func=embed_func,
        ),
        addon_params={"enable_hybrid_search": True, "hybrid_search_mode": "hybrid"},
    )
    await rag.initialize_storages()
    print(f"[fast-bench] LightRAG initialized at {WORK_DIR}")
    return rag


async def fast_ingest(rag):
    from benchmark.ingest_data import ingest_priority_subset

    progress_path = os.path.join(WORK_DIR, "ingest_progress.json")
    print(f"\n[fast-bench] Ingesting priority subset (batch_size={BATCH_SIZE})...")
    t0 = time.time()
    result = await ingest_priority_subset(rag, DATA_ROOT, progress_path,
                                          batch_size=BATCH_SIZE)
    elapsed = time.time() - t0
    print(f"[fast-bench] Ingest done in {elapsed/60:.1f} min: {result}")
    return result


async def run_eval(rag):
    from benchmark.questions import load_all_data_questions, load_validation_set_a, load_validation_set_b
    from benchmark.retrieval import evaluate_single
    from run_benchmark import compute_metrics, compute_co_retrieval_metrics, render_scenario_table, render_co_retrieval_table

    q_all = load_all_data_questions(DATA_ROOT)
    q_va  = load_validation_set_a(DATA_ROOT)
    q_vb  = load_validation_set_b(DATA_ROOT)
    print(f"[fast-bench] Questions: all_data={len(q_all)}, val_A={len(q_va)}, val_B={len(q_vb)}")

    async def eval_set(questions, label):
        results = []
        for i, q in enumerate(questions):
            try:
                r = await evaluate_single(rag, q, top_k=TOP_K)
                results.append(r)
                hv = "✓" if r["vector"]["hit"] else "✗"
                hb = "✓" if r["bm25"]["hit"] else "✗"
                hh = "✓" if r["hybrid"]["hit"] else "✗"
                if (i+1) % 10 == 0 or i == 0:
                    print(f"  [{label}] {i+1}/{len(questions)} vec={hv} bm25={hb} hybrid={hh}")
            except Exception as e:
                print(f"  [{label}] ERROR {q.get('id')}: {e}")
                results.append({"id": q.get("id",""), "question": q["question"],
                                 "vector": {"hit": False}, "bm25": {"hit": False}, "hybrid": {"hit": False}})
        return results

    print("\n[fast-bench] Evaluating All-Data questions...")
    r_all = await eval_set(q_all, "all_data")

    print("\n[fast-bench] Evaluating Validation Set A (card recall)...")
    r_va = await eval_set(q_va, "val_A")

    print("\n[fast-bench] Evaluating Validation Set B (co-retrieval)...")
    r_vb = await eval_set(q_vb, "val_B")

    # Summary
    print("\n" + "="*60)
    print("RESULTS — Scenario 1: Vector vs BM25 vs Hybrid (source_hit@20)")
    print("="*60)
    for label, results in [("All-Data (106Q)", r_all), ("Val-A (180Q)", r_va), ("Val-B (90Q)", r_vb)]:
        mv = compute_metrics(results, "vector")
        mb = compute_metrics(results, "bm25")
        mh = compute_metrics(results, "hybrid")
        print(f"\n{label}:")
        print(f"  Vector:  {mv['hit']:3d}/{mv['total']} = {mv['recall']:5.1f}%")
        print(f"  BM25:    {mb['hit']:3d}/{mb['total']} = {mb['recall']:5.1f}%")
        print(f"  Hybrid:  {mh['hit']:3d}/{mh['total']} = {mh['recall']:5.1f}%")

    # Write output
    os.makedirs(OUT_DIR, exist_ok=True)
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "fast (stub LLM — retrieval-only, no entity graph)",
        "data_root": DATA_ROOT,
        "work_dir": WORK_DIR,
        "scenario1": {
            "all_data": r_all,
            "validation_a": r_va,
            "validation_b": r_vb,
        },
    }
    out_json = os.path.join(OUT_DIR, "fast_benchmark_results.json")
    Path(out_json).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\n[output] JSON: {out_json}")

    # Markdown report
    lines = [
        "# Evolving LightRAG — Fast Benchmark (Scenario 1 Only)",
        "",
        f"- Mode: **Retrieval-only** (stub LLM, no entity graph built)",
        f"- Generated: {payload['generated_at']}",
        f"- Embedding: `{EMBED_MODEL}` (local, {EMBED_DIM}-dim)",
        f"- Retrieval: vector + BM25 keyword + Hybrid (RRF@k=60)",
        "",
        "## Scenario 1 — Initial State (No EVOLVE)",
        "",
        "> Note: Entity graph was NOT built (stub LLM). EVOLVE (Scenario 2) requires a real LLM.",
        "> Retrieval metrics are valid — chunks are embedded in VDB + BM25.",
        "",
    ]
    for label, results in [
        ("All-Data Benchmark (106Q)", r_all),
        ("Validation Set A — Card Recall (180Q)", r_va),
        ("Validation Set B — Co-retrieval (90Q)", r_vb),
    ]:
        lines += render_scenario_table(label, results)
    lines += render_co_retrieval_table("Set B Co-retrieval Breakdown", r_vb)

    lines += [
        "",
        "## Key Observations",
        "",
        "- **BM25 advantage**: Issue IDs (LYR-455, FA-2231), product codes (EN9100, UF4100) → BM25 ranks these first.",
        "- **Vector advantage**: Semantic queries without exact keywords → VDB finds related chunks.",
        "- **Hybrid**: RRF fusion captures best of both — typically best overall recall.",
        "- **EVOLVE effect**: Not measured here — use `run_benchmark.py --llm-provider groq` or Claude API.",
        "",
        "## Next Steps",
        "",
        "For Scenario 2 (EVOLVE effect measurement):",
        "1. Get [Groq free API key](https://console.groq.com) — no credit card needed",
        "2. Run: `LLM_BASE_URL=https://api.groq.com/openai/v1 LLM_MODEL=llama-3.1-70b-versatile LLM_API_KEY=<key> bash harness_pjt/benchmark/run_benchmark_quick.sh`",
    ]

    out_md = os.path.join(OUT_DIR, "fast_benchmark_results.md")
    Path(out_md).write_text("\n".join(lines), encoding="utf-8")
    print(f"[output] Markdown: {out_md}")

    return payload


async def main():
    print("="*60)
    print("WikiGraph Fast Benchmark (Scenario 1 — Retrieval Only)")
    print("="*60)
    print(f"Data: {DATA_ROOT}")
    print(f"Work: {WORK_DIR}")
    print(f"Out:  {OUT_DIR}")

    if not Path(DATA_ROOT).is_dir():
        print(f"ERROR: data_root not found: {DATA_ROOT}")
        return

    rag = await build_rag()

    # Check existing progress
    progress_path = os.path.join(WORK_DIR, "ingest_progress.json")
    if Path(progress_path).exists():
        p = json.loads(Path(progress_path).read_text())
        done = len(p.get("ingested", []))
        print(f"\n[fast-bench] Resuming: {done} files already ingested")

    await fast_ingest(rag)

    await run_eval(rag)
    await rag.finalize_storages()
    print("\n[fast-bench] Done!")


if __name__ == "__main__":
    asyncio.run(main())
