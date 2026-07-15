#!/bin/bash
# Auto-restart wrapper for Groq benchmark.
# On 429 rate limit: waits 5 min and retries automatically.
cd /home/bwkim_u/harness_pjt/LightRAG
exec python3 harness_pjt/benchmark/auto_runner.py \
    harness_pjt/benchmark/run_benchmark_groq.sh \
    harness_pjt/benchmark/results/benchmark_groq.log
