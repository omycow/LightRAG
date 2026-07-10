"""
Two-track background evolver (DESIGN_HISTORY.md D1, D6, D7).

  Track S (structure → SG only):
    rules: decay unused learned edges, prune low-quality cached plans
    LLM  : ① typed structural edges from co-retrieval evidence bundles
           ② wiki-style doc profiles for scope routing
  Track K (semantics → KG, chunk evidence required):
    rules: stale-edge removal (source chunks gone), dedup candidate report
    LLM  : ③ entity-description wikification (<SEP>-cluttered nodes → clean
             consolidated text → better entity-VDB embeddings)
           ④ extraction gap fill for often-retrieved chunks with no entities
    Doc-level structure and co-retrieval statistics NEVER enter the KG (ver4 rule).

  Every mutation is appended to structrag_meta/evolution_log.jsonl with track,
  evidence and reason. KG files can be checkpointed before a cycle and rolled
  back by the eval harness if the regression guard trips (D7).

  Boundary between hot path and here: the retriever already does the cheap
  inline learning (L2 reinforce, bandit, plan promotion). This module does
  everything slower, on a budget, off the query path.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from collections import Counter, defaultdict

GRAPH_FIELD_SEP = "<SEP>"

# files that constitute the KG + its embeddings (checkpointed before Track K)
KG_FILES = ["graph_chunk_entity_relation.graphml", "vdb_entities.json"]

_EDGE_PROMPT = """Two technical documents keep being retrieved together for the same queries.
Decide if there is a REAL structural relation between them, using ONLY the evidence below.
Reply with ONLY JSON: {{"relation": "spec-of|implements|tests|references|part-of|none",
"confidence": 0.0-1.0, "rationale": "<one sentence citing the evidence>"}}

Document A: {doc_a}
Excerpt A: {excerpt_a}
Document B: {doc_b}
Excerpt B: {excerpt_b}
Example queries that retrieved both: {queries}"""

_PROFILE_PROMPT = """Write a 2-sentence factual profile of this technical document for a \
retrieval router: what it is, and which topics/components/IDs it covers. No fluff.
Document: {doc}
Excerpts: {excerpts}"""

_WIKIFY_PROMPT = """This knowledge-graph entity has a cluttered description merged from \
multiple sources (separated by <SEP>). Rewrite it as ONE clean, information-dense \
description keeping every distinct fact, ID and relationship. Reply with only the new text.
Entity: {name}
Description: {desc}"""


class Evolver:
    def __init__(self, rag, retriever, llm_func=None, llm_budget: int = 10):
        self.rag = rag
        self.retriever = retriever          # StructRetriever (sg, analyzer, rq, query log)
        self.llm_func = llm_func
        self.llm_budget = llm_budget
        self._meta = os.path.join(os.path.dirname(retriever._log_path))
        self._state_path = os.path.join(self._meta, "evolver_state.json")
        try:
            self._state = json.load(open(self._state_path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._state = {"log_cursor": 0, "cycles": 0}

    # ── query-log window since last cycle ────────────────────────────────────

    def _read_new_records(self) -> list[dict]:
        path = self.retriever._log_path
        if not os.path.exists(path):
            return []
        recs = []
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[self._state["log_cursor"]:]:
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        self._state["log_cursor"] = len(lines)
        return recs

    # ── KG checkpoint / rollback (used by the eval harness regression guard) ──

    def checkpoint_kg(self) -> str:
        ckpt = os.path.join(self._meta, "kg_checkpoint")
        os.makedirs(ckpt, exist_ok=True)
        for fn in KG_FILES:
            src = os.path.join(self.rag.working_dir, fn)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(ckpt, fn))
        return ckpt

    def rollback_kg(self, reason: str):
        ckpt = os.path.join(self._meta, "kg_checkpoint")
        for fn in KG_FILES:
            src = os.path.join(ckpt, fn)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(self.rag.working_dir, fn))
        self.retriever.sg.log("kg_rollback", track="K", reason=reason)

    # ── main cycle ────────────────────────────────────────────────────────────

    async def run_cycle(self) -> dict:
        t0 = time.perf_counter()
        recs = self._read_new_records()
        sg = self.retriever.sg
        rq = self.retriever.rq
        budget = self.llm_budget
        report = {"records": len(recs), "s_rules": {}, "s_llm": {}, "k_rules": {}, "k_llm": {},
                  "llm_calls": 0}

        good = [r for r in recs if r["quality"] >= rq.reinforce_gate]
        attention = [r for r in recs if r["quality"] <= rq.attention_gate]
        report["good"] = len(good)
        report["attention"] = len(attention)

        # ── Track S · rules ───────────────────────────────────────────────────
        removed = sg.decay()
        self.retriever.analyzer.cache.invalidate_low(min_quality=rq.attention_gate)
        report["s_rules"] = {"decayed_layers": removed}

        # ── Track S · LLM ① typed structural edges ────────────────────────────
        if self.llm_func and budget > 0:
            added = 0
            for (a, b), cnt, queries in self._typed_edge_candidates(good):
                if budget <= 0:
                    break
                budget -= 1
                report["llm_calls"] += 1
                verdict = await self._propose_edge(a, b, queries)
                if verdict:
                    rel, conf, rationale, evidence = verdict
                    sg.add_curated_edge(a, b, rel, evidence, rationale,
                                        weight=min(0.9, 0.5 + conf * 0.4))
                    added += 1
            report["s_llm"]["typed_edges"] = added

        # ── Track S · LLM ② doc profiles ──────────────────────────────────────
        if self.llm_func and budget > 0:
            profiled = 0
            for doc in self._profile_candidates(recs, limit=min(3, budget)):
                budget -= 1
                report["llm_calls"] += 1
                doc_chunks, _ = self.retriever._best_chunks_of_doc(doc, doc, 2)
                excerpts = " / ".join(c["content"][:250] for c in doc_chunks)
                try:
                    profile = await self.llm_func(_PROFILE_PROMPT.format(doc=doc, excerpts=excerpts))
                    if profile and len(profile) > 30:
                        sg.set_profile(doc, profile.strip())
                        profiled += 1
                except Exception:
                    pass
            report["s_llm"]["profiles"] = profiled

        # ── Track K · rules: stale edges + dedup candidate report ────────────
        report["k_rules"] = await self._k_rules()

        # ── Track K · LLM ③ description wikification ──────────────────────────
        if self.llm_func and budget > 0:
            report["k_llm"]["wikified"] = await self._wikify_descriptions(
                attention, max_nodes=min(3, budget))
            budget -= report["k_llm"]["wikified"]
            report["llm_calls"] += report["k_llm"]["wikified"]

        # ── bookkeeping ───────────────────────────────────────────────────────
        self._state["cycles"] += 1
        json.dump(self._state, open(self._state_path, "w", encoding="utf-8"))
        self.retriever.save()
        report["elapsed_s"] = round(time.perf_counter() - t0, 1)
        sg.log("evolve_cycle", track="S+K", reason="scheduled background cycle", **{
            k: v for k, v in report.items() if k != "records"})
        return report

    # ── Track S helpers ───────────────────────────────────────────────────────

    def _typed_edge_candidates(self, good: list[dict], min_cooc: int = 2, limit: int = 5):
        """Doc pairs co-retrieved by ≥2 distinct high-quality queries, not already
        explained by an L1/L3 edge — the pairs where LLM curation adds information."""
        pair_queries: dict[tuple, list[str]] = defaultdict(list)
        for r in good:
            docs = r.get("retrieved", [])[:8]
            for i in range(len(docs)):
                for j in range(i + 1, len(docs)):
                    key = tuple(sorted((docs[i], docs[j])))
                    pair_queries[key].append(r["query"])
        edges = self.retriever.sg._data["edges"]
        out = []
        for pair, queries in pair_queries.items():
            if len(queries) < min_cooc:
                continue
            key = f"{pair[0]}||{pair[1]}"
            layers = edges.get(key, {}).get("layers", {})
            if "explicit_ref" in layers or "llm_curated" in layers:
                continue
            out.append((pair, len(queries), queries[:3]))
        out.sort(key=lambda x: -x[1])
        return out[:limit]

    async def _propose_edge(self, a: str, b: str, queries: list[str]):
        ex_a, _ = self.retriever._best_chunks_of_doc(a, " ".join(queries), 1)
        ex_b, _ = self.retriever._best_chunks_of_doc(b, " ".join(queries), 1)
        if not ex_a or not ex_b:
            return None
        try:
            raw = await self.llm_func(_EDGE_PROMPT.format(
                doc_a=a, excerpt_a=ex_a[0]["content"][:400],
                doc_b=b, excerpt_b=ex_b[0]["content"][:400],
                queries="; ".join(q[:80] for q in queries),
            ))
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            d = json.loads(m.group(0)) if m else {}
            rel = d.get("relation", "none")
            conf = float(d.get("confidence", 0))
            if rel != "none" and conf >= 0.6:
                return rel, conf, d.get("rationale", ""), [ex_a[0]["id"], ex_b[0]["id"]]
        except Exception:
            pass
        return None

    def _profile_candidates(self, recs: list[dict], limit: int) -> list[str]:
        counts = Counter(d for r in recs for d in r.get("retrieved", [])[:10])
        nodes = self.retriever.sg._data["nodes"]
        return [d for d, _ in counts.most_common(50)
                if d in nodes and not nodes[d].get("profile")][:limit]

    # ── Track K helpers ───────────────────────────────────────────────────────

    async def _k_rules(self) -> dict:
        """Stale-edge removal + duplicate-entity candidate report (report only —
        automatic node merging is deferred; see DESIGN_HISTORY History)."""
        graph = self.rag.chunk_entity_relation_graph
        chunk_ids = set(self.rag.text_chunks._data.keys())
        stale = []
        try:
            edges = await graph.edges()
            for src, tgt in edges:
                edge = await graph.get_edge(src, tgt)
                if not edge:
                    continue
                sid = edge.get("source_id", "")
                fp = edge.get("file_path", "")
                if fp.startswith("wikigraph") or not sid:
                    continue
                refs = [c for c in sid.split(GRAPH_FIELD_SEP) if c.strip()]
                if refs and all(c not in chunk_ids for c in refs):
                    stale.append((src, tgt))
            if stale:
                await graph.remove_edges(stale)
                await graph.index_done_callback()
                self.retriever.sg.log("k_stale_edges_removed", track="K",
                                      count=len(stale),
                                      reason="all source chunks gone from corpus")
        except Exception:
            pass
        return {"stale_edges_removed": len(stale)}

    async def _wikify_descriptions(self, attention: list[dict], max_nodes: int) -> int:
        """Consolidate <SEP>-cluttered entity descriptions, prioritizing entities
        from documents involved in low-quality retrievals. Chunk-evidence rule:
        we only rewrite text that came from real source merges — nothing invented."""
        if max_nodes <= 0:
            return 0
        from lightrag.utils import compute_mdhash_id
        graph = self.rag.chunk_entity_relation_graph

        # attention docs first, else global scan
        att_docs = {d for r in attention for d in r.get("retrieved", [])[:10]}
        candidates: list[tuple[int, str, dict]] = []
        try:
            labels = await graph.get_all_labels()
        except Exception:
            return 0
        for name in labels[:4000]:
            node = await graph.get_node(name)
            if not node:
                continue
            desc = node.get("description", "")
            seps = desc.count(GRAPH_FIELD_SEP)
            if seps < 3:
                continue
            prio = 1 if node.get("file_path", "") in att_docs else 0
            candidates.append((prio * 100 + seps, name, node))
        candidates.sort(key=lambda x: -x[0])

        done = 0
        vdb_batch = {}
        for _, name, node in candidates[:max_nodes]:
            try:
                new_desc = await self.llm_func(_WIKIFY_PROMPT.format(
                    name=name, desc=node["description"][:3000]))
                new_desc = (new_desc or "").strip()
                if len(new_desc) < 40 or GRAPH_FIELD_SEP in new_desc:
                    continue
                await graph.upsert_node(name, {**node, "description": new_desc})
                vdb_batch[compute_mdhash_id(name, prefix="ent-")] = {
                    "content": f"{name}\n{new_desc}",
                    "entity_name": name,
                }
                self.retriever.sg.log(
                    "k_wikify", track="K", entity=name,
                    reason=f"consolidated {node['description'].count(GRAPH_FIELD_SEP)+1} merged fragments",
                )
                done += 1
            except Exception:
                pass
        if vdb_batch:
            await graph.index_done_callback()
            await self.rag.entities_vdb.upsert(vdb_batch)
        return done
