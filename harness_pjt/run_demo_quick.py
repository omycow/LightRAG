"""
Quick end-to-end test: 1 small doc, local embedding, remote LLM.
"""

import sys, os, shutil, asyncio
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sentence_transformers import SentenceTransformer
from lightrag import LightRAG, QueryParam
from lightrag.llm.openai import openai_complete_if_cache
from lightrag.utils import EmbeddingFunc

LLM_BASE_URL = "http://222.117.133.162:30010/v1"
LLM_MODEL = "qwen-task-pool"
LLM_API_KEY = "asdf"
EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384
WORK_DIR = "/tmp/lightrag_hybrid_demo"

print(f"Loading embedding model: {EMBED_MODEL_NAME} ...")
embed_model = SentenceTransformer(EMBED_MODEL_NAME)
print("Done.\n")


async def llm_func(prompt, system_prompt=None, history_messages=[], **kwargs):
    return await openai_complete_if_cache(
        LLM_MODEL,
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        **kwargs,
    )


async def local_embed(texts: list[str]) -> np.ndarray:
    return embed_model.encode(texts, normalize_embeddings=True)


async def main():
    if os.path.exists(WORK_DIR):
        shutil.rmtree(WORK_DIR)

    rag = LightRAG(
        working_dir=WORK_DIR,
        llm_model_func=llm_func,
        llm_model_max_async=2,
        embedding_func=EmbeddingFunc(
            embedding_dim=EMBED_DIM,
            max_token_size=8192,
            func=local_embed,
        ),
        addon_params={
            "enable_hybrid_search": True,
            "hybrid_search_mode": "hybrid",
        },
    )
    await rag.initialize_storages()
    print(f"LightRAG ready  (hybrid_search={rag._addon_params['enable_hybrid_search']})\n")

    # Use 1 small doc for quick test
    doc_path = os.path.join(os.path.dirname(__file__), "..", "docs", "FrontendBuildGuide.md")
    with open(doc_path, "r") as f:
        text = f.read()
    print(f"Document: FrontendBuildGuide.md ({len(text):,} chars)\n")

    print("Inserting (chunk → LLM extract → embed → BM25) ...")
    track_id = await rag.ainsert([text], file_paths=["FrontendBuildGuide.md"])
    print(f"Insert complete (track_id: {track_id})\n")

    print("=== BM25 Index ===")
    for name, idx in [
        ("Chunks", rag._bm25_chunks),
        ("Entities", rag._bm25_entities),
        ("Relations", rag._bm25_relations),
    ]:
        built = idx and idx.is_built
        count = len(idx.corpus_ids) if built else 0
        print(f"  {name:12s}: {count} indexed")
    print()

    # Compare 3 modes
    query = "How to build the frontend WebUI?"
    print("=" * 60)
    print(f'Query: "{query}"')
    print("=" * 60)

    param = QueryParam(mode="naive", top_k=5)
    labels = {
        "vector_only": "Vector Only",
        "keyword_only": "Keyword (BM25)",
        "hybrid": "Hybrid (RRF)",
    }
    results = {}
    for sm in ["vector_only", "keyword_only", "hybrid"]:
        rag._addon_params["hybrid_search_mode"] = sm
        r = await rag.aquery_data(query, param=param)
        chunks = []
        if r.get("status") == "success":
            chunks = r.get("data", {}).get("chunks", [])
        results[sm] = chunks
        print(f"\n  [{labels[sm]}] {len(chunks)} chunks")
        for c in chunks[:3]:
            print(f'    - {c.get("content", "")[:80]}...')
        if not chunks:
            print("    (no results)")

    v = {c.get("content", "")[:50] for c in results["vector_only"]}
    k = {c.get("content", "")[:50] for c in results["keyword_only"]}
    only_k = k - v
    only_v = v - k
    if only_k:
        print(f"\n  -> BM25 found {len(only_k)} chunk(s) that Vector missed")
    if only_v:
        print(f"  -> Vector found {len(only_v)} chunk(s) that BM25 missed")

    await rag.finalize_storages()
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
