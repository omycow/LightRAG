#!/bin/bash
# Auto-restart wrapper for Claude CLI benchmark.
# On usage limit: parses reset time and waits automatically.
cd /home/bwkim_u/harness_pjt/LightRAG
exec python3 harness_pjt/benchmark/auto_runner.py \
    harness_pjt/benchmark/run_benchmark_claude_cli.sh \
    harness_pjt/benchmark/results/benchmark_claude_cli.log
