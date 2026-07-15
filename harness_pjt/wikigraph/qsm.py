"""
Query-Structural Memory (QSM)
==============================
Persistent memory that accumulates query-retrieval patterns over time.

Role in Agentic RAG:
  Each query reveals which documents are jointly relevant.
  QSM records these patterns and uses them to:
    1. Scope future retrieval (pre-retrieval: narrow entity VDB search)
    2. Trigger incremental graph improvement (online DCSG)
    3. Build a routing table (topic → relevant documents)

"Gets better with use":
  - Each query updates QSM with retrieved documents
  - Future similar queries get scoped retrieval (relevant docs prioritized)
  - After enough co-occurrence evidence, DCSG edges are added online
  - Graph evolves continuously, not just in batch EVOLVE steps

No LLM required: purely statistical from (query_text, retrieved_docs) pairs.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict


class QueryStructuralMemory:
    """
    Stores and queries accumulated retrieval patterns.

    Data model:
      query_routing: fingerprint → {"docs": [...], "count": int, "ts": float}
      doc_topic_index: doc → [keyword, ...]
      cooc_pending: "doc_a||doc_b" → count  (pending DCSG edge candidates)
      cooc_confirmed: "doc_a||doc_b" → count  (already added as DCSG edges)
    """

    def __init__(self, working_dir: str):
        meta_dir = os.path.join(working_dir, "wikigraph_meta")
        os.makedirs(meta_dir, exist_ok=True)
        self._path = os.path.join(meta_dir, "qsm.json")
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
            "query_routing": {},
            "doc_topic_index": {},
            "cooc_pending": {},
            "cooc_confirmed": {},
        }

    def save(self):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    # ── Query fingerprinting ───────────────────────────────────────────────────

    @staticmethod
    def fingerprint(query: str) -> str:
        """Normalize query to a stable, comparable fingerprint."""
        words = sorted(set(
            w.lower() for w in re.findall(r"\b\w{3,}\b", query)
            if w.lower() not in {"the", "and", "for", "that", "this", "are", "was",
                                  "what", "how", "why", "when", "where", "which"}
        ))
        return " ".join(words[:12])

    # ── Scope hint retrieval ───────────────────────────────────────────────────

    def get_scope_docs(self, query: str, min_count: int = 2) -> list[str]:
        """
        Return documents known to be relevant for this query pattern.

        Uses:
          1. Exact fingerprint match in query_routing
          2. Partial keyword overlap (≥3 shared terms)
          3. doc_topic_index keyword match

        Returns: list of basenames (e.g. "37_NVMe_Spec.html"), up to 5
        """
        fp = self.fingerprint(query)
        routing = self._data["query_routing"]
        query_words = set(fp.split())

        # 1. Exact match
        if fp in routing and routing[fp]["count"] >= min_count:
            return list(routing[fp]["docs"])[:5]

        # 2. Partial overlap search (≥3 shared terms with saved patterns)
        best_docs: list[str] = []
        best_overlap = 0
        for stored_fp, data in routing.items():
            if data["count"] < min_count:
                continue
            stored_words = set(stored_fp.split())
            overlap = len(query_words & stored_words)
            if overlap >= 3 and overlap > best_overlap:
                best_overlap = overlap
                best_docs = list(data["docs"])[:5]

        if best_docs:
            return best_docs

        # 3. doc_topic_index: find docs whose known topics overlap with query
        topic_idx = self._data["doc_topic_index"]
        doc_scores: dict[str, int] = defaultdict(int)
        for doc, topics in topic_idx.items():
            overlap = len(query_words & set(t.lower() for t in topics))
            if overlap >= 2:
                doc_scores[doc] += overlap

        if doc_scores:
            return sorted(doc_scores, key=lambda d: -doc_scores[d])[:5]

        return []

    # ── Update from retrieval results ──────────────────────────────────────────

    # Non-corpus artifact filenames to exclude from routing and co-occurrence
    _ARTIFACT_FILES: frozenset = frozenset({
        "golden_code_plan.md",
        "unknown_source",
        "",
    })

    @staticmethod
    def _is_corpus_file(basename: str) -> bool:
        """Return True if basename looks like a real corpus document."""
        if not basename or len(basename) < 5:
            return False
        if basename in QueryStructuralMemory._ARTIFACT_FILES:
            return False
        if basename.startswith("wikigraph_") or basename.startswith("online_dcsg"):
            return False
        return True

    def update(
        self,
        query: str,
        retrieved_docs: list[str],
        doc_topic_keywords: dict[str, list[str]] | None = None,
    ):
        """
        Record which documents were retrieved for this query.

        Args:
          query: original query text
          retrieved_docs: list of doc basenames returned for this query
          doc_topic_keywords: optional {doc → [keywords]} for topic index update
        """
        # Filter non-corpus artifacts before recording
        clean_docs = [d for d in retrieved_docs if self._is_corpus_file(d)]

        fp = self.fingerprint(query)
        routing = self._data["query_routing"]

        if fp not in routing:
            routing[fp] = {"docs": [], "count": 0, "ts": time.time()}

        entry = routing[fp]
        entry["count"] += 1
        entry["ts"] = time.time()
        # Merge new docs into saved list (dedup, keep most recent)
        all_docs = list(dict.fromkeys(list(entry["docs"]) + clean_docs))
        entry["docs"] = all_docs[:10]

        # Update co-occurrence pending counter (corpus files only)
        docs = sorted(set(clean_docs))
        cooc = self._data["cooc_pending"]
        confirmed = self._data["cooc_confirmed"]
        for i in range(len(docs)):
            for j in range(i + 1, len(docs)):
                key = f"{docs[i]}||{docs[j]}"
                if key in confirmed:
                    continue  # already added as DCSG edge, skip
                cooc[key] = cooc.get(key, 0) + 1

        # Update doc topic index if keywords provided
        if doc_topic_keywords:
            topic_idx = self._data["doc_topic_index"]
            for doc, kws in doc_topic_keywords.items():
                if doc not in topic_idx:
                    topic_idx[doc] = []
                existing = set(topic_idx[doc])
                topic_idx[doc] = list(existing | set(kws))[:30]

    def build_doc_topic_index_from_rag(self, rag) -> int:
        """
        Populate doc_topic_index from DA DOC entity descriptions.
        Extracts filename terms and content keywords for each document.
        Should be called once after DA runs.
        """
        import asyncio

        async def _build():
            topic_idx = self._data["doc_topic_index"]
            updated = 0
            chunk_store = getattr(rag, "text_chunks", None)
            if not chunk_store:
                return 0

            file_to_words: dict[str, set[str]] = defaultdict(set)
            for cid, cdata in chunk_store._data.items():
                fp = cdata.get("file_path", "")
                if not fp:
                    continue
                basename = os.path.basename(fp)
                stem = os.path.splitext(basename)[0]
                terms = [t for t in re.split(r"[_\-\s\.]+", stem) if len(t) > 2]
                file_to_words[basename].update(t.lower() for t in terms)

                # Also extract high-value words from chunk content
                content = cdata.get("content", "")
                content_words = re.findall(r"\b[A-Za-z][A-Za-z0-9]{3,}\b", content)
                # Keep capitalized / technical terms (CamelCase, acronyms)
                tech_words = [w for w in content_words if w[0].isupper() or w.isupper()]
                file_to_words[basename].update(w.lower() for w in tech_words[:20])

            for fname, words in file_to_words.items():
                topic_idx[fname] = list(words)[:30]
                updated += 1

            return updated

        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _build())
                return future.result()
        else:
            return loop.run_until_complete(_build())

    # ── Online DCSG: pending edge candidates ──────────────────────────────────

    def get_pending_cooccurrences(
        self,
        min_count: int = 3,
        max_pairs: int = 100,
    ) -> list[tuple[str, str, int]]:
        """
        Return doc pairs ready for DCSG edge injection (online learning).

        Args:
          min_count: minimum co-occurrence queries to qualify
          max_pairs: maximum pairs to return

        Returns: [(doc_a, doc_b, count), ...] sorted by count desc
        """
        pairs = []
        for key, cnt in self._data["cooc_pending"].items():
            if cnt >= min_count:
                parts = key.split("||", 1)
                if len(parts) == 2:
                    pairs.append((parts[0], parts[1], cnt))
        return sorted(pairs, key=lambda x: -x[2])[:max_pairs]

    def mark_cooccurrences_confirmed(self, pairs: list[tuple[str, str]]):
        """
        Mark doc pairs as DCSG-confirmed so they're not re-injected.
        Called after DCSG edge injection.
        """
        cooc = self._data["cooc_pending"]
        confirmed = self._data["cooc_confirmed"]
        for a, b in pairs:
            key = f"{a}||{b}" if a <= b else f"{b}||{a}"
            cnt = cooc.pop(key, 0)
            confirmed[key] = confirmed.get(key, 0) + cnt

    # ── Stats ─────────────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        return {
            "query_patterns": len(self._data["query_routing"]),
            "docs_indexed": len(self._data["doc_topic_index"]),
            "pending_cooc_pairs": len(self._data["cooc_pending"]),
            "confirmed_pairs": len(self._data["cooc_confirmed"]),
        }
