#!/bin/bash
# Benchmark using Claude CLI subprocess (Haiku model).
# Key: --output-format text only. No --max-turns (that flag enables agent/tool mode → timeout).
# Uses SEPARATE rag_data_claude/ — does NOT interfere with local Qwen run.

set -eo pipefail
cd /home/bwkim_u/harness_pjt/LightRAG

RAG_DIR="harness_pjt/rag_data_claude"
RESULTS_DIR="harness_pjt/benchmark/results"
mkdir -p "$RAG_DIR" "$RESULTS_DIR"

export DATA_ROOT="/home/bwkim_u/harness_pjt/shs-poc-ragflow/data"
export RAG_LLM="claude-cli"
export CLAUDE_CLI_MODEL="claude-haiku-4-5-20251001"
export MAX_ASYNC=2              # 2 parallel: haiku handles 2 concurrent well; auto_runner handles any rate limits
export CHUNK_SIZE=3000          # 3000-token chunks → ~60% fewer LLM calls vs default 1200
export CHUNK_OVERLAP_SIZE=200   # proportional overlap for larger chunks

echo "============================================"
echo "Evolving LightRAG — SHS Benchmark (Claude CLI)"
echo "$(date)"
echo "LLM  : $CLAUDE_CLI_MODEL via claude subprocess"
echo "RAG  : $(pwd)/$RAG_DIR"
echo "============================================"

python3 harness_pjt/benchmark/run_benchmark.py \
    --data-root "$DATA_ROOT" \
    --work-dir "$(pwd)/$RAG_DIR" \
    --scenario all \
    --ingest-mode full \
    --evolve-cycles 3 \
    --top-k 20 \
    --out-dir "$RESULTS_DIR" \
    2>&1 | tee "$RESULTS_DIR/benchmark_claude_cli.log"

echo ""
echo "Done: $(date)"
ls -lh "$RESULTS_DIR/"*.md 2>/dev/null
