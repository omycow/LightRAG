#!/bin/bash
# Benchmark using Claude Haiku for LLM (entity extraction) — much faster than Qwen.
# Requires: ANTHROPIC_API_KEY environment variable
#
# Usage:
#   export ANTHROPIC_API_KEY="sk-ant-..."
#   bash harness_pjt/benchmark/run_benchmark_claude.sh

set -e
cd /home/bwkim_u/harness_pjt/LightRAG

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "ERROR: ANTHROPIC_API_KEY not set"
    echo "  export ANTHROPIC_API_KEY=sk-ant-..."
    exit 1
fi

RESULTS_DIR="harness_pjt/benchmark/results_claude"
mkdir -p "$RESULTS_DIR"

export DATA_ROOT="/home/bwkim_u/harness_pjt/shs-poc-ragflow/data"
export RAG_LLM="claude"
export CLAUDE_MODEL="claude-haiku-4-5-20251001"

echo "============================================"
echo "Evolving LightRAG — SHS Benchmark (Claude)"
echo "$(date)"
echo "LLM: $CLAUDE_MODEL"
echo "============================================"

python3 harness_pjt/benchmark/run_benchmark.py \
    --data-root "$DATA_ROOT" \
    --work-dir /tmp/wikigraph_benchmark_claude \
    --llm-provider claude \
    --claude-model "$CLAUDE_MODEL" \
    --scenario all \
    --ingest-mode priority \
    --evolve-cycles 3 \
    --top-k 20 \
    --out-dir "$RESULTS_DIR" \
    2>&1 | tee "$RESULTS_DIR/benchmark_run.log"

echo "Done! Results in $RESULTS_DIR/"
