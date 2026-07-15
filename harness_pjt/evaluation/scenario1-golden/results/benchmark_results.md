# Evolving LightRAG — SHS Benchmark Report

- Generated: 2026-07-06 16:24:26
- Data root: `/home/bwkim_u/harness_pjt/shs-poc-ragflow/data`
- Work dir: `/home/bwkim_u/harness_pjt/LightRAG/harness_pjt/rag_data`
- LLM: `qwen-task-pool` @ `http://222.117.133.162:30010/v1`
- Embedding: `sentence-transformers/all-MiniLM-L6-v2` (local, 384-dim)

## Benchmark Design

| Stage | Description |
|---|---|
| **Scenario 1A** | Initial graph, Vector vs BM25 vs Hybrid source_hit@20 |
| **Scenario 1B** | BM25 keyword advantage on proper nouns / issue IDs |
| **Scenario 2** | After 3×EVOLVE cycles — graph evolution effect on hybrid recall |

**Source matching**: expected_source basename vs retrieved chunk file_path.
A hit = expected file's basename appears in any top-20 retrieved chunk's file_path.

## Scenario 1 — Initial State (Pre-EVOLVE)

Vector similarity vs BM25 keyword vs Hybrid (RRF fusion).


### All-Data Benchmark (17 core + golden + spec)

**Graph-enhanced (entity graph guided, BM25+VDB fill):**

| Mode | Hit | Total | Recall@20 |
|---|---:|---:|---:|
| **Mix+BM25 — graph-enhanced hybrid** | **105** | **106** | **99.1%** |

*Chunk-level retrieval (no entity graph):*

| Mode | Hit | Total | Recall@20 |
|---|---:|---:|---:|
| Vector (VDB) | 99 | 106 | 93.4% |
| BM25 (keyword) | 104 | 106 | 98.1% |
| Hybrid RRF | 105 | 106 | 99.1% |


### Validation Recall — Set A (card recall, 180Q)

**Graph-enhanced (entity graph guided, BM25+VDB fill):**

| Mode | Hit | Total | Recall@20 |
|---|---:|---:|---:|
| **Mix+BM25 — graph-enhanced hybrid** | **179** | **180** | **99.4%** |

*Chunk-level retrieval (no entity graph):*

| Mode | Hit | Total | Recall@20 |
|---|---:|---:|---:|
| Vector (VDB) | 133 | 180 | 73.9% |
| BM25 (keyword) | 179 | 180 | 99.4% |
| Hybrid RRF | 179 | 180 | 99.4% |


### Validation Recall — Set B (co-retrieval, 90Q)

**Graph-enhanced (entity graph guided, BM25+VDB fill):**

| Mode | Hit | Total | Recall@20 |
|---|---:|---:|---:|
| **Mix+BM25 — graph-enhanced hybrid** | **90** | **90** | **100.0%** |

*Chunk-level retrieval (no entity graph):*

| Mode | Hit | Total | Recall@20 |
|---|---:|---:|---:|
| Vector (VDB) | 76 | 90 | 84.4% |
| BM25 (keyword) | 90 | 90 | 100.0% |
| Hybrid RRF | 90 | 90 | 100.0% |


### Set B Co-Retrieval Breakdown (Co-retrieval — BOTH card AND linked found)

| Mode | Card | Linked | **Both** | Co-Recall@20 |
|---|---:|---:|---:|---:|
| Vector | 53 | 56 | 33 | 36.7% |
| BM25 | 89 | 48 | 47 | 52.2% |
| Hybrid RRF | 89 | 53 | 52 | 57.8% |
| **Mix+BM25 (graph)** | **89** | **55** | **54** | **60.0%** |

## Notes & Limitations

- **source_hit metric**: LightRAG stores file_path as basename only (normalize_document_file_path).
  Matching is basename-level. Ambiguity risk is low since files have unique IDs.
- **EVOLVE co-retrieval improvement**: Graph edges added by strategy 1 (co-retrieval)
  improve multi-hop entity traversal but don't directly boost pure vector recall.
  Co-retrieval improvement shows in hybrid mode where graph edges add context.
- **BM25 advantage**: Issue IDs (LYR-455, AUR-905), product codes (EN9100, UF4100),
  and Korean tech terms benefit from keyword matching over vector similarity.
- **Large corpus**: Full 563-file ingest is slow (LLM extraction per chunk).
  Use `--ingest-mode priority` for a quick run on benchmark-referenced files only.
