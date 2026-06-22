# LightRAG Hybrid Search Enhancement
## Vector + BM25 Keyword Search Integration

---

## 1. Problem: Vector-Only Search Limitation

LightRAG's retrieval pipeline relies **entirely on vector similarity search**:

| Search Path | Function | Method |
|---|---|---|
| Chunk Search | `chunks_vdb.query()` | Cosine Similarity |
| Entity Search | `entities_vdb.query()` | Cosine Similarity |
| Relation Search | `relationships_vdb.query()` | Cosine Similarity |

### Limitations

- **Proper nouns**: "GPT-4o", "HKUDS" - embedding approximation misses exact matches
- **Technical terms**: Similar meaning documents get confused
- **Short queries (1-2 words)**: Embedding quality degrades significantly
- **Exact keyword match**: Documents containing the exact query term can be missed

---

## 2. Before: Vector-Only Pipeline

![Before: Vector-Only Pipeline](images/before_vector_only.png)

All three search paths (entities, relationships, chunks) use **only** vector similarity.
No keyword matching path exists.

---

## 3. After: Hybrid Search Pipeline

![After: Hybrid Search Pipeline](images/after_hybrid_search.png)

Each search path now has **dual retrieval**:
- **Blue**: Vector similarity search (existing)
- **Orange**: BM25 keyword search (NEW)

Results are merged via **RRF (Reciprocal Rank Fusion)**.

---

## 4. RRF (Reciprocal Rank Fusion)

![RRF Fusion](images/rrf_fusion.png)

### Formula

```
RRF(d) = SUM  1 / (k + rank_r(d))      k=60
```

### Why RRF?

- Vector and BM25 scores have **completely different scales**
  - Vector: cosine similarity 0~1
  - BM25: TF-IDF weighted score 0~100+
- RRF uses **only rank order**, no score normalization needed
- Industry standard: used in OpenSearch, Elasticsearch

### Example

| Document | Vector Rank | BM25 Rank | RRF Score |
|---|---|---|---|
| Doc_A | #1 | #3 | 1/61 + 1/63 = **0.0323** |
| Doc_C | #3 | #1 | 1/63 + 1/61 = **0.0323** |
| Doc_B | #2 | - | 1/62 = 0.0161 |
| Doc_F | - | #2 | 1/62 = 0.0161 |

Documents found by **both** methods rank higher.

---

## 5. Implementation Architecture

### New Module: `lightrag/bm25_index.py`

```python
class BM25Index:
    """In-memory BM25 index for keyword search."""
    
    def build(self, documents: dict[str, str]) -> None:
        """Build index from {id: text} mapping."""
        
    def query(self, query_text: str, top_k: int = 20) -> list[dict]:
        """BM25 search returning [{id, score}, ...]."""

def reciprocal_rank_fusion(
    vector_results, bm25_results, k=60
) -> list[dict]:
    """Merge vector + BM25 results using RRF."""
```

### Modified Files

| File | Change |
|---|---|
| `lightrag/bm25_index.py` | NEW - BM25Index class + RRF function |
| `lightrag/lightrag.py` | BM25 index build, lazy rebuild, global_config injection |
| `lightrag/operate.py` | Hybrid search in `_get_node_data`, `_get_edge_data`, `_get_vector_context` |
| `lightrag/addon_params.py` | `enable_hybrid_search` default parameter |
| `pyproject.toml` | `rank-bm25` dependency |

---

## 6. BM25 Index Lifecycle

### Build Phase (on `initialize_storages()`)

```
text_chunks (KV)      --> BM25 Chunks Index
entities_vdb (VDB)    --> BM25 Entities Index  
relationships_vdb (VDB) --> BM25 Relations Index
```

### Lazy Invalidation

```
Document Insert --> _insert_done() --> _bm25_stale = True
Next Query     --> aquery_llm()    --> if stale: rebuild indices
```

### Activation

```python
rag = LightRAG(
    addon_params={"enable_hybrid_search": True},  # OFF by default
    ...
)
```

---

## 7. Search Function Changes

### Entity Search (`_get_node_data`)

```python
# Existing: Vector search
results = await entities_vdb.query(query, top_k=...)

# NEW: BM25 hybrid
if entities_bm25 and entities_bm25.is_built:
    bm25_results = entities_bm25.query(query, top_k=...)
    results = reciprocal_rank_fusion(results, bm25_results)
```

### Relation Search (`_get_edge_data`) - Same pattern

### Chunk Search (`_get_vector_context`) - Same pattern

All modifications are **guarded by `enable_hybrid_search` flag**.
Default behavior is **unchanged** (backward compatible).

---

## 8. Expected Results

| Query Type | Vector Only | Hybrid (Vector + BM25) |
|---|---|---|
| Proper nouns ("GPT-4o") | Embedding approximation | BM25 exact match complement |
| Technical terms | Similar meaning confusion | Exact token prioritization |
| Short queries (1-2 words) | Embedding quality issues | BM25 IDF weighting effective |
| Long descriptive queries | Effective | Vector primary + BM25 supplement |

**Expected Recall@K improvement: 15-20%** (especially for keyword-critical queries)

---

## 9. Configuration

```python
# Enable hybrid search
rag = LightRAG(
    working_dir="./my_rag",
    llm_model_func=your_llm_func,
    embedding_func=EmbeddingFunc(...),
    addon_params={
        "enable_hybrid_search": True,
    }
)

# Query - works with all modes
result = await rag.aquery("What is GPT-4o?", param=QueryParam(mode="hybrid"))
```

### Dependencies

```toml
# pyproject.toml
"rank-bm25>=0.2.2,<1.0.0"
```

---

## 10. Demo

See `demo_hybrid_search.ipynb` for:
- BM25 index build verification
- Vector-only vs Hybrid query comparison
- RRF fusion behavior demonstration
