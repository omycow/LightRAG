"""StructRAG pipeline smoke test — 10 questions against a golden copy.

Copies golden rag_data → scratch work dir (golden untouched), builds the SG (L1),
runs the full pipeline without LLM (rules tier) and reports hits + latency.

Usage:
  python3 harness_pjt/structrag/smoke_test.py [--work-dir DIR] [--with-llm]
"""

import argparse
import asyncio
import os
import shutil
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT))

GOLDEN = REPO_ROOT / "harness_pjt" / "rag_data"
DATA_ROOT = os.environ.get("DATA_ROOT", str(REPO_ROOT.parents[0] / "shs-poc-ragflow" / "data"))


async def main(work_dir: str, with_llm: bool):
    from harness_pjt.benchmark.run_benchmark import build_rag
    from harness_pjt.benchmark.questions import load_validation_set_b, co_retrieval_hit
    from harness_pjt.structrag.retriever import StructRetriever

    if not Path(work_dir, "kv_store_text_chunks.json").exists():
        print(f"[setup] copying golden → {work_dir}")
        shutil.copytree(str(GOLDEN), work_dir, dirs_exist_ok=True)

    rag, llm_func = await build_rag(work_dir)
    retriever = StructRetriever(rag, work_dir, llm_func=llm_func if with_llm else None)

    stats = retriever.sg.build_l1(rag.text_chunks._data)
    print(f"[sg] {stats} | {retriever.sg.stats}")

    questions = load_validation_set_b(DATA_ROOT)[:10]
    hits = 0
    t0 = time.time()
    for i, q in enumerate(questions, 1):
        ret = await retriever.retrieve(q["question"], top_k=20)
        srcs = ret["retrieved_docs"]
        co = co_retrieval_hit(srcs[:20], q["expected_card"], q.get("expected_linked", ""))
        hits += bool(co["co_hit"])
        print(f"  Q{i:2d} co@20={'✓' if co['co_hit'] else '✗'} "
              f"quality={ret['quality']:.2f} sg_expand={ret['sg_expand']} "
              f"lat={ret['latency_ms']}ms tier={ret['plan'].source}")

    print(f"\n[smoke] co@20: {hits}/{len(questions)}  total={round(time.time()-t0,1)}s")
    retriever.save()
    await rag.finalize_storages()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--work-dir", default="/tmp/structrag_smoke/rag_data")
    p.add_argument("--with-llm", action="store_true")
    args = p.parse_args()
    asyncio.run(main(args.work_dir, args.with_llm))
