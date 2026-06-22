"""End-to-end test for WikiGraph Agent."""

import sys, os, shutil, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
from sentence_transformers import SentenceTransformer
from lightrag import LightRAG, QueryParam
from lightrag.llm.openai import openai_complete_if_cache
from lightrag.utils import EmbeddingFunc

LLM_BASE_URL = "http://222.117.133.162:30010/v1"
LLM_MODEL = "qwen-task-pool"
LLM_API_KEY = "asdf"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384
WORK_DIR = "/tmp/wikigraph_test"

print("Loading embedding model...")
embed_model = SentenceTransformer(EMBED_MODEL)
print("Done.\n")


async def llm_func(prompt, system_prompt=None, history_messages=[], **kwargs):
    return await openai_complete_if_cache(
        LLM_MODEL, prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        **kwargs,
    )


async def embed_func(texts):
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
            func=embed_func,
        ),
        addon_params={
            "enable_hybrid_search": True,
            "hybrid_search_mode": "hybrid",
        },
    )
    await rag.initialize_storages()
    print("LightRAG initialized\n")

    # WikiGraph Agent
    from wikigraph.agent import WikiGraphAgent
    from wikigraph.config import WikiGraphConfig

    config = WikiGraphConfig(working_dir=WORK_DIR)
    agent = WikiGraphAgent(rag, config, llm_func=llm_func)
    print("WikiGraph Agent initialized\n")

    # === INGEST ===
    print("=" * 60)
    print("STEP 1: INGEST")
    print("=" * 60)
    docs_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    doc_file = "FrontendBuildGuide.md"
    with open(os.path.join(docs_dir, doc_file), "r") as f:
        doc_text = f.read()
    print(f"  Document: {doc_file} ({len(doc_text):,} chars)")

    result = await agent.ingest([doc_text], file_paths=[doc_file])
    for msg in result["messages"]:
        print(f"  {msg}")
    print()

    # === QUERY ===
    print("=" * 60)
    print("STEP 2: QUERY (5 queries)")
    print("=" * 60)
    queries = [
        "How to build the frontend WebUI?",
        "What is bun used for?",
        "How to install dependencies?",
        "What build tools does LightRAG use?",
        "How does the frontend deployment work?",
    ]
    for q in queries:
        result = await agent.query(q)
        for msg in result["messages"][-3:]:
            print(f"  {msg}")
        if result.get("evolved"):
            print("  >>> Knowledge evolved!")
        print()

    stats = agent.stats
    print(f"Stats: {stats['total_queries']} queries, {stats['tracked_entities']} entities tracked\n")

    # === EVOLVE ===
    print("=" * 60)
    print("STEP 3: EVOLVE (manual trigger)")
    print("=" * 60)
    result = await agent.evolve()
    for msg in result["messages"]:
        print(f"  {msg}")
    print(f"  Mutations applied: {result['applied']}\n")

    # === LINT ===
    print("=" * 60)
    print("STEP 4: LINT")
    print("=" * 60)
    result = await agent.lint()
    for msg in result["messages"]:
        print(f"  {msg}")
    if result["findings"]:
        print(f"\n  Findings ({len(result['findings'])}):")
        for f in result["findings"][:5]:
            print(f"    [{f['severity']}] {f['finding_type']}: {f['entity_name']}")
    print()

    # === Graph Stats ===
    print("=" * 60)
    print("STEP 5: Graph Overview")
    print("=" * 60)
    import networkx as nx
    G = nx.read_graphml(os.path.join(WORK_DIR, "graph_chunk_entity_relation.graphml"))
    print(f"  Nodes: {G.number_of_nodes()}, Edges: {G.number_of_edges()}")
    evolved = [(u, v) for u, v, d in G.edges(data=True) if "wikigraph_evolve" in d.get("source_id", "")]
    print(f"  Evolved edges: {len(evolved)}")
    for u, v in evolved[:5]:
        print(f"    {u} → {v}")

    await rag.finalize_storages()
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
