"""WikiGraph Agent — LangGraph state machine wrapping LightRAG.

Flow per query, matching the two-path design in README.md:

  1. ANALYZE (inline)   — rewrite + decompose the query (query improvement)
  2. fork:
     a. RESPOND (foreground, awaited)     — answer the user immediately,
        using the query ANALYZE just optimized.
     b. EVOLVE pipeline (background, queued, not awaited) —
        RETRIEVE -> EVALUATE -> EVOLVE(light) -> [every N queries]
        EVOLVE(batch) + STRUCTURAL. Its effect on the graph lands before the
        *next* query, never blocking this one's answer.

Only step 2b is modeled as a LangGraph StateGraph — it is the one genuinely
multi-step, stateful pipeline. ANALYZE and RESPOND are single calls, so
they're invoked directly.

The background pipeline is processed by a single dedicated worker coroutine
consuming a FIFO queue, one job at a time. This keeps ``self._state`` (the
query log and entity metadata that EVOLVE reads and writes) mutated from
exactly one place, so concurrent queries can never interleave a
read-modify-write on it.
"""

from __future__ import annotations

import asyncio

from langgraph.graph import END, StateGraph

from lightrag import LightRAG
from wikigraph.config import WikiGraphConfig
from wikigraph.metadata import MetadataStore
from wikigraph.operations.analyze import analyze_node
from wikigraph.operations.evolve import batch_evolve_node, evolve_light_node
from wikigraph.operations.ingest import ingest_node
from wikigraph.operations.lint import lint_node
from wikigraph.operations.query import evaluate_node, respond_node, retrieve_node
from wikigraph.operations.structural import structural_evolve_node
from wikigraph.state import EntityMeta, QueryLogEntry, WikiGraphState


def _build_evolve_graph(rag: LightRAG, config: WikiGraphConfig, llm_func=None):
    """Background evolving pipeline: RETRIEVE -> EVALUATE -> EVOLVE -> STRUCTURAL."""

    async def _retrieve(state: WikiGraphState) -> dict:
        return await retrieve_node(state, rag)

    def _evaluate(state: WikiGraphState) -> dict:
        return evaluate_node(state, config)

    async def _evolve_light(state: WikiGraphState) -> dict:
        return await evolve_light_node(state, rag, config, llm_func)

    async def _batch_evolve(state: WikiGraphState) -> dict:
        return await batch_evolve_node(state, rag, config, llm_func)

    async def _structural(state: WikiGraphState) -> dict:
        if not state.get("batch_triggered"):
            return {}
        return await structural_evolve_node(state, rag, config)

    graph = StateGraph(WikiGraphState)
    graph.add_node("retrieve", _retrieve)
    graph.add_node("evaluate", _evaluate)
    graph.add_node("evolve_light", _evolve_light)
    graph.add_node("batch_evolve", _batch_evolve)
    graph.add_node("structural", _structural)

    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "evaluate")
    graph.add_edge("evaluate", "evolve_light")
    graph.add_edge("evolve_light", "batch_evolve")
    graph.add_edge("batch_evolve", "structural")
    graph.add_edge("structural", END)

    return graph.compile()


class WikiGraphAgent:
    """Immediate-answer agent whose knowledge graph keeps improving in the background."""

    def __init__(self, rag: LightRAG, config: WikiGraphConfig | None = None, llm_func=None):
        self.rag = rag
        self.config = config or WikiGraphConfig(working_dir=rag.working_dir)
        self.llm_func = llm_func
        self.store = MetadataStore(rag.working_dir, self.config.max_query_log_size)
        self._evolve_graph = _build_evolve_graph(rag, self.config, llm_func)

        self._state: WikiGraphState = {"messages": [], "query_log": [], "entity_metadata": {}}
        self._state_lock = asyncio.Lock()
        self._evolve_queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

        self._load_persistent_state()

    def _load_persistent_state(self) -> None:
        meta = self.store.load_entity_metadata()
        self._state["entity_metadata"] = {k: v.__dict__ for k, v in meta.items()}
        log = self.store.load_query_log()
        self._state["query_log"] = [e.__dict__ for e in log]

    def _save_persistent_state(self) -> None:
        meta = {k: EntityMeta(**v) for k, v in self._state.get("entity_metadata", {}).items()}
        self.store.save_entity_metadata(meta)
        log = [QueryLogEntry(**e) for e in self._state.get("query_log", [])]
        self.store.save_query_log(log)

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._evolve_worker())

    async def _evolve_worker(self) -> None:
        while True:
            job = await self._evolve_queue.get()
            try:
                async with self._state_lock:
                    self._state["current_query"] = job["current_query"]
                    self._state["optimized_query"] = job["optimized_query"]
                    self._state["sub_queries"] = job["sub_queries"]
                    self._state["messages"] = []
                    result = await self._evolve_graph.ainvoke(self._state)
                    self._state.update(result)
                    self._save_persistent_state()
            except Exception as e:
                self._state.setdefault("messages", []).append(f"EVOLVE: background pass failed: {e}")
            finally:
                self._evolve_queue.task_done()

    async def ingest(self, documents: list[str], file_paths: list[str] | None = None) -> dict:
        """Ingest documents into the knowledge graph."""
        result = await ingest_node({"documents": documents, "file_paths": file_paths or []}, self.rag)
        return {"messages": result.get("messages", []), "track_id": result.get("last_track_id")}

    async def query(self, query: str) -> dict:
        """ANALYZE, then fork: RESPOND is awaited and returned to the caller;
        the background EVOLVE pipeline is queued and NOT awaited.
        """
        analysis = await analyze_node({"current_query": query}, self.config, self.llm_func)
        optimized_query = analysis.get("optimized_query", query)
        sub_queries = analysis.get("sub_queries", [query])

        answer_result = await respond_node({"optimized_query": optimized_query}, self.rag)

        self._ensure_worker()
        await self._evolve_queue.put({
            "current_query": query,
            "optimized_query": optimized_query,
            "sub_queries": sub_queries,
        })

        return {
            "answer": answer_result.get("final_answer", ""),
            "messages": analysis.get("messages", []) + answer_result.get("messages", []),
        }

    async def flush(self) -> None:
        """Wait for all queued background evolve passes to finish (tests/shutdown)."""
        if self._worker_task is not None:
            await self._evolve_queue.join()

    async def evolve(self) -> dict:
        """Manually run the full background evolve pipeline and wait for it."""
        async with self._state_lock:
            result = await self._evolve_graph.ainvoke(self._state)
            self._state.update(result)
            self._save_persistent_state()
        return {
            "messages": result.get("messages", []),
            "evolve_applied": result.get("evolve_applied", 0),
            "batch_triggered": result.get("batch_triggered", False),
            "batch_evolve_applied": result.get("batch_evolve_applied", 0),
            "structural_applied": result.get("structural_applied", 0),
        }

    async def lint(self) -> dict:
        """Run graph health checks (read-only except for the auto-fix pass)."""
        async with self._state_lock:
            result = await lint_node(self._state, self.rag, self.config)
        return {
            "messages": result.get("messages", []),
            "findings": result.get("lint_findings", []),
        }

    async def watch(self, directory: str) -> dict:
        """Scan a directory for new/changed files and auto-ingest them."""
        import os

        from wikigraph.sources import detect_changed_files

        hash_path = os.path.join(self.rag.working_dir, "wikigraph_meta", "file_hashes.json")
        changed = detect_changed_files(directory, hash_path)
        if not changed:
            return {"messages": ["WATCH: no changes detected"], "ingested": 0}

        docs = [text for _, text in changed]
        paths = [name for name, _ in changed]
        result = await self.ingest(docs, file_paths=paths)
        result["messages"] = [f"WATCH: {len(changed)} file(s) changed"] + result["messages"]
        result["ingested"] = len(changed)
        return result

    async def ingest_artifact(self, text: str, label: str = "agent_artifact") -> dict:
        """Ingest agent-generated knowledge (answers/summaries) back into the graph."""
        return await self.ingest([text], file_paths=[label])

    @property
    def stats(self) -> dict:
        return {
            "total_queries": len(self._state.get("query_log", [])),
            "tracked_entities": len(self._state.get("entity_metadata", {})),
            "pending_evolve_jobs": self._evolve_queue.qsize(),
            "messages": self._state.get("messages", [])[-5:],
        }
