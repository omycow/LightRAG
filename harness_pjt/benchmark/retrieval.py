"""Direct chunk retrieval from LightRAG VDB + BM25 for benchmark evaluation.

Bypasses LLM synthesis to evaluate source-level retrieval (like /search/debug).

Key facts:
  - chunks_vdb.query(query, top_k) embeds internally; result['distance'] = cosine similarity
  - file_path in results = normalize_document_file_path(input_path) = basename
  - text_chunks._data[chunk_id]["file_path"] = same basename
  - BM25Index.query returns (chunk_id, score) pairs
"""

from __future__ import annotations

import os
from typing import Any


async def query_vector(rag, query: str, top_k: int = 20) -> list[dict]:
    """Vector-only retrieval: embed query → search chunks_vdb → return with file_path."""
    results = await rag.chunks_vdb.query(query=query, top_k=top_k)
    return _normalize_vdb_results(results)


def query_bm25(rag, query: str, top_k: int = 20) -> list[dict]:
    """BM25-only retrieval from our custom BM25 chunk index.

    BM25Index.query returns list[dict] with {"id", "score", "content"} keys.
    """
    bm25 = getattr(rag, "_bm25_chunks", None)
    if bm25 is None:
        return []
    try:
        raw = bm25.query(query, top_k=top_k)
        if not raw:
            return []
        results = []
        for item in raw:
            chunk_id = item["id"]
            score = float(item.get("score", 0.0))
            # Prefer file_path from text_chunks KV (authoritative), fall back to BM25 content lookup
            file_path = _lookup_file_path(rag, chunk_id)
            content = _lookup_content(rag, chunk_id) or item.get("content", "")
            results.append({
                "id": chunk_id,
                "score": score,
                "file_path": file_path,
                "content": content[:200] if content else "",
            })
        return results
    except Exception as e:
        return []


async def query_hybrid(rag, query: str, top_k: int = 20, k: int = 60) -> list[dict]:
    """Hybrid: RRF fusion of vector + BM25."""
    vec_results = await query_vector(rag, query, top_k=top_k)
    bm25_results = query_bm25(rag, query, top_k=top_k)

    if not bm25_results:
        return vec_results

    vec_by_id = {r["id"]: r for r in vec_results}
    bm25_by_id = {r["id"]: r for r in bm25_results}

    vec_ids = [r["id"] for r in vec_results]
    bm25_ids = [r["id"] for r in bm25_results]
    fused = _rrf(vec_ids, bm25_ids, k=k)

    merged = []
    seen: set[str] = set()
    for doc_id, rrf_score in fused[:top_k]:
        if doc_id in seen:
            continue
        seen.add(doc_id)
        r = dict(vec_by_id.get(doc_id) or bm25_by_id.get(doc_id) or {"id": doc_id, "file_path": ""})
        r["rrf_score"] = rrf_score
        if not r.get("file_path"):
            r["file_path"] = _lookup_file_path(rag, doc_id)
        merged.append(r)

    return merged


def get_retrieved_sources(results: list[dict]) -> list[str]:
    """Extract non-empty file_path strings from retrieval results."""
    return [
        r["file_path"] for r in results
        if r.get("file_path") and r["file_path"] not in ("unknown_source", "")
    ]


def _rrf(list_a: list[str], list_b: list[str], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for i, doc_id in enumerate(list_a):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + i + 1)
    for i, doc_id in enumerate(list_b):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + i + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def _normalize_vdb_results(raw: list[dict]) -> list[dict]:
    """VDB results already include file_path in meta_fields; distance = cosine similarity."""
    out = []
    for r in (raw or []):
        out.append({
            "id": r.get("id", ""),
            "score": float(r.get("distance", 0.0)),  # cosine similarity (higher = better)
            "file_path": r.get("file_path", ""),
            "content": str(r.get("content", ""))[:200],
        })
    return out


def _lookup_file_path(rag, chunk_id: str) -> str:
    """Lookup file_path for a chunk_id via text_chunks KV store."""
    if not chunk_id:
        return ""
    try:
        data = rag.text_chunks._data
        if data is not None:
            entry = data.get(chunk_id)
            if entry and isinstance(entry, dict):
                return entry.get("file_path", "")
    except Exception:
        pass
    return ""


def _lookup_content(rag, chunk_id: str) -> str:
    """Lookup chunk content via text_chunks KV store."""
    if not chunk_id:
        return ""
    try:
        data = rag.text_chunks._data
        if data is not None:
            entry = data.get(chunk_id)
            if entry and isinstance(entry, dict):
                return entry.get("content", "")
    except Exception:
        pass
    return ""


async def query_mix_mode(rag, query: str, top_k: int = 20) -> list[dict]:
    """Run mix mode retrieval (entity graph + BM25 + VDB).

    Uses whatever hybrid_search_mode is configured on the rag instance (default: "hybrid").
    No shared-state mutation — safe for concurrent asyncio calls.
    """
    from lightrag import QueryParam
    try:
        result = await rag.aquery_data(query, param=QueryParam(mode="mix", top_k=top_k))
    except Exception:
        return []
    if result.get("status") != "success":
        return []
    chunks = result.get("data", {}).get("chunks", []) or []
    return [
        {
            "file_path": c.get("file_path", ""),
            "content": c.get("content", "")[:200],
            "chunk_id": c.get("chunk_id", ""),
        }
        for c in chunks
    ]


async def evaluate_single(rag, question: dict, top_k: int = 20) -> dict:
    """Run all retrieval modes for one question and compute source_hit scores.

    Modes evaluated:
    - vector:     direct VDB chunk retrieval (no BM25, no entity graph)
    - bm25:       BM25 keyword retrieval only
    - hybrid:     RRF(VDB + BM25) chunk retrieval (our enhancement, no graph)
    - mix_vector: aquery_data(mode=mix) without BM25 — entity graph + VDB (LightRAG baseline)
    - mix_bm25:   aquery_data(mode=mix) with BM25 — entity graph + VDB + BM25 (our full system)
    """
    from benchmark.questions import source_hit, co_retrieval_hit

    q_text = question["question"]
    expected = question.get("expected_sources", [])
    is_no_context = question.get("no_context", False)
    is_co_retrieval = question.get("category") == "co_retrieval"

    # Chunk-level retrieval (no entity graph)
    vec_results = await query_vector(rag, q_text, top_k=top_k)
    bm25_results = query_bm25(rag, q_text, top_k=top_k)
    hybrid_results = await query_hybrid(rag, q_text, top_k=top_k)

    vec_src  = get_retrieved_sources(vec_results)
    bm25_src = get_retrieved_sources(bm25_results)
    hyb_src  = get_retrieved_sources(hybrid_results)

    # Graph-enhanced mix: entity-graph chunks first (graph signal prioritised),
    # then BM25 fills remaining slots. Mix mode returns ~10 graph-context chunks;
    # merging ensures mix_bm25 is never worse than pure BM25.
    # mix_vec removed — no shared-state mutation needed → safe for asyncio.gather.
    has_graph = _has_entity_graph(rag)
    if has_graph:
        mix_bm25_chunks = await query_mix_mode(rag, q_text, top_k=top_k)
        _graph_bm25 = [c["file_path"] for c in mix_bm25_chunks if c.get("file_path")]
        mix_bm25_src = list(dict.fromkeys(_graph_bm25 + bm25_src))
    else:
        mix_bm25_src = []

    def _hit(sources):
        if is_no_context:
            return len(sources) == 0
        return source_hit(sources, expected)

    result: dict[str, Any] = {
        "id": question.get("id", ""),
        "question": q_text,
        "category": question.get("category", "general"),
        "expected_sources": expected,
        "no_context": is_no_context,
        "has_graph": has_graph,
        "vector":   {"sources": vec_src[:5],      "hit": _hit(vec_src)},
        "bm25":     {"sources": bm25_src[:5],     "hit": _hit(bm25_src), "count": len(bm25_results)},
        "hybrid":   {"sources": hyb_src[:5],      "hit": _hit(hyb_src)},
        "mix_bm25": {"sources": mix_bm25_src[:5], "hit": _hit(mix_bm25_src)},
    }

    if is_co_retrieval:
        expected_card   = question.get("expected_card", "")
        expected_linked = question.get("expected_linked", "")
        result["co_retrieval"] = {
            "vector":   co_retrieval_hit(vec_src,      expected_card, expected_linked),
            "bm25":     co_retrieval_hit(bm25_src,     expected_card, expected_linked),
            "hybrid":   co_retrieval_hit(hyb_src,      expected_card, expected_linked),
            "mix_bm25": co_retrieval_hit(mix_bm25_src, expected_card, expected_linked),
        }

    return result


def _has_entity_graph(rag) -> bool:
    """True only if entity extraction has completed (BM25 entities index is non-empty)."""
    try:
        bm25_ents = getattr(rag, "_bm25_entities", None)
        return bm25_ents is not None and len(bm25_ents.corpus_ids) > 0
    except Exception:
        return False
