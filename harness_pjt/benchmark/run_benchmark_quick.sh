#!/bin/bash
# Quick benchmark: priority ingest only (benchmark-referenced files), both scenarios
# Run from: /home/bwkim_u/harness_pjt/LightRAG/
# Output goes to harness_pjt/benchmark/results/

set -e
cd /home/bwkim_u/harness_pjt/LightRAG

RESULTS_DIR="harness_pjt/benchmark/results"
mkdir -p "$RESULTS_DIR"

export DATA_ROOT="/home/bwkim_u/harness_pjt/shs-poc-ragflow/data"
export LLM_BASE_URL="http://222.117.133.162:30010/v1"
export LLM_MODEL="qwen-task-pool"
export LLM_API_KEY="asdf"

echo "============================================"
echo "Evolving LightRAG — SHS Benchmark"
echo "$(date)"
echo "============================================"
echo ""
echo "Scenario: all (1 + 2)"
echo "Ingest: priority (benchmark-referenced files only)"
echo "Results: $RESULTS_DIR/"
echo ""

python3 harness_pjt/benchmark/run_benchmark.py \
    --data-root "$DATA_ROOT" \
    --work-dir /tmp/wikigraph_benchmark_run \
    --scenario all \
    --ingest-mode priority \
    --evolve-cycles 3 \
    --top-k 20 \
    --out-dir "$RESULTS_DIR" \
    2>&1 | tee "$RESULTS_DIR/benchmark_run.log"

echo ""
echo "Done! Results in $RESULTS_DIR/"
ls -la "$RESULTS_DIR/"
