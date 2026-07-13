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
EXPAND_BUDGET = 6      # max expansion docs admitted into the unified ranking
EXPAND_L1_SLOTS = 4    # budget seats reserved for explicit-ref (L1) candidates
EXPAND_TAIL = 8        # budget-overflow expansions appended after the window


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

    def _best_chunks_of_doc(self, doc: str, query: str, n: int) -> tuple[list[dict], int]:
        """Cheap lexical pick of the doc's chunks most relevant to the query.
        Returns (chunks, best_hit_count) — the hit count doubles as a relevance
        signal for expansion-score modulation."""
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
        best_hits = scored[0][0] if scored else 0
        return ([{**c, "id": cid, "file_path": c.get("file_path", "")}
                 for _, cid, c in scored[:n]], best_hits)

    # ── main entry ────────────────────────────────────────────────────────────

    async def retrieve(self, query: str, top_k: int = 20, learn: bool = True) -> dict:
        t0 = time.perf_counter()
        self.queries_seen += 1

        # 1. ANALYZE (Tier 0 cache / Tier 1 LLM / rules)
        plan: QueryPlan = await self.analyzer.analyze(
            query, min_cache_quality=self.rq.promote_gate)

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

        # 5. Doc-level ranking with SG score propagation (v5.2 fix C5).
        # Previous versions appended SG-expansion chunks AFTER the base window, so
        # a linked doc reached from the #1 hit via a strong explicit edge landed at
        # doc-rank 10-20 — co@5/co@10 were structurally unreachable. Instead the
        # neighbor inherits its anchor's fused score discounted by edge strength
        # (path-score propagation, hyperlink-graph retrieval — research_notes §1)
        # and competes in one unified doc ranking.
        _doc = lambda cid: os.path.basename(pool[cid].get("file_path", ""))
        doc_score: dict[str, float] = {}
        doc_chunks: dict[str, list[str]] = defaultdict(list)
        for cid in sorted(fused, key=lambda c: -fused[c]):
            d = _doc(cid)
            if not d:
                continue
            doc_score.setdefault(d, fused[cid])  # best (first-seen) chunk score
            doc_chunks[d].append(cid)

        base_rank = sorted(doc_score, key=lambda d: -doc_score[d])

        # Channel-representation guarantee (doc level): each ranking channel's
        # top-3 docs must survive into the window — RRF alone lets docs found by
        # a single precise ranker (rare-term BM25 hits) get outvoted.
        window = base_rank[:top_k]
        floor_score = doc_score[window[-1]] if window else 0.0
        for ranking in rankings:
            for cid in ranking[:3]:
                d = _doc(cid)
                if d and d not in doc_score:
                    doc_score[d] = floor_score * 0.95
                    doc_chunks[d].append(cid)

        # EXPAND: SG neighbors of the top anchor docs join the ranking with an
        # inherited, edge-discounted score, tiered by edge provenance (v5.4):
        #   - explicit_ref (L1): the anchor document ITSELF declares this link —
        #     admitted on structural trust alone. Lexical gating here is wrong by
        #     construction (v5.3 lesson): linked docs are often lexically distant
        #     from the query, which is exactly why structural expansion exists.
        #   - learned edges (L2/L3): statistical correlation — must additionally
        #     show query-term relevance to enter the window.
        # A global budget still caps how many expansion docs join the ranking
        # (v5.2 lesson: unbounded admission pushed correct base docs out).
        expansion: dict[str, tuple[float, str, float, list[dict], bool]] = {}
        for anchor in base_rank[:EXPAND_TOP_DOCS]:
            for nb, w, is_l1 in self.sg.get_neighbors_layered(
                    anchor, top_n=EXPAND_NEIGHBORS, min_weight=0.3):
                if nb in doc_score or nb in expansion:
                    continue
                picked, best_hits = self._best_chunks_of_doc(nb, plan.rewrite, EXPAND_CHUNKS)
                if not picked:
                    continue
                if is_l1:
                    s = doc_score[anchor] * (0.5 + 0.5 * w)
                else:
                    relevance = 0.4 + 0.6 * min(1.0, best_hits / 3.0)
                    s = doc_score[anchor] * (0.4 + 0.6 * w) * relevance
                expansion[nb] = (s, anchor, w, picked, is_l1)

        # v5.6: provenance-reserved budget seats. Pure score competition let ever-
        # growing L2 stats catch up with single-mention L1 edges (both eff 0.6) and
        # evict the document-declared link from the ranked window over iterations —
        # the co@10 decline signature (rises at 20 via tail, falls at 10). L1
        # candidates own EXPAND_L1_SLOTS seats; learned edges compete for the rest;
        # unused seats on either side are released to the other.
        ranked_exp = sorted(expansion.items(), key=lambda kv: -kv[1][0])
        l1_c = [kv for kv in ranked_exp if kv[1][4]]
        ln_c = [kv for kv in ranked_exp if not kv[1][4]]
        # v5.7 I3: unused L1 seats are NOT released to learned candidates — echo-
        # strengthened L2 edges were claiming them with increasingly self-
        # referential docs. An unfilled seat goes back to base documents instead.
        top_list = l1_c[:EXPAND_L1_SLOTS] + ln_c[:EXPAND_BUDGET - EXPAND_L1_SLOTS]
        top_expansion = dict(top_list)
        # v5.5 safety net: budget losers still get appended after the ranked
        # window — serves co@20 while the reserved seats serve co@5/10.
        tail_expansion = dict([kv for kv in ranked_exp if kv[0] not in top_expansion][:EXPAND_TAIL])

        merged = dict(doc_score)
        merged.update({d: v[0] for d, v in top_expansion.items()})
        doc_rank = sorted(merged, key=lambda d: -merged[d])

        # v5.14 escort: the #1 doc's single strongest explicit-ref neighbor is
        # placed directly behind it. Path-expansion logic (Asai §1): if the best
        # match declares one link above all others, that link is the best second
        # guess. Structural signal only (edge weight — D8), costs one slot.
        if doc_rank:
            top1 = doc_rank[0]
            l1_nbs = [(nb, w) for nb, w, is_l1 in self.sg.get_neighbors_layered(
                top1, top_n=1, min_weight=0.3) if is_l1]
            if l1_nbs:
                esc = l1_nbs[0][0]
                if esc in doc_rank:
                    doc_rank.remove(esc)
                doc_rank.insert(1, esc)
                if esc not in top_expansion and esc not in doc_chunks:
                    picked, _ = self._best_chunks_of_doc(esc, plan.rewrite, EXPAND_CHUNKS)
                    if picked:
                        top_expansion[esc] = (merged.get(top1, 0.0), top1, l1_nbs[0][1], picked, True)
                    else:
                        doc_rank.remove(esc)

        # assemble chunks in doc-rank order: ≤2 fused chunks per base doc,
        # ≤EXPAND_CHUNKS lexically-best chunks per expansion doc
        final_chunks: list[dict] = []
        supplement_count = 0
        chunk_budget = top_k + 10
        for d in doc_rank:
            if len(final_chunks) >= chunk_budget:
                break
            if d in top_expansion:
                _, anchor, w, picked, _ = top_expansion[d]
                for c in picked:
                    final_chunks.append({**c, "_sg_expand": True, "_sg_from": anchor, "_sg_w": w})
                supplement_count += len(picked)
            else:
                for cid in doc_chunks[d][:2]:
                    final_chunks.append(pool[cid])
        for d, (s, anchor, w, picked, _) in tail_expansion.items():
            if len(final_chunks) >= chunk_budget + EXPAND_TAIL:
                break
            final_chunks.append({**picked[0], "_sg_expand": True, "_sg_from": anchor,
                                 "_sg_w": w, "_sg_tail": True})
            supplement_count += 1

        # 6. SCORE (deterministic QPP) + learn + log
        vec_sims = [c.get("score", 0.0) for c in vec_results]
        # struct_coverage (C2): among the top BASE docs (pre-expansion), how many
        # have at least one strong L1 neighbor also present in the base window —
        # a co-retrieval proxy the promote/reinforce gates can act on. Computed on
        # base results so blindly stuffed expansions can't inflate it.
        base_set = set(base_rank[:top_k])
        cov_vals = []
        for d in base_rank[:5]:
            l1_nbs = [nb for nb, w in self.sg.get_neighbors(d, top_n=5, min_weight=0.5)]
            if l1_nbs:
                cov_vals.append(1.0 if any(nb in base_set for nb in l1_nbs) else 0.0)
        extra = {"struct_coverage": sum(cov_vals) / len(cov_vals)} if cov_vals else None
        q = score_retrieval(
            plan.rewrite, final_chunks, vector_sims=vec_sims,
            bm25_ids=bm25_ids, vector_ids=vec_ids, scope=scope or None,
            chunk_docs=lambda cid: os.path.basename(pool.get(cid, {}).get("file_path", "")),
            extra_signals=extra,
        )
        quality = q["quality"]

        retrieved_docs = list(dict.fromkeys(
            os.path.basename(c.get("file_path", "")) for c in final_chunks if c.get("file_path")
        ))
        self.rq.add(quality)
        if learn:
            self.analyzer.feedback(query, plan, quality,
                                   promote_gate=self.rq.promote_gate,
                                   promote_floor=self.rq.attention_gate)
            # v5.8 selective echo: expansion-injected docs participate in L2
            # reinforcement only for pairs with explicit_ref provenance (see
            # reinforce_co_retrieval). v5.7's blanket base-only rule killed the
            # growth engine; v5.6's no-rule version built an echo chamber.
            exp_docs = {os.path.basename(c.get("file_path", ""))
                        for c in final_chunks if c.get("_sg_expand")}
            self.sg.reinforce_co_retrieval(retrieved_docs[:10], quality,
                                           quality_gate=self.rq.reinforce_gate,
                                           expansion_docs=exp_docs)

        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        self._log({
            "ts": round(time.time(), 1), "query": query, "rewrite": plan.rewrite,
            "source": plan.source,
            "subqueries": [{"q": s["q"][:80], "intent": s["intent"], "mode": s["mode"]}
                           for s in plan.subqueries],
            "scope": list(scope)[:12], "retrieved": retrieved_docs[:20],
            "quality": quality, "signals": q["signals"],
            "sg_expand": supplement_count, "latency_ms": latency_ms,
        })

        return {
            "chunks": final_chunks, "plan": plan, "scope": scope,
            "quality": quality, "signals": q["signals"],
            "sg_expand": supplement_count, "latency_ms": latency_ms,
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
