#!/bin/bash
# Benchmark using Groq compound API.
# compound-beta / compound-beta-mini: 30 RPM, 250 RPD, 70K context.
# Uses SEPARATE rag_data_groq/ — does NOT interfere with other runs.

set -eo pipefail
cd /home/bwkim_u/harness_pjt/LightRAG

RAG_DIR="harness_pjt/rag_data_groq"
RESULTS_DIR="harness_pjt/benchmark/results"
mkdir -p "$RAG_DIR" "$RESULTS_DIR"

export DATA_ROOT="/home/bwkim_u/harness_pjt/shs-poc-ragflow/data"
export RAG_LLM="groq"
# Set GROQ_API_KEY in your environment before running: export GROQ_API_KEY=gsk_...
if [ -z "$GROQ_API_KEY" ]; then
    echo "ERROR: GROQ_API_KEY not set. Run: export GROQ_API_KEY=gsk_..."
    exit 1
fi
export GROQ_MODEL="${GROQ_MODEL:-llama-3.1-8b-instant}"
# llama-3.1-8b-instant: 6K TPM, 14.4K RPD — compound 라우팅 없음, 독립 한도
# compound/llama-4-scout/llama-3.3-70b 모두 groq/compound RPD 250 공유라 사용 불가
export MAX_ASYNC=1              # 6K TPM: 4K토큰/콜 → 순차 처리
export CHUNK_SIZE=1200          # ~4K 토큰/콜 (시스템프롬프트2K + 청크1.2K + 출력0.8K)
export CHUNK_OVERLAP_SIZE=100

echo "============================================"
echo "Evolving LightRAG — SHS Benchmark (Groq)"
echo "$(date)"
echo "LLM  : $GROQ_MODEL via Groq API"
echo "RAG  : $(pwd)/$RAG_DIR"
echo "============================================"

python3 harness_pjt/benchmark/run_benchmark.py \
    --data-root "$DATA_ROOT" \
    --work-dir "$(pwd)/$RAG_DIR" \
    --scenario all \
    --ingest-mode priority \
    --evolve-cycles 3 \
    --top-k 20 \
    --llm-provider groq \
    --out-dir "$RESULTS_DIR" \
    2>&1 | tee "$RESULTS_DIR/benchmark_groq.log"

echo ""
echo "Done: $(date)"
ls -lh "$RESULTS_DIR/"*.md 2>/dev/null
