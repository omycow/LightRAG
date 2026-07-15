#!/bin/bash
# Scenario 1 — Golden baseline evaluation (run once, never modify)
# Evaluates the completed ingested graph WITHOUT any evolving.
set -eo pipefail
cd /home/bwkim_u/harness_pjt/LightRAG

export DATA_ROOT="/home/bwkim_u/harness_pjt/shs-poc-ragflow/data"
export LLM_BASE_URL="http://222.117.133.162:30010/v1"
export LLM_MODEL="qwen-task-pool"
export LLM_API_KEY="asdf"

RESULTS_DIR="harness_pjt/evaluation/scenario1-golden/results"
mkdir -p "$RESULTS_DIR"

echo "============================================"
echo "Scenario 1 — Golden Baseline"
echo "$(date)"
echo "============================================"

python3 harness_pjt/benchmark/run_benchmark.py \
    --data-root "$DATA_ROOT" \
    --work-dir "$(pwd)/harness_pjt/rag_data" \
    --scenario 1 \
    --ingest-mode skip \
    --top-k 20 \
    --out-dir "$RESULTS_DIR" \
    2>&1 | tee "$RESULTS_DIR/run.log"

echo "Done: $(date)"
echo "Results: $RESULTS_DIR/"
