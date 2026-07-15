"""
Document Relation Graph (DRG) — Auxiliary Structure for Agentic RAG
====================================================================

An in-memory + persistent auxiliary graph that records document-level
structural relationships learned purely from query-retrieval patterns.

Key design:
  - SEPARATE from the main LightRAG KG — never modifies core graph structures
  - Accumulates edge weights with each co-retrieval observation
  - Used at query time to supplement retrieval with structurally related docs
  - "Gets better with use" — edge confidence grows as more queries are processed

Mechanism:
  Query → mix_mode retrieval → retrieved docs {A, B, C}
  DRG.update({A, B, C}) → edges A↔B, A↔C, B↔C weight += step
  DRG.get_related({A, B}) → returns {C, D, ...} (above threshold, not in source)
  supplement: fetch N chunks from each related doc → append to results

  Next similar query:
    DRG.get_related returns docs based on accumulated evidence
    → hub/linked documents consistently discovered even if they miss entity VDB

This directly improves co-retrieval (AND logic):
  Before DRG: card found in entity VDB, hub NOT found → co_hit FAIL
  After N queries with DRG: card↔hub edge strong → hub supplemented → co_hit PASS

No ground truth required. No data-type assumptions.
Works with any corpus structure.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lightrag import LightRAG

# Files that are clearly non-corpus artifacts — excluded from DRG updates
_ARTIFACT_BASENAMES: frozenset[str] = frozenset({
    "golden_code_plan.md",
    "unknown_source",
    "",
})

# Minimum filename length — very short basenames are likely artifacts
_MIN_FILENAME_LEN = 5


def _is_corpus_file(basename: str) -> bool:
    """Return True if basename looks like a real corpus document (not artifact)."""
    if not basename or len(basename) < _MIN_FILENAME_LEN:
        return False
    if basename in _ARTIFACT_BASENAMES:
        return False
    # Exclude internal wikigraph virtual files
    if basename.startswith("wikigraph_") or basename.startswith("online_dcsg"):
        return False
    return True


class DocRelationGraph:
    """
    Auxiliary document relation graph for Agentic RAG.

    Stores:
      edges       : {doc_a||doc_b → {weight, count, last_ts}}
      doc_index   : {doc_basename → [chunk_id, ...]}  (cached chunk lookup)
      query_log   : {fingerprint → {docs, count}}     (raw retrieval log)

    Edge weight semantics:
      0.0 → never co-retrieved (no edge)
      0.1 → co-retrieved once
      0.5 → strong signal (≥5 co-retrievals)
      1.0 → maximum confidence

    Weight update rule:
      w_new = min(1.0, w_old + step)
      step defaults to 0.1 so 10 co-occurrences → weight ≈ 1.0
    """

    WEIGHT_STEP = 0.1
    WEIGHT_MAX = 1.0

    def __init__(self, working_dir: str):
        meta_dir = os.path.join(working_dir, "wikigraph_meta")
        os.makedirs(meta_dir, exist_ok=True)
        self._path = os.path.join(meta_dir, "drg.json")
        self._data = self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "edges": {},       # "doc_a||doc_b": {"weight": float, "count": int, "ts": float}
            "doc_index": {},   # "doc_basename": [chunk_id, ...]
        }

    def save(self):
        """Persist DRG to disk."""
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    # ── Edge management ────────────────────────────────────────────────────────

    def update(self, retrieved_docs: list[str] | set[str]):
        """
        Record a co-retrieval observation. All pairs of retrieved docs have
        their edge weights incremented.

        Args:
          retrieved_docs: basenames of documents retrieved for a single query.
                          Non-corpus files (artifacts) are filtered out.
        """
        corpus_docs = sorted(d for d in retrieved_docs if _is_corpus_file(d))
        if len(corpus_docs) < 2:
            return

        edges = self._data["edges"]
        ts = time.time()

        for i in range(len(corpus_docs)):
            for j in range(i + 1, len(corpus_docs)):
                key = f"{corpus_docs[i]}||{corpus_docs[j]}"
                if key not in edges:
                    edges[key] = {"weight": 0.0, "count": 0, "ts": ts}
                entry = edges[key]
                entry["count"] += 1
                entry["weight"] = min(
                    self.WEIGHT_MAX,
                    entry["weight"] + self.WEIGHT_STEP,
                )
                entry["ts"] = ts

    def get_related_docs(
        self,
        source_docs: set[str],
        top_n: int = 5,
        min_weight: float = 0.3,
    ) -> list[tuple[str, float]]:
        """
        Given a set of already-retrieved docs, return related docs not in source_docs.

        Uses max-edge aggregation: a related doc's score = max edge weight to any
        source doc. Returned in descending score order.

        Args:
          source_docs: basenames of already-retrieved docs
          top_n: maximum related docs to return
          min_weight: minimum edge weight threshold (avoid noise from count=1)

        Returns:
          [(doc_basename, weight), ...] sorted by weight descending
        """
        related: dict[str, float] = {}
        edges = self._data["edges"]

        for key, data in edges.items():
            w = data.get("weight", 0.0)
            if w < min_weight:
                continue
            parts = key.split("||", 1)
            if len(parts) != 2:
                continue
            a, b = parts
            if a in source_docs and b not in source_docs and _is_corpus_file(b):
                related[b] = max(related.get(b, 0.0), w)
            elif b in source_docs and a not in source_docs and _is_corpus_file(a):
                related[a] = max(related.get(a, 0.0), w)

        ranked = sorted(related.items(), key=lambda x: -x[1])
        return ranked[:top_n]

    # ── Document chunk index ──────────────────────────────────────────────────

    def index_doc_chunks(self, rag: "LightRAG", max_chunks_per_doc: int = 15):
        """
        Pre-index top chunks per document for fast supplement lookup.

        Iterates rag.text_chunks to build {basename → [chunk_id, ...]} index.
        Should be called once after rag is initialized (or after ingest).
        """
        doc_index: dict[str, list[str]] = defaultdict(list)
        try:
            for cid, cdata in rag.text_chunks._data.items():
                fp = cdata.get("file_path", "")
                if not fp:
                    continue
                basename = os.path.basename(fp)
                if not _is_corpus_file(basename):
                    continue
                if len(doc_index[basename]) < max_chunks_per_doc:
                    doc_index[basename].append(cid)
        except Exception:
            pass

        self._data["doc_index"] = dict(doc_index)

    def get_doc_chunks(self, doc_basename: str) -> list[str]:
        """Return pre-indexed chunk IDs for a document."""
        return self._data["doc_index"].get(doc_basename, [])

    def is_indexed(self) -> bool:
        """True if doc_index has been populated."""
        return bool(self._data.get("doc_index"))

    # ── Stats ─────────────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        edges = self._data["edges"]
        return {
            "total_edges": len(edges),
            "edges_w03":   sum(1 for e in edges.values() if e.get("weight", 0) >= 0.3),
            "edges_w05":   sum(1 for e in edges.values() if e.get("weight", 0) >= 0.5),
            "edges_w08":   sum(1 for e in edges.values() if e.get("weight", 0) >= 0.8),
            "docs_indexed": len(self._data.get("doc_index", {})),
        }

    # ── Batch import from DCSG / QSM ─────────────────────────────────────────

    def import_from_qsm(self, qsm_data: dict, min_count: int = 2):
        """
        Bootstrap DRG edges from QSM co-occurrence data.

        Each QSM pending pair (doc_a, doc_b, count) is translated to a DRG edge
        with weight = min(count * WEIGHT_STEP, WEIGHT_MAX).

        Args:
          qsm_data: QSM._data dict
          min_count: minimum co-occurrence count to import
        """
        cooc = qsm_data.get("cooc_pending", {})
        confirmed = qsm_data.get("cooc_confirmed", {})
        all_pairs = {**cooc, **confirmed}

        imported = 0
        for key, cnt in all_pairs.items():
            if cnt < min_count:
                continue
            parts = key.split("||", 1)
            if len(parts) != 2:
                continue
            a, b = parts
            if not _is_corpus_file(a) or not _is_corpus_file(b):
                continue

            edge_key = f"{min(a, b)}||{max(a, b)}"
            if edge_key not in self._data["edges"]:
                self._data["edges"][edge_key] = {"weight": 0.0, "count": 0, "ts": time.time()}

            existing = self._data["edges"][edge_key]
            # Import as weight based on count (each count adds one step)
            import_weight = min(self.WEIGHT_MAX, cnt * self.WEIGHT_STEP)
            if import_weight > existing["weight"]:
                existing["weight"] = import_weight
                existing["count"] = max(existing["count"], cnt)
                imported += 1

        return imported

    def import_from_dcsg_edges(self, rag: "LightRAG"):
        """
        Import existing DCSG edges from the KG into DRG with high confidence.

        DCSG edges already represent strong co-retrieval patterns (batch-vetted).
        Importing them gives DRG a strong starting point for supplement.

        Edge format in KG: DOC:file_a -[CO_RETRIEVED_WITH]-> DOC:file_b
        """
        import asyncio

        async def _import():
            graph = rag.chunk_entity_relation_graph
            imported = 0
            try:
                labels = await graph.get_all_labels()
                doc_labels = [l for l in labels if str(l).startswith("DOC:")]
                for label in doc_labels:
                    edges = await graph.get_node_edges(label)
                    if not edges:
                        continue
                    a = str(label).removeprefix("DOC:")
                    for src, tgt in edges:
                        if str(src).startswith("DOC:") and str(tgt).startswith("DOC:"):
                            b = str(tgt).removeprefix("DOC:")
                            if not _is_corpus_file(a) or not _is_corpus_file(b):
                                continue
                            edge_key = f"{min(a, b)}||{max(a, b)}"
                            if edge_key not in self._data["edges"]:
                                self._data["edges"][edge_key] = {
                                    "weight": 0.7,  # DCSG edges have strong prior
                                    "count": 5,
                                    "ts": time.time(),
                                }
                                imported += 1
            except Exception:
                pass
            return imported

        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, _import()).result()
        return loop.run_until_complete(_import())
