"""
StructRAG retrieval pipeline — analyze → scope → retrieve → expand → fuse → score.

Design (DESIGN_HISTORY.md):
  - Scope from the structure graph is a SOFT boost (extra RRF ranking), never a
    hard filter (D5) — a wrong structural hint must not kill recall.
  - EXPAND pulls chunks from SG neighbors of the top hit docs; this is what
    attacks co-retrieval directly (research_notes.md §1, hyperlink-graph retrieval).
  - Quality is scored deterministically (quality.py) and every query is logged to
    structrag_meta/query_log.jsonl for the background evolver.
  - The KG and all VDBs are read-only here.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict

from harness_pjt.benchmark.retrieval import (
    query_vector, query_bm25, query_hybrid, query_mix_mode,
)
from harness_pjt.structrag.analyzer import QueryAnalyzer, QueryPlan
from harness_pjt.structrag.quality import RunningQuality, score_retrieval, salient_terms
from harness_pjt.structrag.structure_graph import StructureGraph

RRF_K = 60
SCOPE_BOOST = 0.6      # weight of the scope pseudo-ranking in fusion
EXPAND_TOP_DOCS = 5    # how many top hit docs get SG-neighbor expansion
EXPAND_NEIGHBORS = 4   # neighbors per hit doc
EXPAND_CHUNKS = 2      # chunks pulled per neighbor doc


async def _run_mode(rag, mode: str, query: str, top_k: int) -> list[dict]:
    if mode == "bm25":
        return query_bm25(rag, query, top_k=top_k)
    if mode == "vector":
        return await query_vector(rag, query, top_k=top_k)
    if mode == "hybrid":
        return await query_hybrid(rag, query, top_k=top_k)
    if mode == "mix":
        return await query_mix_mode(rag, query, top_k=top_k)
    if mode in ("local", "global"):
        from lightrag import QueryParam
        try:
            result = await rag.aquery_data(query, param=QueryParam(mode=mode, top_k=top_k))
            chunks = (result.get("data", {}) or {}).get("chunks", []) or []
            return [{"id": c.get("chunk_id", ""), "file_path": c.get("file_path", ""),
                     "content": c.get("content", "")[:200]} for c in chunks]
        except Exception:
            return []
    return await query_hybrid(rag, query, top_k=top_k)


def _cid(c: dict) -> str:
    return c.get("id") or c.get("chunk_id") or ""


class StructRetriever:
    def __init__(self, rag, working_dir: str, llm_func=None):
        self.rag = rag
        self.sg = StructureGraph(working_dir)
        self.analyzer = QueryAnalyzer(working_dir, llm_func=llm_func)
        self.rq = RunningQuality(working_dir)
        self._log_path = os.path.join(working_dir, "structrag_meta", "query_log.jsonl")
        self._doc_chunks: dict[str, list[str]] | None = None
        self.queries_seen = 0

    # ── doc → chunk index (lazy, in-memory) ──────────────────────────────────

    def _doc_chunk_index(self) -> dict[str, list[str]]:
        if self._doc_chunks is None:
            idx: dict[str, list[str]] = defaultdict(list)
            for cid, c in self.rag.text_chunks._data.items():
                bn = os.path.basename(c.get("file_path", ""))
                if bn:
                    idx[bn].append(cid)
            self._doc_chunks = dict(idx)
        return self._doc_chunks

    def _best_chunks_of_doc(self, doc: str, query: str, n: int) -> list[dict]:
        """Cheap lexical pick of the doc's chunks most relevant to the query."""
        terms = set(salient_terms(query))
        scored = []
        for cid in self._doc_chunk_index().get(doc, []):
            c = self.rag.text_chunks._data.get(cid)
            if not c:
                continue
            content = c.get("content", "")
            hits = sum(1 for t in terms if t in content.lower())
            scored.append((hits, cid, c))
        scored.sort(key=lambda x: -x[0])
        return [{**c, "id": cid, "file_path": c.get("file_path", "")}
                for _, cid, c in scored[:n]]

    # ── main entry ────────────────────────────────────────────────────────────

    async def retrieve(self, query: str, top_k: int = 20, learn: bool = True) -> dict:
        t0 = time.perf_counter()
        self.queries_seen += 1

        # 1. ANALYZE (Tier 0 cache / Tier 1 LLM / rules)
        plan: QueryPlan = await self.analyzer.analyze(query)

        # 2. SCOPE — seeds from query tokens/IDs + plan doc hints → SG 1-hop
        seeds = self.sg.match_docs_by_tokens(plan.rewrite, top_n=4)
        for hint in plan.doc_hints:
            bn = os.path.basename(hint)
            if bn in self.sg._data["nodes"] and bn not in seeds:
                seeds.append(bn)
        scope = self.sg.get_scope(seeds, budget=12) if seeds else {}

        # 3. RETRIEVE — each subquery with its selected mode (parallel-safe modes
        #    are already independent; run sequentially to keep LLM cache warm)
        rankings: list[list[str]] = []
        pool: dict[str, dict] = {}
        executed: set[tuple[str, str]] = set()
        for sub in plan.subqueries:
            # Graph modes (mix/local/global) run their own LLM keyword extraction —
            # feed them the ORIGINAL query so the LLM-response cache stays hot; the
            # rewrite/decomposition pays off in the lexical/dense modes instead.
            q_text = query if sub["mode"] in ("mix", "local", "global") else sub["q"]
            if (sub["mode"], q_text) in executed:
                continue
            executed.add((sub["mode"], q_text))
            results = await _run_mode(self.rag, sub["mode"], q_text, top_k)
            ids = []
            for c in results:
                cid = _cid(c)
                if cid:
                    pool.setdefault(cid, c)
                    ids.append(cid)
            if ids:
                rankings.append(ids)

        # Independent bm25 + vector rankings on the rewritten query: full-width so
        # lexical evidence gets equal votes in fusion (rare-term/ID queries), and
        # they double as the QPP evidence for quality scoring.
        vec_results = await query_vector(self.rag, plan.rewrite, top_k=top_k)
        bm25_results = query_bm25(self.rag, plan.rewrite, top_k=top_k)
        vec_ids = [_cid(c) for c in vec_results]
        bm25_ids = [_cid(c) for c in bm25_results]
        for c in vec_results + bm25_results:
            pool.setdefault(_cid(c), c)
        rankings.append(vec_ids)
        rankings.append(bm25_ids)

        # 4. FUSE (RRF) + scope soft-boost as an extra pseudo-ranking
        fused: dict[str, float] = defaultdict(float)
        for ranking in rankings:
            for i, cid in enumerate(ranking):
                fused[cid] += 1.0 / (RRF_K + i + 1)
        if scope:
            in_scope = [cid for cid, sc in sorted(fused.items(), key=lambda kv: -kv[1])
                        if os.path.basename(pool[cid].get("file_path", "")) in scope]
            for i, cid in enumerate(in_scope):
                conf = scope[os.path.basename(pool[cid].get("file_path", ""))]
                fused[cid] += SCOPE_BOOST * conf / (RRF_K + i + 1)

        # Doc-diversified selection: cap chunks per doc so the top_k window covers
        # more distinct documents (file-level recall + co-retrieval both need doc
        # breadth, not more chunks of the same doc).
        ordered = sorted(fused, key=lambda c: -fused[c])
        chunks, per_doc, deferred = [], defaultdict(int), []
        for cid in ordered:
            doc = os.path.basename(pool[cid].get("file_path", ""))
            if per_doc[doc] >= 2:
                deferred.append(cid)
                continue
            per_doc[doc] += 1
            chunks.append(pool[cid])
            if len(chunks) >= top_k:
                break
        for cid in deferred:  # backfill if diversification under-fills the window
            if len(chunks) >= top_k:
                break
            chunks.append(pool[cid])

        # Channel-representation guarantee: RRF dilutes docs that only ONE ranker
        # found (e.g. a rare-term card only BM25 ranks high gets outvoted by docs
        # two weak rankers agree on). Each channel's top-3 docs must survive into
        # the final window; they replace the lowest-fused tail.
        docs_sel = {os.path.basename(c.get("file_path", "")) for c in chunks}
        guaranteed = []
        for ranking in rankings:
            for cid in ranking[:3]:
                d = os.path.basename(pool[cid].get("file_path", ""))
                if d and d not in docs_sel:
                    guaranteed.append(pool[cid])
                    docs_sel.add(d)
        if guaranteed:
            keep = max(top_k // 2, top_k - len(guaranteed))
            chunks = chunks[:keep] + guaranteed[: top_k - keep]

        # 5. EXPAND — SG neighbors of top hit docs supply supplementary chunks
        top_docs = list(dict.fromkeys(
            os.path.basename(c.get("file_path", "")) for c in chunks if c.get("file_path")
        ))
        have_ids = {_cid(c) for c in chunks}
        supplement: list[dict] = []
        for doc in top_docs[:EXPAND_TOP_DOCS]:
            for nb, w in self.sg.get_neighbors(doc, top_n=EXPAND_NEIGHBORS, min_weight=0.3):
                if nb in top_docs:
                    continue
                for c in self._best_chunks_of_doc(nb, plan.rewrite, EXPAND_CHUNKS):
                    if c["id"] in have_ids:
                        continue
                    supplement.append({**c, "_sg_expand": True, "_sg_from": doc, "_sg_w": w})
                    have_ids.add(c["id"])
        final_chunks = chunks + supplement

        # 6. SCORE (deterministic QPP) + learn + log
        vec_sims = [c.get("score", 0.0) for c in vec_results]
        q = score_retrieval(
            plan.rewrite, final_chunks, vector_sims=vec_sims,
            bm25_ids=bm25_ids, vector_ids=vec_ids, scope=scope or None,
            chunk_docs=lambda cid: os.path.basename(pool.get(cid, {}).get("file_path", "")),
        )
        quality = q["quality"]

        retrieved_docs = list(dict.fromkeys(
            os.path.basename(c.get("file_path", "")) for c in final_chunks if c.get("file_path")
        ))
        self.rq.add(quality)
        if learn:
            self.analyzer.feedback(query, plan, quality, promote_gate=self.rq.promote_gate)
            self.sg.reinforce_co_retrieval(retrieved_docs[:10], quality,
                                           quality_gate=self.rq.reinforce_gate)

        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        self._log({
            "ts": round(time.time(), 1), "query": query, "rewrite": plan.rewrite,
            "source": plan.source,
            "subqueries": [{"q": s["q"][:80], "intent": s["intent"], "mode": s["mode"]}
                           for s in plan.subqueries],
            "scope": list(scope)[:12], "retrieved": retrieved_docs[:20],
            "quality": quality, "signals": q["signals"],
            "sg_expand": len(supplement), "latency_ms": latency_ms,
        })

        return {
            "chunks": final_chunks, "plan": plan, "scope": scope,
            "quality": quality, "signals": q["signals"],
            "sg_expand": len(supplement), "latency_ms": latency_ms,
            "retrieved_docs": retrieved_docs,
        }

    def _log(self, rec: dict):
        os.makedirs(os.path.dirname(self._log_path), exist_ok=True)
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def save(self):
        self.analyzer.save()
        self.sg.save()
        self.rq.save()
