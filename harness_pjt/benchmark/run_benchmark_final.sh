#!/bin/bash
# Final benchmark run — local Qwen, project directory, optimal settings.
# max_async=4: verified 4 parallel Qwen calls complete in ~13s (same as 1 sequential = efficient)
# batch_size=8: 8 files per ainsert call
# work_dir: permanent project path (not /tmp)

set -eo pipefail
cd /home/bwkim_u/harness_pjt/LightRAG

RAG_DIR="harness_pjt/rag_data"
RESULTS_DIR="harness_pjt/benchmark/results"
mkdir -p "$RAG_DIR" "$RESULTS_DIR"

export DATA_ROOT="/home/bwkim_u/harness_pjt/shs-poc-ragflow/data"
export LLM_BASE_URL="http://222.117.133.162:30010/v1"
export LLM_MODEL="qwen-task-pool"
export LLM_API_KEY="asdf"
export MAX_ASYNC=16             # 서버 안정성 최적값 (32는 connection error 유발)
export LLM_TIMEOUT=900          # 32 병렬 시 응답 지연 대비
export INGEST_BATCH_SIZE=16     # 한 번에 ainsert할 문서 수
export CHUNK_SIZE=3000          # 청크 크기 → LLM 호출 횟수 감소
export CHUNK_OVERLAP_SIZE=200
export MAX_GLEANING=0           # 청크당 LLM 1회 (기본값 1이면 2회) → 2x 속도 향상
export FORCE_LLM_SUMMARY_ON_MERGE=999  # entity summary merge LLM 호출 비활성화

echo "============================================"
echo "Evolving LightRAG — SHS Benchmark"
echo "$(date)"
echo "LLM  : $LLM_MODEL @ $LLM_BASE_URL"
echo "RAG  : $(pwd)/$RAG_DIR"
echo "Data : $DATA_ROOT"
echo "============================================"

python3 harness_pjt/benchmark/run_benchmark.py \
    --data-root "$DATA_ROOT" \
    --work-dir "$(pwd)/$RAG_DIR" \
    --scenario all \
    --ingest-mode full \
    --evolve-cycles 3 \
    --top-k 20 \
    --out-dir "$RESULTS_DIR" \
    2>&1 | tee "$RESULTS_DIR/benchmark_final.log"

echo ""
echo "============================================"
echo "Done: $(date)"
echo "Results: $RESULTS_DIR/"
ls -lh "$RESULTS_DIR/"*.md 2>/dev/null || echo "(no .md results yet)"
echo "RAG data: $RAG_DIR/ ($(du -sh $(pwd)/$RAG_DIR | cut -f1))"
echo "============================================"
