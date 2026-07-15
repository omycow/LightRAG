# Evolving LightRAG — Fast Benchmark (Scenario 1 Only)

- Mode: **Retrieval-only** (stub LLM, no entity graph built)
- Generated: 2026-07-04 16:07:46
- Embedding: `sentence-transformers/all-MiniLM-L6-v2` (local, 384-dim)
- Retrieval: vector + BM25 keyword + Hybrid (RRF@k=60)

## Scenario 1 — Initial State (No EVOLVE)

> Note: Entity graph was NOT built (stub LLM). EVOLVE (Scenario 2) requires a real LLM.
> Retrieval metrics are valid — chunks are embedded in VDB + BM25.


### All-Data Benchmark (106Q)

| Mode | Hit | Total | Recall@20 |
|---|---:|---:|---:|
| Vector (VDB) | 102 | 106 | **96.2%** |
| BM25 (keyword) | 105 | 106 | **99.1%** |
| Hybrid (RRF) | 105 | 106 | **99.1%** |


### Validation Set A — Card Recall (180Q)

| Mode | Hit | Total | Recall@20 |
|---|---:|---:|---:|
| Vector (VDB) | 160 | 180 | **88.9%** |
| BM25 (keyword) | 180 | 180 | **100.0%** |
| Hybrid (RRF) | 180 | 180 | **100.0%** |


### Validation Set B — Co-retrieval (90Q)

| Mode | Hit | Total | Recall@20 |
|---|---:|---:|---:|
| Vector (VDB) | 87 | 90 | **96.7%** |
| BM25 (keyword) | 90 | 90 | **100.0%** |
| Hybrid (RRF) | 90 | 90 | **100.0%** |


### Set B Co-retrieval Breakdown (Co-retrieval)

| Mode | Card Hit | Linked Hit | Both (Co-Hit) | Co-Recall@20 |
|---|---:|---:|---:|---:|
| Vector | 66 | 62 | 41 | **45.6%** |
| BM25 | 90 | 52 | 52 | **57.8%** |
| Hybrid | 90 | 59 | 59 | **65.6%** |


## Key Observations

- **BM25 advantage**: Issue IDs (LYR-455, FA-2231), product codes (EN9100, UF4100) → BM25 ranks these first.
- **Vector advantage**: Semantic queries without exact keywords → VDB finds related chunks.
- **Hybrid**: RRF fusion captures best of both — typically best overall recall.
- **EVOLVE effect**: Not measured here — use `run_benchmark.py --llm-provider groq` or Claude API.

## Next Steps

For Scenario 2 (EVOLVE effect measurement):
1. Get [Groq free API key](https://console.groq.com) — no credit card needed
2. Run: `LLM_BASE_URL=https://api.groq.com/openai/v1 LLM_MODEL=llama-3.1-70b-versatile LLM_API_KEY=<key> bash harness_pjt/benchmark/run_benchmark_quick.sh`