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
    """
    High-level Agentic RAG wrapper around LightRAG.

    Architecture:
      - Base retrieval: LightRAG mix mode (entity VDB + relation VDB + BM25)
      - Runtime learning: QSM + DRG accumulate query-retrieval patterns
      - Background KG improvement: batch_evolve() fires every evolve_every queries
      - Progressive effect: later queries benefit from accumulated patterns

    Start from golden LightRAG. As queries flow in, the agent observes and
    improves. No pre-processing or ground truth required.
    """

    def __init__(
        self,
        rag: LightRAG,
        config: WikiGraphConfig | None = None,
        llm_func=None,
        evolve_every: int = 50,
        fast_mode: bool = True,
    ):
        self.rag = rag
        self.config = config or WikiGraphConfig(working_dir=rag.working_dir)
        self.llm_func = llm_func
        self.store = MetadataStore(rag.working_dir, self.config.max_query_log_size)
        # fast_mode: use hybrid retrieval (no LLM per query)
        rag._fast_mode = fast_mode
        self._graph = _build_graph(rag, self.config, llm_func)
        self._state: WikiGraphState = {
            "operation": "done",
            "messages": [],
            "query_log": [],
            "entity_metadata": {},
        }
        self._load_persistent_state()

        # Agentic query state
        self.evolve_every = evolve_every          # trigger EVOLVE every N queries
        self._agentic_query_buffer: list[dict] = []  # recent query log
        self._evolve_count = 0                    # how many evolves have run
        self._qsm = None                          # set via attach_memories()
        self._drg = None                          # set via attach_memories()

    def attach_memories(self, qsm, drg):
        """Attach QSM and DRG to the agent for agentic_query learning."""
        self._qsm = qsm
        self._drg = drg

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
        self._state["messages"] = []
        result = await self._graph.ainvoke(self._state)
        self._state.update(result)
        self._save_persistent_state()
        return {"messages": result.get("messages", []), "track_id": result.get("last_track_id")}

    async def query(self, query: str) -> dict:
        """Query with automatic evaluation and optional evolution.

        EVOLVE triggers when:
        1. Result quality is below threshold (reactive), OR
        2. Every auto_evolve_interval queries (proactive background improvement)
        """
        self._state["operation"] = "query"
        self._state["current_query"] = query
        self._state["should_evolve"] = False
        self._state["messages"] = []

        # Proactive: force evolve every N queries
        total = len(self._state.get("query_log", [])) + 1
        if total % self.config.auto_evolve_interval == 0:
            self._state["should_evolve"] = True

        result = await self._graph.ainvoke(self._state)
        self._state.update(result)
        self._save_persistent_state()
        new_messages = result.get("messages", [])
        return {
            "messages": new_messages,
            "result": result.get("query_result"),
            "evolved": result.get("evolve_applied", 0) > 0,
        }

    async def agentic_query(
        self,
        query: str,
        top_k: int = 20,
        qsm_min_count: int = 2,
        drg_min_weight: float = 0.15,
        drg_supplement_k: int = 5,
        online_dcsg_threshold: int = 3,
        evolve_co_occur_min: int = 1,
    ) -> dict:
        """
        Query with automatic pattern learning and background KG improvement.

        Pipeline:
          1. QSM scope restriction: prepend known-relevant DOC: hints to query
          2. LightRAG mix-mode retrieval (entity VDB + relation VDB + BM25)
          3. DRG supplement: add chunks from structurally related docs
          4. Update QSM + DRG with retrieval results
          5. If N queries accumulated → fire EVOLVE (blocking, then continue)

        The graph improves on its own as queries flow in.
        No ground truth or manual intervention needed.

        Args:
          query: user query text
          top_k: retrieval window size
          qsm_min_count: min QSM pattern count to activate scope restriction
          drg_min_weight: min DRG edge weight to use for supplement
          drg_supplement_k: max chunks to supplement per related doc
          online_dcsg_threshold: DRG edge strength before live KG injection
          evolve_co_occur_min: min co-occurrence count for EVOLVE edge creation

        Returns:
          {"chunks": list, "qsm_scope": list, "drg_supplement": int, "evolve_fired": bool}
        """
        from wikigraph.adaptive_retriever import agentic_retrieve

        if self._qsm is None or self._drg is None:
            raise RuntimeError(
                "Call attach_memories(qsm, drg) before agentic_query()"
            )

        # Retrieve with QSM scope + DRG supplement + learn
        chunks = await agentic_retrieve(
            self.rag, query,
            self._qsm, self._drg,
            top_k=top_k,
            qsm_min_count=qsm_min_count,
            drg_min_weight=drg_min_weight,
            drg_supplement_k=drg_supplement_k,
            online_dcsg_threshold=online_dcsg_threshold,
            update_qsm=True,
            update_drg=True,
        )

        # Buffer query for EVOLVE analysis
        self._agentic_query_buffer.append({"question": query})

        evolve_fired = False
        evolve_result = None

        # Trigger EVOLVE when buffer hits threshold
        if self.evolve_every > 0 and len(self._agentic_query_buffer) % self.evolve_every == 0:
            print(
                f"  [agentic] {len(self._agentic_query_buffer)} queries accumulated "
                f"→ firing EVOLVE (DA+DCSG+CDRB)..."
            )
            recent = self._agentic_query_buffer[-self.evolve_every:]
            try:
                evolve_result = await self.batch_evolve(
                    recent,
                    evolve_k=50,
                    co_occur_min=evolve_co_occur_min,
                    max_edges=500,
                    qsm=self._qsm,
                    drg=self._drg,
                )
                self._evolve_count += 1
                evolve_fired = True
                print(
                    f"  [agentic] EVOLVE #{self._evolve_count} complete: "
                    f"DA={evolve_result['da_updates']} "
                    f"DCSG={evolve_result['dcsg_edges']} "
                    f"CDRB={evolve_result['cdrb_edges']}"
                )
            except Exception as e:
                print(f"  [agentic] EVOLVE error: {e}")

        drg_supplement_count = sum(1 for c in chunks if c.get("_drg_supplement"))
        return {
            "chunks": chunks,
            "drg_supplement": drg_supplement_count,
            "evolve_fired": evolve_fired,
            "evolve_count": self._evolve_count,
            "queries_seen": len(self._agentic_query_buffer),
        }

    async def evolve(self) -> dict:
        """Manually trigger knowledge evolution."""
        self._state["operation"] = "evolve"
        self._state["messages"] = []
        result = await self._graph.ainvoke(self._state)
        self._state.update(result)
        self._save_persistent_state()
        return {
            "messages": result.get("messages", []),
            "mutations": result.get("evolve_mutations", []),
            "applied": result.get("evolve_applied", 0),
        }

    async def batch_evolve(
        self,
        questions: list[dict],
        evolve_k: int = 50,
        co_occur_min: int = 2,
        max_edges: int = 500,
        qsm=None,
        drg=None,
    ) -> dict:
        """
        Agentic batch EVOLVE: multi-layer knowledge graph improvement.

        Observes ONLY:
          - query text (questions)
          - retrieval results (what system returns for each query)

        No ground truth. No document labels. No expected answers.

        Four complementary strategies run in sequence:

        Strategy 8 — Document Anchoring (DA):
          Creates DOC:filename entities for every document.
          Inserted into entity VDB → queries matching document names retrieve
          ALL doc chunks directly, independent of extracted entity coverage.
          Force-refreshes old-format descriptions (removes content-excerpt noise).

        Strategy 9 — Document Co-Structure Graph (DCSG):
          Builds DOC-to-DOC edges from query-retrieval co-occurrence patterns.
          Enables: entity VDB → DOC:A → [CO_RETRIEVED_WITH] → DOC:B → B's chunks.
          Document-level structural traversal in the KG.

        Strategy 6 — Cross-Document Retrieval Bridge (CDRB):
          Entity-level complement: specific entity-pair cross-document connections.

        DRG (Document Relation Graph) bootstrap:
          If DRG provided, imports DCSG edges as high-confidence DRG edges.
          This seeds the DRG for immediate use in agentic_retrieve supplement.
          Also indexes document chunks for fast supplement lookup.

        Args:
          questions:    list of {"question": str, ...} (no expected answers)
          evolve_k:     retrieval window for pattern mining (wider than service k)
          co_occur_min: minimum co-occurrence count to qualify as edge
          max_edges:    total KG edge budget (split between DCSG and CDRB)
          qsm:          QueryStructuralMemory (optional, for pattern import)
          drg:          DocRelationGraph (optional, bootstrapped from DCSG results)

        Returns:
          {"edges_added": int, "da_updates": int, "dcsg_edges": int,
           "cdrb_edges": int, "drg_edges": int, "messages": list[str]}
        """
        from wikigraph.operations.evolve import (
            da_strategy, dcsg_strategy, dcsg_from_drg, cdrb_strategy
        )

        all_messages: list[str] = []

        # Strategy 8: DA — create/refresh DOC entities for all documents
        da_updates, da_msgs = await da_strategy(self.rag)
        all_messages.extend(da_msgs)

        # Split edge budget: DCSG for doc-level, CDRB for entity-level
        dcsg_budget = max_edges // 2
        cdrb_budget = max_edges - dcsg_budget

        # Strategy 9: DCSG — fast path from DRG data if available, else query-based
        if drg is not None and drg._data.get("edges"):
            # Fast: derive structural edges from DRG co-retrieval weights (no queries)
            drg_weight_min = co_occur_min * 0.1  # co_occur_min=1 → weight≥0.1
            dcsg_edges, dcsg_msgs = await dcsg_from_drg(
                self.rag, drg,
                co_occur_min=drg_weight_min,
                max_edges=dcsg_budget,
            )
        else:
            # Fallback: mine co-retrieval from query runs (slower, needs LLM)
            dcsg_edges, dcsg_msgs = await dcsg_strategy(
                self.rag, questions,
                evolve_k=evolve_k,
                co_occur_min=co_occur_min,
                max_edges=dcsg_budget,
            )
        all_messages.extend(dcsg_msgs)

        # Strategy 6: CDRB — entity-level cross-document edges
        cdrb_edges, cdrb_msgs = await cdrb_strategy(
            self.rag, questions,
            evolve_k=evolve_k,
            co_occur_min=co_occur_min,
            max_edges=cdrb_budget,
        )
        all_messages.extend(cdrb_msgs)

        # DRG: only refresh chunk index (no edge bootstrap — edges grow from real queries)
        drg_edges = 0
        if drg is not None:
            if not drg.is_indexed():
                drg.index_doc_chunks(self.rag)
                all_messages.append(
                    f"DRG: chunk index built ({len(drg._data['doc_index'])} docs)"
                )
            else:
                # Re-index to pick up any new DA doc entities after this EVOLVE
                drg.index_doc_chunks(self.rag)
            drg.save()
            s = drg.stats
            all_messages.append(
                f"DRG: edges={s['total_edges']} strong(≥0.5)={s['edges_w05']} "
                f"indexed_docs={s['docs_indexed']} "
                f"[edges from real queries only — no bootstrap]"
            )

        return {
            "edges_added": dcsg_edges + cdrb_edges,
            "da_updates": da_updates,
            "dcsg_edges": dcsg_edges,
            "cdrb_edges": cdrb_edges,
            "drg_edges": drg_edges,
            "messages": all_messages,
        }

    async def lint(self) -> dict:
        """Run graph health checks."""
        self._state["operation"] = "lint"
        self._state["messages"] = []
        result = await self._graph.ainvoke(self._state)
        self._state.update(result)
        return {
            "messages": result.get("messages", []),
            "findings": result.get("lint_findings", []),
        }

    async def watch(self, directory: str, interval: float = 0) -> dict:
        """Scan directory for new/changed files and auto-ingest them.

        Uses content hash to detect changes. Only modified files are processed.
        Call periodically or once after adding files to the watched directory.
        """
        from wikigraph.sources import detect_changed_files
        import os

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
        """Ingest agent-generated knowledge artifact into the graph.

        Use this to feed back high-quality query answers, summaries, or
        analysis results into the RAG so they become searchable.
        """
        return await self.ingest([text], file_paths=[label])

    @property
    def stats(self) -> dict:
        return {
            "total_queries": len(self._state.get("query_log", [])),
            "tracked_entities": len(self._state.get("entity_metadata", {})),
            "messages": self._state.get("messages", [])[-5:],
        }
