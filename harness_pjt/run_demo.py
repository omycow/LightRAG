"""
LightRAG Hybrid Search Demo — local embedding version
Runs the same flow as demo_hybrid_search.ipynb but as a plain script.

Embedding: sentence-transformers/all-MiniLM-L6-v2 (384-dim, CPU)
LLM: remote server at 222.117.133.162:30010
"""

import sys, os, shutil, asyncio
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sentence_transformers import SentenceTransformer
from lightrag import LightRAG, QueryParam
from lightrag.llm.openai import openai_complete_if_cache
from lightrag.utils import EmbeddingFunc

# ── Config ──────────────────────────────────────────────────────────
LLM_BASE_URL = "http://222.117.133.162:30010/v1"
LLM_MODEL = "qwen-task-pool"
LLM_API_KEY = "asdf"
EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384
WORK_DIR = "/tmp/lightrag_hybrid_demo"
# ────────────────────────────────────────────────────────────────────

print(f"Loading local embedding model: {EMBED_MODEL_NAME} ...")
embed_model = SentenceTransformer(EMBED_MODEL_NAME)
print("Embedding model loaded.\n")


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
    # ── 1. Init LightRAG ──
    if os.path.exists(WORK_DIR):
        shutil.rmtree(WORK_DIR)

    rag = LightRAG(
        working_dir=WORK_DIR,
        llm_model_func=llm_func,
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

    print(f"working_dir: {WORK_DIR}")
    print(f"enable_hybrid_search: {rag._addon_params['enable_hybrid_search']}")
    print(f"hybrid_search_mode:   {rag._addon_params['hybrid_search_mode']}")
    print()

    # ── 2. Load docs ──
    docs_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    md_files = []
    for f in os.listdir(docs_dir):
        if f.endswith(".md") and "-zh" not in f:
            path = os.path.join(docs_dir, f)
            md_files.append((f, os.path.getsize(path)))

    md_files.sort(key=lambda x: x[1], reverse=True)
    selected = md_files[:3]

    documents = []
    file_paths = []
    for fname, size in selected:
        path = os.path.join(docs_dir, fname)
        with open(path, "r") as f:
            text = f.read()
        documents.append(text)
        file_paths.append(fname)
        print(f"  {fname:45s} {size:>8,} bytes  ({len(text):,} chars)")

    print(f"\nTotal: {len(documents)} documents, {sum(len(d) for d in documents):,} chars\n")

    # ── 3. Insert ──
    print("Inserting documents (chunking → LLM extract → embed → BM25 index)...")
    track_id = await rag.ainsert(documents, file_paths=file_paths)
    print(f"Insert complete (track_id: {track_id})\n")

    print("=== BM25 Index Status ===")
    for name, idx in [
        ("Chunks", rag._bm25_chunks),
        ("Entities", rag._bm25_entities),
        ("Relations", rag._bm25_relations),
    ]:
        built = idx and idx.is_built
        count = len(idx.corpus_ids) if built else 0
        print(f"  {name:12s}: {count} indexed")
    print()

    # ── 4. Storage overview ──
    print("=== Storage Files ===")
    for f in sorted(os.listdir(WORK_DIR)):
        size = os.path.getsize(os.path.join(WORK_DIR, f))
        tag = ""
        if "vdb_" in f:
            tag = "[VectorDB]"
        elif "graph_" in f:
            tag = "[Graph]"
        elif "kv_" in f:
            tag = "[KV]"
        print(f"  {f:50s} {size:>10,} bytes  {tag}")
    print()

    # ── 5. BM25 direct query test ──
    bm25_e = rag._bm25_entities
    bm25_c = rag._bm25_chunks
    test_queries = ["chunking pipeline", "API server", "paragraph semantic", "embedding"]
    print("=== BM25 Direct Query ===")
    for q in test_queries:
        ent_hits = bm25_e.query(q, top_k=3)
        chunk_hits = bm25_c.query(q, top_k=2)
        print(f'\n  "{q}"')
        print(f'    Entities: {[r["id"] for r in ent_hits]}')
        print(f'    Chunks:   {[r["content"][:50] + "..." for r in chunk_hits]}')
    print()

    # ── 6. Compare 3 modes ──
    async def compare_modes(query, mode="naive"):
        print("=" * 70)
        print(f'Query: "{query}"')
        print("=" * 70)

        param = QueryParam(mode=mode, top_k=5)
        results = {}

        for sm in ["vector_only", "keyword_only", "hybrid"]:
            rag._addon_params["hybrid_search_mode"] = sm
            r = await rag.aquery_data(query, param=param)
            chunks = []
            if r.get("status") == "success":
                chunks = r.get("data", {}).get("chunks", [])
            results[sm] = chunks

        labels = {
            "vector_only": "Vector Only",
            "keyword_only": "Keyword (BM25)",
            "hybrid": "Hybrid (RRF)",
        }
        for sm in ["vector_only", "keyword_only", "hybrid"]:
            chunks = results[sm]
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
        print()
        rag._addon_params["hybrid_search_mode"] = "hybrid"

    await compare_modes("file processing pipeline chunking strategy")
    await compare_modes("REST API server configuration")
    await compare_modes("paragraph semantic chunking")
    await compare_modes("How does LightRAG handle document ingestion and graph construction?")

    # ── 7. Cleanup ──
    await rag.finalize_storages()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
