"""Standalone SG verification — L1 build against golden chunks (read-only on golden).

Usage: python3 harness_pjt/structrag/verify_sg.py [--work-dir DIR]
Builds the SG in a scratch dir from golden text_chunks and reports:
  - node/edge counts per layer
  - Val-B card→linked pair coverage by L1 edges (reference metric, not a training signal)
  - scope/neighbor traversal sanity on a few sample docs
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT))

from harness_pjt.structrag.structure_graph import StructureGraph, _pair_key

GOLDEN_CHUNKS = REPO_ROOT / "harness_pjt" / "rag_data" / "kv_store_text_chunks.json"
VALB = Path(os.environ.get(
    "DATA_ROOT", str(REPO_ROOT.parents[0] / "shs-poc-ragflow" / "data")
)) / "validation" / "validation_coretrieval_questions.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", default=None, help="where to write structrag_meta (default: temp dir)")
    args = parser.parse_args()

    work_dir = args.work_dir or tempfile.mkdtemp(prefix="sg_verify_")
    chunks = json.loads(GOLDEN_CHUNKS.read_text(encoding="utf-8"))

    sg = StructureGraph(work_dir)
    stats = sg.build_l1(chunks, rebuild=True)
    print(f"[build] {stats}")
    print(f"[stats] {sg.stats}")

    # Val-B coverage (reference only — never used for training)
    if VALB.exists():
        qs = json.loads(VALB.read_text(encoding="utf-8"))
        edges = sg._data["edges"]
        cov = 0
        missing = []
        for q in qs:
            card = os.path.basename(q["expected_card"])
            linked = os.path.basename(q.get("expected_linked", ""))
            if linked and _pair_key(card, linked) in edges:
                cov += 1
            else:
                missing.append((card, linked))
        print(f"[val-b] L1 edge coverage: {cov}/{len(qs)}")
        for m in missing[:5]:
            print(f"        missing: {m[0]} ↔ {m[1]}")

    # traversal sanity
    sample = list(sg._data["nodes"])[:3]
    for doc in sample:
        nbs = sg.get_neighbors(doc, top_n=3)
        print(f"[nbrs] {doc} → {nbs}")
    if sample:
        scope = sg.get_scope(sample[:2], budget=8)
        print(f"[scope] seeds={sample[:2]} → {len(scope)} docs")

    print(f"[done] SG written under {work_dir}/structrag_meta/")


if __name__ == "__main__":
    main()
