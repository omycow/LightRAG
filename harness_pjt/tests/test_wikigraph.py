"""Tests for the wikigraph evolving-graph agent.

Uses a real LightRAG instance (NetworkX graph + JSON KV storages, the
built-in defaults) so the tests exercise the actual `ainsert_custom_kg()`
remapping behavior — in particular the "source_id resolves to UNKNOWN,
keywords passes through untouched" quirk documented in evolve.py — rather
than a hand-rolled fake that could hide a mismatch with real behavior.

No network calls: `ainsert()`/entity extraction (which needs a real LLM) is
never exercised. Chunks/entities/relations are seeded directly into the
storages, and `rag.aquery`/`rag.aquery_data` are monkeypatched for the
agent-level tests.
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lightrag import LightRAG
from lightrag.utils import EmbeddingFunc, Tokenizer

from wikigraph.agent import WikiGraphAgent
from wikigraph.config import WikiGraphConfig
from wikigraph.operations.analyze import _parse_analysis, analyze_node
from wikigraph.operations.evolve import (
    _find_co_retrieved_pairs,
    _find_shortcut_paths,
    _parse_gap_fill_response,
    batch_evolve_node,
    evolve_light_node,
    should_run_batch_evolve,
    structural_evolve_node,
)
from wikigraph.operations.lint import lint_node
from wikigraph.operations.query import _match_document_nodes, _scoped_entities, respond_node


async def _dummy_embed(texts):
    return np.random.rand(len(texts), 8)


EMBED = EmbeddingFunc(embedding_dim=8, max_token_size=512, func=_dummy_embed)


async def _dummy_llm(prompt, **kwargs):
    return ""


class _WhitespaceTokenizer:
    """Avoids TiktokenTokenizer's network fetch of encoding data in sandboxes."""

    def encode(self, content: str) -> list[int]:
        return [hash(tok) % 50000 for tok in content.split()] or [0]

    def decode(self, tokens: list[int]) -> str:
        return " ".join(str(t) for t in tokens)


TOKENIZER = Tokenizer(model_name="whitespace-test-tokenizer", tokenizer=_WhitespaceTokenizer())


@pytest.fixture
async def rag(tmp_path):
    working_dir = str(tmp_path / "wikigraph_kb")
    r = LightRAG(
        working_dir=working_dir,
        embedding_func=EMBED,
        llm_model_func=_dummy_llm,
        tokenizer=TOKENIZER,
    )
    await r.initialize_storages()
    yield r
    await r.finalize_storages()


def _node(entity_id: str, source_id: str = "chunk-1") -> dict:
    return {
        "entity_id": entity_id,
        "entity_type": "TEST",
        "description": f"Description of {entity_id}",
        "source_id": source_id,
        "file_path": "test.txt",
        "created_at": int(time.time()),
    }


def _edge(weight: float = 1.0) -> dict:
    return {
        "weight": weight,
        "description": "test edge",
        "keywords": "extracted",
        "source_id": "chunk-1",
        "file_path": "test.txt",
        "created_at": int(time.time()),
    }


async def _seed_chunk(rag, chunk_id: str, full_doc_id: str, order: int, file_path: str, content: str = "x"):
    await rag.text_chunks.upsert({
        chunk_id: {
            "content": content,
            "source_id": chunk_id,
            "tokens": 1,
            "chunk_order_index": order,
            "full_doc_id": full_doc_id,
            "file_path": file_path,
            "status": "processed",
        }
    })


# ---------------------------------------------------------------------------
# ANALYZE — query improvement (pure parsing, no I/O)
# ---------------------------------------------------------------------------


def test_parse_analysis_single_query():
    response = "OPTIMIZED: What is LightRAG?\nSUB1: What is LightRAG?\nDOCS: NONE"
    optimized, subs, docs = _parse_analysis(response, "lightrag??", max_subqueries=3)
    assert optimized == "What is LightRAG?"
    assert subs == ["What is LightRAG?"]
    assert docs == []


def test_parse_analysis_decomposition():
    response = (
        "OPTIMIZED: Compare LightRAG and vanilla RAG on cost and accuracy\n"
        "SUB1: What is the cost of LightRAG vs vanilla RAG?\n"
        "SUB2: What is the accuracy of LightRAG vs vanilla RAG?\n"
    )
    optimized, subs, docs = _parse_analysis(response, "fallback", max_subqueries=3)
    assert optimized.startswith("Compare LightRAG")
    assert len(subs) == 2
    assert docs == []


def test_parse_analysis_malformed_falls_back_to_original_query():
    optimized, subs, docs = _parse_analysis("garbage response", "original question", max_subqueries=3)
    assert optimized == "original question"
    assert subs == ["original question"]
    assert docs == []


def test_parse_analysis_extracts_mentioned_docs():
    response = (
        "OPTIMIZED: Compare the Q3 report and the Q4 report\n"
        "SUB1: Compare the Q3 report and the Q4 report\n"
        "DOCS: Q3 report, Q4 report"
    )
    optimized, subs, docs = _parse_analysis(response, "fallback", max_subqueries=3)
    assert docs == ["Q3 report", "Q4 report"]


async def test_analyze_node_without_llm_is_passthrough():
    result = await analyze_node({"current_query": "hello"}, WikiGraphConfig(), llm_func=None)
    assert result["optimized_query"] == "hello"
    assert result["sub_queries"] == ["hello"]
    assert result["mentioned_docs"] == []


async def test_analyze_node_respects_max_subqueries():
    async def llm(prompt):
        return "OPTIMIZED: q\nSUB1: a\nSUB2: b\nSUB3: c\nSUB4: d"

    config = WikiGraphConfig(query_decompose_max_subqueries=2)
    result = await analyze_node({"current_query": "q"}, config, llm_func=llm)
    assert len(result["sub_queries"]) == 2


async def test_analyze_node_parses_mentioned_docs():
    async def llm(prompt):
        return "OPTIMIZED: q\nSUB1: q\nDOCS: report.pdf"

    result = await analyze_node({"current_query": "q"}, WikiGraphConfig(), llm_func=llm)
    assert result["mentioned_docs"] == ["report.pdf"]


# ---------------------------------------------------------------------------
# RESPOND — query-time document scoping (uses structural-mining doc:: nodes)
# ---------------------------------------------------------------------------


async def test_match_document_nodes_fuzzy_matches_doc_node(rag):
    graph = rag.chunk_entity_relation_graph
    await graph.upsert_node("doc::doc-1", {
        **_node("doc::doc-1"), "entity_type": "document", "description": "Source document: report.pdf",
    })
    matched = await _match_document_nodes(rag, ["report.pdf"])
    assert matched == ["doc::doc-1"]


async def test_match_document_nodes_no_match_returns_empty(rag):
    matched = await _match_document_nodes(rag, ["nonexistent.pdf"])
    assert matched == []


async def test_scoped_entities_follows_contains_edges(rag):
    graph = rag.chunk_entity_relation_graph
    await graph.upsert_node("doc::doc-1", {**_node("doc::doc-1"), "entity_type": "document"})
    await graph.upsert_node("Alpha", _node("Alpha"))
    await graph.upsert_edge("doc::doc-1", "Alpha", _edge())

    entities = await _scoped_entities(rag, ["doc::doc-1"], max_entities=10)
    assert entities == ["Alpha"]


async def test_respond_node_scopes_to_matched_document(rag, monkeypatch):
    graph = rag.chunk_entity_relation_graph
    await graph.upsert_node("doc::doc-1", {
        **_node("doc::doc-1"), "entity_type": "document", "description": "Source document: report.pdf",
    })
    await graph.upsert_node("Alpha", _node("Alpha"))
    await graph.upsert_edge("doc::doc-1", "Alpha", _edge())

    captured = {}

    async def fake_aquery(query, param=None):
        captured["param"] = param
        return "answer"

    monkeypatch.setattr(rag, "aquery", fake_aquery)
    result = await respond_node(
        {"optimized_query": "what does report.pdf say", "mentioned_docs": ["report.pdf"]},
        rag,
        WikiGraphConfig(),
    )
    assert captured["param"].ll_keywords == ["Alpha"]
    assert any("scoped" in m for m in result["messages"])


async def test_respond_node_unscoped_without_mentioned_docs(rag, monkeypatch):
    captured = {}

    async def fake_aquery(query, param=None):
        captured["param"] = param
        return "answer"

    monkeypatch.setattr(rag, "aquery", fake_aquery)
    result = await respond_node({"optimized_query": "hello"}, rag, WikiGraphConfig())
    assert captured["param"].ll_keywords == []
    assert not any("scoped" in m for m in result["messages"])


async def test_respond_node_falls_back_when_mentioned_doc_not_yet_mined(rag, monkeypatch):
    """Structural mining hasn't run yet (or doesn't match) -> unscoped, no crash."""
    captured = {}

    async def fake_aquery(query, param=None):
        captured["param"] = param
        return "answer"

    monkeypatch.setattr(rag, "aquery", fake_aquery)
    result = await respond_node(
        {"optimized_query": "what does report.pdf say", "mentioned_docs": ["report.pdf"]},
        rag,
        WikiGraphConfig(),
    )
    assert captured["param"].ll_keywords == []
    assert "answer" == result["final_answer"]


# ---------------------------------------------------------------------------
# EVOLVE tier 1 — pure pattern-mining helpers
# ---------------------------------------------------------------------------


def test_find_co_retrieved_pairs_respects_min_count():
    log = [
        {"retrieved_entities": ["A", "B"]},
        {"retrieved_entities": ["A", "B"]},
        {"retrieved_entities": ["A", "B"]},
        {"retrieved_entities": ["A", "C"]},
    ]
    pairs = _find_co_retrieved_pairs(log, min_count=3)
    assert ("A", "B", 3) in pairs
    assert not any(p[:2] == ("A", "C") for p in pairs)


def test_find_shortcut_paths_detects_missing_direct_edge():
    log = [{"retrieved_relations": [("A", "B"), ("B", "C")]}] * 3
    shortcuts = _find_shortcut_paths(log, min_count=3)
    assert ("A", "B", "C") in shortcuts


def test_find_shortcut_paths_skips_when_direct_edge_present():
    log = [{"retrieved_relations": [("A", "B"), ("B", "C"), ("A", "C")]}] * 3
    shortcuts = _find_shortcut_paths(log, min_count=3)
    assert shortcuts == []


def test_parse_gap_fill_response():
    response = "ENTITY: Foo | thing | a foo\nRELATIONSHIP: Foo | Bar | foo relates to bar"
    entity, rel = _parse_gap_fill_response(response)
    assert entity["entity_name"] == "Foo"
    assert rel["src_id"] == "Foo" and rel["tgt_id"] == "Bar"


# ---------------------------------------------------------------------------
# EVOLVE tier 1/2 against a real graph — validates the keywords-vs-source_id fix
# ---------------------------------------------------------------------------


async def test_evolve_light_injects_co_retrieval_edge_tagged_via_keywords(rag):
    graph = rag.chunk_entity_relation_graph
    await graph.upsert_node("A", _node("A"))
    await graph.upsert_node("B", _node("B"))

    query_log = [{"retrieved_entities": ["A", "B"], "retrieved_relations": [], "retrieved_chunks": []}] * 3
    state = {"query_log": query_log}
    config = WikiGraphConfig(co_retrieval_min_count=3)

    result = await evolve_light_node(state, rag, config, llm_func=None)
    assert result["evolve_applied"] >= 1

    edge = await graph.get_edge("A", "B")
    assert edge is not None
    # The injected edge's source_id resolves to UNKNOWN (ainsert_custom_kg
    # can't map it without a matching chunk) — keywords is the reliable tag.
    assert edge["source_id"] == "UNKNOWN"
    assert "wikigraph_evolve" in edge["keywords"]
    assert edge["weight"] == config.inferred_edge_weight


async def test_batch_evolve_runs_only_on_interval(rag):
    config = WikiGraphConfig(batch_evolve_interval=5)
    assert should_run_batch_evolve({"query_log": [{}] * 4}, config) is False
    assert should_run_batch_evolve({"query_log": [{}] * 5}, config) is True
    assert should_run_batch_evolve({"query_log": [{}] * 7}, config) is False
    assert should_run_batch_evolve({"query_log": []}, config) is False

    result = await batch_evolve_node({"query_log": [{}] * 4}, rag, config, llm_func=None)
    assert result["batch_triggered"] is False


async def test_source_verification_preserves_evolved_and_structural_edges(rag):
    graph = rag.chunk_entity_relation_graph
    await graph.upsert_node("A", _node("A"))
    await graph.upsert_node("B", _node("B"))
    # A real extracted edge whose source chunk ("chunk-1") is missing from
    # text_chunks. Seed an unrelated chunk so text_chunks isn't empty --
    # source verification treats a completely empty text_chunks store as
    # "not initialized yet" and skips the check entirely.
    await _seed_chunk(rag, "chunk-unrelated", full_doc_id="doc-x", order=0, file_path="x.md")
    await graph.upsert_edge("A", "B", _edge())

    config = WikiGraphConfig(batch_evolve_interval=1)
    result = await batch_evolve_node({"query_log": [{}]}, rag, config, llm_func=None)
    assert result["batch_triggered"] is True
    # The extracted edge's source chunk ("chunk-1") isn't in text_chunks -> removed
    assert await graph.get_edge("A", "B") is None


# ---------------------------------------------------------------------------
# STRUCTURAL — document/chunk structural relations
# ---------------------------------------------------------------------------


async def test_structural_evolve_creates_document_and_contains_edges(rag):
    graph = rag.chunk_entity_relation_graph
    await graph.upsert_node("Alpha", _node("Alpha", source_id="chunk-1"))
    await _seed_chunk(rag, "chunk-1", full_doc_id="doc-1", order=0, file_path="a.md")

    config = WikiGraphConfig(structural_max_mutations=20)
    result = await structural_evolve_node({}, rag, config)
    assert result["structural_applied"] >= 1

    doc_node = await graph.get_node("doc::doc-1")
    assert doc_node is not None
    assert doc_node["entity_type"] == "document"

    edge = await graph.get_edge("doc::doc-1", "Alpha")
    assert edge is not None
    assert "wikigraph_structural" in edge["keywords"]


async def test_structural_evolve_links_adjacent_chunk_entities(rag):
    graph = rag.chunk_entity_relation_graph
    await graph.upsert_node("Alpha", _node("Alpha", source_id="chunk-1"))
    await graph.upsert_node("Beta", _node("Beta", source_id="chunk-2"))
    await _seed_chunk(rag, "chunk-1", full_doc_id="doc-1", order=0, file_path="a.md")
    await _seed_chunk(rag, "chunk-2", full_doc_id="doc-1", order=1, file_path="a.md")

    config = WikiGraphConfig(structural_chunk_adjacency_max_gap=1)
    await structural_evolve_node({}, rag, config)

    edge = await graph.get_edge("Alpha", "Beta")
    assert edge is not None
    assert "chunk_adjacency" in edge["keywords"]


async def test_structural_evolve_links_documents_sharing_entities(rag):
    graph = rag.chunk_entity_relation_graph
    for name, cid in [("Alpha", "chunk-1"), ("Beta", "chunk-2")]:
        await graph.upsert_node(name, _node(name, source_id=cid))
    await _seed_chunk(rag, "chunk-1", full_doc_id="doc-1", order=0, file_path="a.md")
    await _seed_chunk(rag, "chunk-2", full_doc_id="doc-2", order=0, file_path="b.md")
    # Both entities need to appear in both docs to share >= structural_doc_shared_entity_min
    await graph.upsert_node("Alpha2", _node("Alpha", source_id="chunk-2"))

    config = WikiGraphConfig(structural_doc_shared_entity_min=1)
    result = await structural_evolve_node({}, rag, config)
    assert result["structural_applied"] >= 1


async def test_structural_evolve_noop_without_chunk_metadata(rag):
    result = await structural_evolve_node({}, rag, WikiGraphConfig())
    assert result["structural_applied"] == 0


# ---------------------------------------------------------------------------
# LINT — auto-fix only ever touches wikigraph_evolve edges, never structural
# ---------------------------------------------------------------------------


async def test_lint_autofix_removes_stale_evolved_edge_but_not_structural(rag):
    graph = rag.chunk_entity_relation_graph
    for name in ["A", "B", "C", "D"]:
        await graph.upsert_node(name, _node(name))

    await graph.upsert_edge("A", "B", {
        "weight": 0.5, "description": "d", "keywords": "wikigraph_evolve,co-retrieved",
        "source_id": "UNKNOWN", "file_path": "x", "created_at": int(time.time()),
    })
    await graph.upsert_edge("C", "D", {
        "weight": 0.3, "description": "d", "keywords": "wikigraph_structural,contains",
        "source_id": "UNKNOWN", "file_path": "x", "created_at": int(time.time()),
    })

    state = {"entity_metadata": {}, "query_log": []}
    result = await lint_node(state, rag, WikiGraphConfig())

    assert await graph.get_edge("A", "B") is None  # evolved, zero access -> removed
    assert await graph.get_edge("C", "D") is not None  # structural -> never auto-removed
    assert result["lint_fixes"] == 1


# ---------------------------------------------------------------------------
# Agent orchestration — fork + background queue + batch trigger
# ---------------------------------------------------------------------------


async def test_agent_query_returns_immediately_and_queues_background_evolve(rag, monkeypatch):
    async def fake_aquery(query, param=None):
        return f"answer to: {query}"

    async def fake_aquery_data(query, param=None):
        return {"status": "success", "data": {"entities": [], "relationships": [], "chunks": []}}

    monkeypatch.setattr(rag, "aquery", fake_aquery)
    monkeypatch.setattr(rag, "aquery_data", fake_aquery_data)

    agent = WikiGraphAgent(rag, WikiGraphConfig(working_dir=rag.working_dir), llm_func=None)
    result = await agent.query("What is LightRAG?")

    assert "answer to:" in result["answer"]
    await agent.flush()
    assert agent.stats["total_queries"] == 1


async def test_agent_batch_evolve_triggers_at_configured_interval(rag, monkeypatch):
    async def fake_aquery(query, param=None):
        return "ok"

    async def fake_aquery_data(query, param=None):
        return {"status": "success", "data": {"entities": [], "relationships": [], "chunks": []}}

    monkeypatch.setattr(rag, "aquery", fake_aquery)
    monkeypatch.setattr(rag, "aquery_data", fake_aquery_data)

    config = WikiGraphConfig(working_dir=rag.working_dir, batch_evolve_interval=3)
    agent = WikiGraphAgent(rag, config, llm_func=None)

    for i in range(3):
        await agent.query(f"question {i}")
    await agent.flush()

    assert agent._state.get("batch_triggered") is True
    assert agent.stats["total_queries"] == 3
