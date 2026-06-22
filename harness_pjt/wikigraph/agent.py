"""WikiGraph Agent — LangGraph state machine wrapping LightRAG."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from lightrag import LightRAG
from wikigraph.config import WikiGraphConfig
from wikigraph.metadata import MetadataStore
from wikigraph.operations.evolve import evolve_node
from wikigraph.operations.ingest import ingest_node
from wikigraph.operations.lint import lint_node
from wikigraph.operations.query import evaluate_node, query_node
from wikigraph.state import WikiGraphState


def _build_graph(rag: LightRAG, config: WikiGraphConfig, llm_func=None):
    """Build the LangGraph StateGraph."""

    async def _ingest(state: WikiGraphState) -> dict:
        return await ingest_node(state, rag)

    async def _query(state: WikiGraphState) -> dict:
        return await query_node(state, rag)

    def _evaluate(state: WikiGraphState) -> dict:
        return evaluate_node(state, config)

    async def _evolve(state: WikiGraphState) -> dict:
        return await evolve_node(state, rag, config, llm_func)

    async def _lint(state: WikiGraphState) -> dict:
        return await lint_node(state, rag, config)

    def _route(state: WikiGraphState) -> str:
        return state.get("operation", "done")

    def _should_evolve(state: WikiGraphState) -> str:
        return "evolve" if state.get("should_evolve") else "done"

    graph = StateGraph(WikiGraphState)
    graph.add_node("router", lambda s: s)
    graph.add_node("ingest", _ingest)
    graph.add_node("query", _query)
    graph.add_node("evaluate", _evaluate)
    graph.add_node("evolve", _evolve)
    graph.add_node("lint", _lint)

    graph.set_entry_point("router")
    graph.add_conditional_edges("router", _route, {
        "ingest": "ingest",
        "query": "query",
        "evolve": "evolve",
        "lint": "lint",
        "done": END,
    })
    graph.add_edge("ingest", END)
    graph.add_edge("query", "evaluate")
    graph.add_conditional_edges("evaluate", _should_evolve, {
        "evolve": "evolve",
        "done": END,
    })
    graph.add_edge("evolve", END)
    graph.add_edge("lint", END)

    return graph.compile()


class WikiGraphAgent:
    """High-level convenience wrapper around the LangGraph agent."""

    def __init__(self, rag: LightRAG, config: WikiGraphConfig | None = None, llm_func=None):
        self.rag = rag
        self.config = config or WikiGraphConfig(working_dir=rag.working_dir)
        self.llm_func = llm_func
        self.store = MetadataStore(rag.working_dir, self.config.max_query_log_size)
        self._graph = _build_graph(rag, self.config, llm_func)
        self._state: WikiGraphState = {
            "operation": "done",
            "messages": [],
            "query_log": [],
            "entity_metadata": {},
        }
        self._load_persistent_state()

    def _load_persistent_state(self):
        meta = self.store.load_entity_metadata()
        self._state["entity_metadata"] = {k: v.__dict__ for k, v in meta.items()}
        log = self.store.load_query_log()
        self._state["query_log"] = [e.__dict__ for e in log]

    def _save_persistent_state(self):
        from wikigraph.state import EntityMeta, QueryLogEntry
        meta = {k: EntityMeta(**v) for k, v in self._state.get("entity_metadata", {}).items()}
        self.store.save_entity_metadata(meta)
        log = [QueryLogEntry(**e) for e in self._state.get("query_log", [])]
        self.store.save_query_log(log)

    async def ingest(self, documents: list[str], file_paths: list[str] | None = None) -> dict:
        """Ingest documents into the knowledge graph."""
        self._state["operation"] = "ingest"
        self._state["documents"] = documents
        self._state["file_paths"] = file_paths or []
        result = await self._graph.ainvoke(self._state)
        self._state.update(result)
        self._save_persistent_state()
        return {"messages": result.get("messages", []), "track_id": result.get("last_track_id")}

    async def query(self, query: str) -> dict:
        """Query with automatic evaluation and optional evolution."""
        self._state["operation"] = "query"
        self._state["current_query"] = query
        self._state["should_evolve"] = False
        result = await self._graph.ainvoke(self._state)
        self._state.update(result)
        self._save_persistent_state()
        return {
            "messages": result.get("messages", []),
            "result": result.get("query_result"),
            "evolved": result.get("evolve_applied", 0) > 0,
        }

    async def evolve(self) -> dict:
        """Manually trigger knowledge evolution."""
        self._state["operation"] = "evolve"
        result = await self._graph.ainvoke(self._state)
        self._state.update(result)
        self._save_persistent_state()
        return {
            "messages": result.get("messages", []),
            "mutations": result.get("evolve_mutations", []),
            "applied": result.get("evolve_applied", 0),
        }

    async def lint(self) -> dict:
        """Run graph health checks."""
        self._state["operation"] = "lint"
        result = await self._graph.ainvoke(self._state)
        self._state.update(result)
        return {
            "messages": result.get("messages", []),
            "findings": result.get("lint_findings", []),
        }

    @property
    def stats(self) -> dict:
        return {
            "total_queries": len(self._state.get("query_log", [])),
            "tracked_entities": len(self._state.get("entity_metadata", {})),
            "messages": self._state.get("messages", [])[-5:],
        }
