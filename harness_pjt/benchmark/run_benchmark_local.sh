#!/bin/bash
# Benchmark using local Qwen server (qwen-task-pool) — no rate limits.
# Server: http://222.117.133.162:30010/v1 (~3-15s per call, max_async=2)

set -e
cd /home/bwkim_u/harness_pjt/LightRAG

RESULTS_DIR="harness_pjt/benchmark/results"
mkdir -p "$RESULTS_DIR"

export DATA_ROOT="/home/bwkim_u/harness_pjt/shs-poc-ragflow/data"
export LLM_BASE_URL="http://222.117.133.162:30010/v1"
export LLM_MODEL="qwen-task-pool"
export LLM_API_KEY="asdf"
# No rate limits on local server — max_async=2 for throughput
export GROQ_MAX_ASYNC=2

echo "============================================"
echo "Evolving LightRAG — SHS Benchmark (Local Qwen)"
echo "$(date)"
echo "LLM: $LLM_MODEL @ $LLM_BASE_URL"
echo "============================================"

python3 harness_pjt/benchmark/run_benchmark.py \
    --data-root "$DATA_ROOT" \
    --work-dir /tmp/wikigraph_local \
    --scenario all \
    --ingest-mode priority \
    --evolve-cycles 3 \
    --top-k 20 \
    --out-dir "$RESULTS_DIR" \
    2>&1 | tee "$RESULTS_DIR/benchmark_local.log"

echo "Done! Results in $RESULTS_DIR/"
ls -la "$RESULTS_DIR/"
