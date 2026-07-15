"""Quick smoke test: ingest 3 files, check BM25+vector retrieval works end-to-end."""

import asyncio
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

LLM_BASE_URL = "http://222.117.133.162:30010/v1"
LLM_MODEL    = "qwen-task-pool"
LLM_API_KEY  = "asdf"
EMBED_MODEL  = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM    = 384
WORK_DIR     = "/tmp/wikigraph_smoke"
DATA_ROOT    = "/home/bwkim_u/harness_pjt/shs-poc-ragflow/data"


async def main():
    import shutil
    if os.path.exists(WORK_DIR):
        shutil.rmtree(WORK_DIR)
    os.makedirs(WORK_DIR)

    from sentence_transformers import SentenceTransformer
    from lightrag import LightRAG, QueryParam
    from lightrag.llm.openai import openai_complete_if_cache
    from lightrag.utils import EmbeddingFunc

    print("[smoke] Loading embedding model...")
    embed_model = SentenceTransformer(EMBED_MODEL)

    async def llm_func(prompt, system_prompt=None, history_messages=[], **kwargs):
        return await openai_complete_if_cache(
            LLM_MODEL, prompt, system_prompt=system_prompt,
            history_messages=history_messages, api_key=LLM_API_KEY, base_url=LLM_BASE_URL, **kwargs)

    async def embed_func(texts):
        return embed_model.encode(texts, normalize_embeddings=True)

    rag = LightRAG(
        working_dir=WORK_DIR,
        llm_model_func=llm_func,
        llm_model_max_async=1,
        default_llm_timeout=300,
        embedding_func=EmbeddingFunc(embedding_dim=EMBED_DIM, max_token_size=8192, func=embed_func),
        addon_params={"enable_hybrid_search": True, "hybrid_search_mode": "hybrid"},
    )
    await rag.initialize_storages()
    print("[smoke] LightRAG initialized")

    # Ingest 3 targeted files
    from benchmark.extract import extract_text
    test_files = [
        "confluence/01_FTL_Architecture.html",
        "jira/LYR-455.html",
        "confluence/02_Garbage_Collection.html",
    ]
    texts, paths = [], []
    for rel in test_files:
        abs_path = os.path.join(DATA_ROOT, rel)
        if not os.path.exists(abs_path):
            print(f"  MISSING: {rel}")
            continue
        text = extract_text(abs_path)
        print(f"  {rel}: {len(text)} chars")
        texts.append(text[:10000])
        paths.append(rel)

    print(f"\n[smoke] Ingesting {len(texts)} files...")
    await rag.ainsert(texts, file_paths=paths)
    print("[smoke] Ingest done")

    # Check what was stored
    if hasattr(rag, 'text_chunks') and hasattr(rag.text_chunks, '_data') and rag.text_chunks._data:
        print(f"\n[smoke] text_chunks: {len(rag.text_chunks._data)} chunks")
        # Sample a chunk to check file_path
        sample_id = next(iter(rag.text_chunks._data))
        sample = rag.text_chunks._data[sample_id]
        print(f"  Sample chunk fields: {list(sample.keys())}")
        print(f"  Sample file_path: {sample.get('file_path', 'NOT FOUND')}")
    else:
        print("[smoke] WARNING: text_chunks._data not accessible")

    # Run vector retrieval
    print("\n[smoke] Testing retrieval...")
    from benchmark.retrieval import query_vector, query_bm25, query_hybrid, get_retrieved_sources
    from benchmark.questions import source_hit

    q = "FTL의 L2P 매핑 구조와 동작 방식은?"
    print(f"\n  Query: {q}")

    vec_results = await query_vector(rag, q, top_k=5)
    vec_src = get_retrieved_sources(vec_results)
    print(f"  Vector results ({len(vec_results)}): {vec_src[:3]}")

    bm25_results = query_bm25(rag, q, top_k=5)
    bm25_src = get_retrieved_sources(bm25_results)
    print(f"  BM25 results ({len(bm25_results)}): {bm25_src[:3]}")

    hyb_results = await query_hybrid(rag, q, top_k=5)
    hyb_src = get_retrieved_sources(hyb_results)
    print(f"  Hybrid results ({len(hyb_results)}): {hyb_src[:3]}")

    expected = ["confluence/01_FTL_Architecture.html"]
    print(f"\n  Expected: {expected}")
    print(f"  Vector hit: {source_hit(vec_src, expected)}")
    print(f"  BM25 hit:   {source_hit(bm25_src, expected)}")
    print(f"  Hybrid hit: {source_hit(hyb_src, expected)}")

    # Test issue ID query (BM25 advantage)
    q2 = "LYR-455의 근본 원인과 제안된 flush 정책은?"
    print(f"\n  Query: {q2}")
    vec2 = get_retrieved_sources(await query_vector(rag, q2, top_k=5))
    bm25_2 = get_retrieved_sources(query_bm25(rag, q2, top_k=5))
    hyb2 = get_retrieved_sources(await query_hybrid(rag, q2, top_k=5))
    expected2 = ["jira/LYR-455.html"]
    print(f"  Expected: {expected2}")
    print(f"  Vector hit: {source_hit(vec2, expected2)} → {vec2[:3]}")
    print(f"  BM25 hit:   {source_hit(bm25_2, expected2)} → {bm25_2[:3]}")
    print(f"  Hybrid hit: {source_hit(hyb2, expected2)} → {hyb2[:3]}")

    await rag.finalize_storages()
    print("\n[smoke] PASSED ✓")


if __name__ == "__main__":
    asyncio.run(main())
