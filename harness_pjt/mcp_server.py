"""MCP server exposing Evolving LightRAG (WikiGraph) to Claude Code / Claude.ai.

Usage:
    # With Qwen backend (local server):
    python3 harness_pjt/mcp_server.py

    # With Claude API backend (fast entity extraction):
    ANTHROPIC_API_KEY=sk-ant-... python3 harness_pjt/mcp_server.py --llm claude

Claude Code config (add to claude_desktop_config.json or .claude/settings.json):
{
  "mcpServers": {
    "wikigraph-rag": {
      "command": "python3",
      "args": ["/home/bwkim_u/harness_pjt/LightRAG/harness_pjt/mcp_server.py"],
      "env": {
        "ANTHROPIC_API_KEY": "<your-key>",
        "RAG_LLM": "claude",
        "RAG_WORK_DIR": "/tmp/wikigraph_benchmark_run"
      }
    }
  }
}
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))   # harness_pjt/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # LightRAG/

from fastmcp import FastMCP

# ── Configuration ─────────────────────────────────────────────────────────────

WORK_DIR       = os.environ.get("RAG_WORK_DIR", "/tmp/wikigraph_mcp")
LLM_PROVIDER   = os.environ.get("RAG_LLM", "qwen")   # "claude" or "qwen"
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL   = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
QWEN_BASE_URL  = os.environ.get("LLM_BASE_URL", "http://222.117.133.162:30010/v1")
QWEN_MODEL     = os.environ.get("LLM_MODEL", "qwen-task-pool")
QWEN_API_KEY   = os.environ.get("LLM_API_KEY", "asdf")
EMBED_MODEL    = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM      = 384

# ── LightRAG singleton ────────────────────────────────────────────────────────

_rag = None
_agent = None
_embed_model = None


async def _get_rag():
    global _rag, _embed_model
    if _rag is not None:
        return _rag

    from sentence_transformers import SentenceTransformer
    from lightrag import LightRAG
    from lightrag.utils import EmbeddingFunc

    _embed_model = SentenceTransformer(EMBED_MODEL)

    async def embed_func(texts):
        return _embed_model.encode(texts, normalize_embeddings=True)

    if LLM_PROVIDER == "claude" and ANTHROPIC_KEY:
        from lightrag.llm.anthropic import anthropic_complete_if_cache

        async def llm_func(prompt, system_prompt=None, history_messages=None, **kwargs):
            return await anthropic_complete_if_cache(
                CLAUDE_MODEL, prompt,
                system_prompt=system_prompt,
                history_messages=history_messages or [],
                api_key=ANTHROPIC_KEY,
                **kwargs,
            )
    else:
        from lightrag.llm.openai import openai_complete_if_cache

        async def llm_func(prompt, system_prompt=None, history_messages=None, **kwargs):
            return await openai_complete_if_cache(
                QWEN_MODEL, prompt,
                system_prompt=system_prompt,
                history_messages=history_messages or [],
                api_key=QWEN_API_KEY,
                base_url=QWEN_BASE_URL,
                **kwargs,
            )

    os.makedirs(WORK_DIR, exist_ok=True)
    _rag = LightRAG(
        working_dir=WORK_DIR,
        llm_model_func=llm_func,
        llm_model_max_async=4 if LLM_PROVIDER == "claude" else 2,
        default_llm_timeout=120,
        embedding_func=EmbeddingFunc(
            embedding_dim=EMBED_DIM,
            max_token_size=8192,
            func=embed_func,
        ),
        addon_params={"enable_hybrid_search": True, "hybrid_search_mode": "hybrid"},
    )
    await _rag.initialize_storages()
    return _rag


async def _get_agent():
    global _agent
    if _agent is not None:
        return _agent
    rag = await _get_rag()
    from wikigraph.agent import WikiGraphAgent
    from wikigraph.config import WikiGraphConfig
    config = WikiGraphConfig(working_dir=WORK_DIR, auto_evolve_interval=20)
    _agent = WikiGraphAgent(rag, config)
    return _agent


# ── MCP Server ────────────────────────────────────────────────────────────────

mcp = FastMCP(
    "WikiGraph-RAG",
    instructions=(
        "SHS 하드웨어/펌웨어 지식 베이스 (Evolving LightRAG).\n"
        "query_knowledge_base: 자연어로 질문\n"
        "search_document_sources: 관련 문서 소스 검색\n"
        "ingest_document: 새 문서 추가\n"
        "get_stats: 그래프 통계"
    ),
)


@mcp.tool(
    description=(
        "SHS 하드웨어/펌웨어 지식 베이스에 자연어 질문을 합니다. "
        "FTL, GC, PLP, NVMe, UFS, Jira 이슈, Aurora SoC 등 내부 기술 문서 기반으로 답합니다."
    )
)
async def query_knowledge_base(question: str, mode: str = "hybrid") -> str:
    """Query the SHS knowledge base using WikiGraph RAG.

    Args:
        question: Natural language question in Korean or English
        mode: Retrieval mode — "hybrid" (default), "vector", "naive", "local", "global"

    Returns:
        LLM-synthesized answer with source citations
    """
    rag = await _get_rag()
    from lightrag import QueryParam

    valid_modes = {"hybrid", "vector", "naive", "local", "global", "mix"}
    if mode not in valid_modes:
        mode = "hybrid"

    result = await rag.aquery(question, param=QueryParam(mode=mode))
    return result or "(답변을 생성하지 못했습니다.)"


@mcp.tool(
    description=(
        "벡터+BM25 하이브리드 검색으로 관련 문서 소스를 찾습니다. "
        "어떤 문서가 질문과 관련이 있는지 빠르게 확인할 때 사용합니다."
    )
)
async def search_document_sources(query: str, top_k: int = 10) -> list[dict]:
    """Search for relevant document sources by query.

    Args:
        query: Search query (Korean or English)
        top_k: Number of results to return (default 10)

    Returns:
        List of {file_path, score, content_preview} sorted by relevance
    """
    rag = await _get_rag()
    from benchmark.retrieval import query_hybrid, get_retrieved_sources

    results = await query_hybrid(rag, query, top_k=top_k)
    seen: set[str] = set()
    output = []
    for r in results:
        fp = r.get("file_path", "")
        if fp and fp not in seen:
            seen.add(fp)
            output.append({
                "file_path": fp,
                "score": round(r.get("rrf_score", r.get("score", 0.0)), 4),
                "content_preview": r.get("content", "")[:300],
            })
    return output


@mcp.tool(
    description=(
        "새 문서를 지식 그래프에 추가합니다. "
        "텍스트를 직접 제공하거나 file_path를 지정해 파일을 읽을 수 있습니다."
    )
)
async def ingest_document(text: str = "", file_path: str = "") -> str:
    """Ingest a document into the knowledge graph.

    Args:
        text: Document text (if provided directly)
        file_path: Path to a file to read and ingest (absolute or relative to data dir)

    Returns:
        Status message
    """
    if not text and not file_path:
        return "오류: text 또는 file_path 중 하나를 제공해야 합니다."

    if not text and file_path:
        from benchmark.extract import extract_text
        try:
            text = extract_text(file_path)
        except Exception as e:
            return f"파일 읽기 실패: {e}"

    if not text.strip():
        return "오류: 비어있는 문서는 추가할 수 없습니다."

    rag = await _get_rag()
    label = file_path or "direct_input"
    await rag.ainsert([text[:50_000]], file_paths=[label])
    return f"문서 추가 완료: {label} ({len(text)} chars)"


@mcp.tool(
    description="현재 지식 그래프의 통계를 반환합니다 (청크 수, 엔티티 수, 관계 수, 쿼리 로그 크기)."
)
async def get_stats() -> dict[str, Any]:
    """Return current knowledge graph statistics."""
    rag = await _get_rag()
    chunks = 0
    if hasattr(rag, "text_chunks") and hasattr(rag.text_chunks, "_data"):
        chunks = len(rag.text_chunks._data or {})

    bm25_chunks = 0
    bm25 = getattr(rag, "_bm25_chunks", None)
    if bm25 is not None:
        bm25_chunks = len(bm25.corpus_ids)

    graph = rag.chunk_entity_relation_graph
    try:
        entities = len(await graph.get_all_labels())
    except Exception:
        entities = -1

    agent = _agent
    total_queries = 0
    if agent is not None:
        total_queries = len(agent._state.get("query_log", []))

    return {
        "work_dir": WORK_DIR,
        "llm_provider": LLM_PROVIDER,
        "chunks_indexed": chunks,
        "bm25_chunks": bm25_chunks,
        "graph_entities": entities,
        "total_queries_logged": total_queries,
    }


@mcp.tool(
    description=(
        "쿼리 로그를 분석해 지식 그래프를 진화시킵니다. "
        "자주 함께 검색된 엔티티 간의 새 관계를 추가하고 지식 갭을 채웁니다."
    )
)
async def evolve_knowledge_graph() -> dict:
    """Trigger WikiGraph EVOLVE: analyze query patterns and strengthen the graph."""
    agent = await _get_agent()
    result = await agent.evolve()
    return {
        "applied": result.get("applied", 0),
        "messages": result.get("messages", []),
    }


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", choices=["claude", "qwen"], default=None,
                        help="Override RAG_LLM env variable")
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"],
                        default="stdio")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.llm:
        os.environ["RAG_LLM"] = args.llm
        LLM_PROVIDER = args.llm
    if args.work_dir:
        os.environ["RAG_WORK_DIR"] = args.work_dir
        WORK_DIR = args.work_dir

    llm_info = (
        f"Claude ({CLAUDE_MODEL})" if LLM_PROVIDER == "claude" and ANTHROPIC_KEY
        else f"Qwen ({QWEN_MODEL} @ {QWEN_BASE_URL})"
    )
    print(f"WikiGraph-RAG MCP Server", flush=True)
    print(f"  LLM: {llm_info}", flush=True)
    print(f"  Work dir: {WORK_DIR}", flush=True)
    print(f"  Transport: {args.transport}", flush=True)

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=args.transport, port=args.port)
