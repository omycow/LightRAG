#!/bin/bash
# Scenario 2 ver4 — EVOLVE with WEIGHT chunk pick mode
# Root cause fix: VECTOR mode filters spec chunks by cosine similarity.
# WEIGHT mode allocates min 1 chunk per entity → spec chunks appear in results.
set -eo pipefail
cd /home/bwkim_u/harness_pjt/LightRAG

export DATA_ROOT="/home/bwkim_u/harness_pjt/shs-poc-ragflow/data"
export LLM_BASE_URL="http://222.117.133.162:30010/v1"
export LLM_MODEL="qwen-task-pool"
export LLM_API_KEY="asdf"
export MAX_ASYNC=16
export LLM_TIMEOUT=900

ITER_LOG="harness_pjt/evaluation/scenario2-ver4/results/iter_$(date +%Y%m%d_%H%M%S).log"

echo "============================================"
echo "Scenario 2 ver4 — EVOLVE + WEIGHT mode"
echo "$(date)"
echo "============================================"

python3 harness_pjt/evaluation/scenario2-ver4/iterative_eval.py \
    2>&1 | tee "$ITER_LOG"

echo ""
echo "Done: $(date)"
echo "Log: $ITER_LOG"
