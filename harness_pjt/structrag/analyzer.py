"""
Query analyzer — LLM-based, tiered for speed (DESIGN_HISTORY.md D3).

  Tier 0  PlanCache hit: a similar query already produced a high-quality plan
          → reuse it, zero LLM calls (the hot path most repeat traffic takes).
  Tier 1  Single compact LLM call: rewrite + decomposition + per-subquery intent
          + doc hints, one JSON response. Merged with rule validation so the LLM
          can't route an ID-lookup away from lexical search.
  Tier 2  (background, evolver ⑥) chronic low-quality clusters get deeper
          rewrite mining; results land back in the PlanCache.

Mode selection = consensus of LLM intent, deterministic rules (ID tokens → bm25;
see research_notes.md §2), and the StrategyMemory ε-greedy bandit.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass, field

from harness_pjt.structrag.structure_graph import _ID_TOKEN

MODES = ("bm25", "vector", "hybrid", "mix", "local", "global")

# intent → ordered mode candidates (rule defaults; research_notes.md §2).
# concept defaults to hybrid: on this pipeline's own failure decomposition
# (DESIGN_HISTORY 2026-07-09, 3차) hybrid recovered 14/15 keyword-recall misses
# while mix recovered 0/15 — the SG EXPAND step supplies the cross-doc value mix
# used to add, so mix stays available for cross_doc intent and bandit exploration.
INTENT_MODES = {
    "id_lookup": ["bm25", "hybrid"],
    "concept": ["hybrid", "mix", "vector"],
    "cross_doc": ["mix", "hybrid"],
}

_CONJ_SPLIT = re.compile(r"(?:\?|그리고|및 또한|\band\b(?=\s+(?:how|what|which|where)))", re.IGNORECASE)


@dataclass
class QueryPlan:
    original: str
    rewrite: str
    subqueries: list[dict] = field(default_factory=list)  # {"q", "intent", "mode"}
    doc_hints: list[str] = field(default_factory=list)
    source: str = "rules"  # "cache" | "llm" | "rules" — how THIS query got the plan
    origin: str = ""       # "llm" | "rules" — who authored the plan (survives caching)

    def __post_init__(self):
        if not self.origin:
            self.origin = self.source if self.source in ("llm", "rules") else "rules"

    def to_dict(self) -> dict:
        return {"original": self.original, "rewrite": self.rewrite,
                "subqueries": self.subqueries, "doc_hints": self.doc_hints,
                "source": self.source, "origin": self.origin}

    @staticmethod
    def from_dict(d: dict) -> "QueryPlan":
        return QueryPlan(d["original"], d["rewrite"], d["subqueries"],
                         d.get("doc_hints", []), d.get("source", "cache"),
                         d.get("origin", ""))


def fingerprint(query: str) -> str:
    """Stable keyword fingerprint (same scheme as wikigraph.qsm, +Korean tokens)."""
    words = sorted(set(
        w.lower() for w in re.findall(r"[\w가-힣]{3,}", query)
        if w.lower() not in {"the", "and", "for", "that", "this", "are", "was",
                             "what", "how", "why", "when", "where", "which"}
    ))
    return " ".join(words[:12])


# Artifact-type vocabulary for the skeleton signature (KO→EN normalization,
# language handling — not a corpus convention).
_SKEL_TYPES = [("test", ("테스트", "test", "검증", "validation")),
               ("spec", ("스펙", "spec", "specification", "규격")),
               ("issue", ("이슈", "issue", "jira", "티켓"))]


def skeleton(query: str) -> str:
    """Content-agnostic query-SHAPE signature (v5.15 ①). Two queries about
    different products/components share a skeleton when their form matches —
    strategy learning generalizes across keywords ('Helios GC 테스트 있나요' and
    'Lyra thermal 검증 코드 있어?' are the same shape). The content fingerprint
    stays the PlanCache key: plans carry doc hints and must not cross content."""
    q = query.lower()
    has_id = 1 if _ID_TOKEN.search(query) else 0
    dtype = next((n for n, hints in _SKEL_TYPES if any(h in q for h in hints)), "none")
    parts = [p for p in _CONJ_SPLIT.split(query) if len(p.strip()) >= 8]
    multi = 1 if len(parts) >= 2 else 0
    size = "s" if len(query) < 40 else ("m" if len(query) < 80 else "l")
    return f"id:{has_id}|type:{dtype}|multi:{multi}|len:{size}"


class StrategyMemory:
    """Mode-quality EMAs keyed by query SKELETON (v5.15 ①) — strategy learning
    pools evidence across content-different, shape-alike queries. Also tracks
    per-skeleton plan-source value (rules vs llm) for the LLM-worth gate (②)."""

    def __init__(self, working_dir: str, epsilon: float = 0.1, alpha: float = 0.3):
        meta = os.path.join(working_dir, "structrag_meta")
        os.makedirs(meta, exist_ok=True)
        self._path = os.path.join(meta, "strategy_memory.json")
        self.epsilon = epsilon
        self.alpha = alpha
        try:
            raw = json.load(open(self._path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        if "modes" in raw or "src" in raw:
            self._data = raw.get("modes", {})
            self._src = raw.get("src", {})
        else:  # legacy flat schema
            self._data = raw
            self._src = {}

    def save(self):
        json.dump({"modes": self._data, "src": self._src},
                  open(self._path, "w", encoding="utf-8"), ensure_ascii=False)

    # ── plan-source value tracking (v5.15 ② LLM-worth gate) ─────────────────

    def update_source(self, skel: str, source: str, quality: float):
        s = self._src.setdefault(skel, {}).setdefault(source, {"ema": quality, "n": 0})
        s["ema"] = (1 - self.alpha) * s["ema"] + self.alpha * quality
        s["n"] += 1

    def llm_adds_value(self, skel: str, margin: float = 0.03, min_n: int = 3) -> bool:
        """False only when BOTH sources have evidence and llm ≈ rules — then a
        background LLM analysis for this query shape is money for nothing."""
        src = self._src.get(skel, {})
        llm, rules = src.get("llm"), src.get("rules")
        if llm and rules and llm["n"] >= min_n and rules["n"] >= min_n:
            return llm["ema"] - rules["ema"] >= margin
        return True

    def select(self, cluster: str, candidates: list[str]) -> str:
        """Best learned mode among candidates, ε-greedy with annealing: exploration
        fades as a cluster accumulates visits (v5.2 C6 — constant ε kept flipping
        modes on well-learned clusters, one source of iteration-to-iteration
        metric wobble). Falls back to the first candidate."""
        stats = self._data.get(cluster, {})
        visits = sum(s["n"] for s in stats.values())
        eps_eff = self.epsilon / (1.0 + visits / 5.0)
        if random.random() < eps_eff and len(candidates) > 1:
            return random.choice(candidates)
        best, best_ema = None, -1.0
        for m in candidates:
            s = stats.get(m)
            if s and s["n"] >= 2 and s["ema"] > best_ema:
                best, best_ema = m, s["ema"]
        return best or candidates[0]

    def update(self, cluster: str, mode: str, quality: float):
        stats = self._data.setdefault(cluster, {})
        s = stats.setdefault(mode, {"ema": quality, "n": 0})
        s["ema"] = (1 - self.alpha) * s["ema"] + self.alpha * quality
        s["n"] += 1

    @property
    def stats(self) -> dict:
        return {"clusters": len(self._data)}


class PlanCache:
    """fingerprint → validated high-quality plan. Promotion happens post-scoring."""

    PROMOTE_QUALITY = 0.65

    def __init__(self, working_dir: str):
        meta = os.path.join(working_dir, "structrag_meta")
        os.makedirs(meta, exist_ok=True)
        self._path = os.path.join(meta, "plan_cache.json")
        try:
            self._data = json.load(open(self._path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._data = {}
        self.hits = 0
        self.misses = 0

    def save(self):
        json.dump(self._data, open(self._path, "w", encoding="utf-8"), ensure_ascii=False)

    def get(self, query: str, min_quality: float | None = None) -> QueryPlan | None:
        """Return a cached plan when it's trustworthy enough to skip re-analysis.

        v5.2 C4: high-quality plans (≥ promote gate) are always reused; a
        mid-quality plan is reused only if it CAME FROM the LLM — re-calling the
        LLM on the same query pattern just re-buys the same plan, while a
        mid-quality rules plan keeps its one chance to be upgraded by the LLM."""
        fp = fingerprint(query)
        entry = self._data.get(fp)
        if entry is None:
            # partial overlap: ≥75% token overlap with a cached fingerprint
            q_words = set(fp.split())
            if q_words:
                for cfp, e in self._data.items():
                    cw = set(cfp.split())
                    if cw and len(q_words & cw) / len(q_words | cw) >= 0.75:
                        entry = e
                        break
        if entry is not None:
            high_bar = entry["quality"] >= (min_quality if min_quality is not None
                                            else self.PROMOTE_QUALITY)
            llm_reuse = (entry["plan"].get("source") == "llm"
                         and entry["quality"] >= entry.get("floor", 0.45))
            if not (high_bar or llm_reuse):
                entry = None
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        plan = QueryPlan.from_dict(entry["plan"])
        plan.original = query
        plan.source = "cache"
        return plan

    def promote(self, query: str, plan: QueryPlan, quality: float, gate: float | None = None,
                floor: float | None = None):
        """Store best-so-far plan per fingerprint. Plans below the attention floor
        are never stored; get() decides reuse (see above)."""
        if floor is not None and quality < floor:
            return
        if floor is None and quality < (gate if gate is not None else self.PROMOTE_QUALITY):
            return
        fp = fingerprint(query)
        cur = self._data.get(fp)
        if cur is None or quality >= cur["quality"]:
            self._data[fp] = {"plan": plan.to_dict(), "quality": quality,
                              "floor": floor if floor is not None else 0.45,
                              "ts": time.time()}

    def invalidate_low(self, min_quality: float = 0.5):
        for fp in [f for f, e in self._data.items() if e["quality"] < min_quality]:
            del self._data[fp]

    @property
    def stats(self) -> dict:
        total = self.hits + self.misses
        return {"entries": len(self._data), "hits": self.hits,
                "hit_rate": round(self.hits / total, 3) if total else 0.0}


# ── rule layer ────────────────────────────────────────────────────────────────

def rule_intent(query: str) -> str:
    if _ID_TOKEN.search(query) or re.search(r"\.\w{2,4}\b", query):
        return "id_lookup"
    return "concept"


# v5.17: complementary-artifact facet. A question about a TEST implies interest
# in its spec/issues (the co-retrieval intent behind the query) — engineering-
# domain ontology, not a corpus convention. Deterministic, no LLM, generated at
# plan time so the facet both joins fusion and feeds facet_link learning.
_COMPLEMENT_FACETS = {"test": "스펙 스펙문서 이슈 issue", "spec": "검증 테스트 test",
                      "issue": "검증 테스트 test"}
_TYPE_WORDS = {h for _, hints in _SKEL_TYPES for h in hints}


def complementary_facet(query: str) -> dict | None:
    q = query.lower()
    dtype = next((n for n, hints in _SKEL_TYPES if any(h in q for h in hints)), None)
    if dtype not in _COMPLEMENT_FACETS:
        return None
    from harness_pjt.structrag.quality import salient_terms
    content = [t for t in salient_terms(query) if t not in _TYPE_WORDS][:8]
    if len(content) < 2:
        return None
    return {"q": " ".join(content) + " " + _COMPLEMENT_FACETS[dtype],
            "intent": "cross_doc", "mode": None, "_complement": True}


def rule_plan(query: str) -> QueryPlan:
    parts = [p.strip() for p in _CONJ_SPLIT.split(query) if p and len(p.strip()) >= 8]
    subs = parts if len(parts) > 1 else [query]
    return QueryPlan(
        original=query, rewrite=query,
        subqueries=[{"q": s, "intent": rule_intent(s), "mode": None} for s in subs[:3]],
        source="rules",
    )


# ── LLM tier ──────────────────────────────────────────────────────────────────

_LLM_PROMPT = """You analyze search queries for a technical document retrieval system \
(specs, jira issues, confluence pages, validation test cards; Korean/English mixed).
Reply with ONLY a JSON object, no prose:
{{"rewrite": "<query rewritten for keyword+semantic search, keep IDs/filenames verbatim>",
  "subqueries": [{{"q": "<self-contained subquery>", "intent": "id_lookup|concept|cross_doc"}}],
  "doc_hints": ["<filename or doc-id mentioned or strongly implied>", ...]}}
Rules: 1-3 subqueries; use cross_doc when the query needs evidence from multiple \
documents (e.g. a test AND its spec); doc_hints only for explicit mentions, else [].
Query: {query}"""


async def llm_analyze(query: str, llm_func) -> QueryPlan | None:
    try:
        raw = await llm_func(_LLM_PROMPT.format(query=query))
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        d = json.loads(m.group(0))
        subs = [
            {"q": s.get("q", "").strip(), "intent": s.get("intent", "concept"), "mode": None}
            for s in d.get("subqueries", []) if s.get("q", "").strip()
        ][:3] or [{"q": query, "intent": "concept", "mode": None}]
        return QueryPlan(
            original=query,
            rewrite=(d.get("rewrite") or query).strip(),
            subqueries=subs,
            doc_hints=[h for h in d.get("doc_hints", []) if isinstance(h, str)][:4],
            source="llm",
        )
    except Exception:
        return None


# ── analyzer facade ───────────────────────────────────────────────────────────

class QueryAnalyzer:
    def __init__(self, working_dir: str, llm_func=None):
        self.llm_func = llm_func
        self.memory = StrategyMemory(working_dir)
        self.cache = PlanCache(working_dir)

    def needs_background_analysis(self, query: str) -> bool:
        """Marks queries worth a BACKGROUND LLM analysis pass (v5.15: the hot path
        never waits on an LLM — uniform latency; analysis results land in the
        PlanCache and pay off on the NEXT similar query).
          - complex queries: long, multi-intent — UNLESS the skeleton has learned
            that LLM plans don't beat rules for this query shape (② worth gate)
          - chronically low-quality skeletons: every tried mode has a poor EMA"""
        skel = skeleton(query)
        if not self.memory.llm_adds_value(skel):
            return False
        parts = [p for p in _CONJ_SPLIT.split(query) if len(p.strip()) >= 8]
        if len(query) >= 80 or len(parts) >= 2:
            return True
        stats = self.memory._data.get(skel)
        if stats:
            tried = [s for s in stats.values() if s["n"] >= 2]
            if tried and all(s["ema"] < 0.45 for s in tried):
                return True
        return False

    def _finalize(self, plan: QueryPlan) -> QueryPlan:
        # v5.17: single-facet typed queries get a deterministic complementary
        # facet (test↔spec/issue) — the implicit co-retrieval intent made explicit
        if len(plan.subqueries) == 1:
            comp = complementary_facet(plan.original)
            if comp:
                plan.subqueries.append(comp)
        # rule validation: never let an ID-bearing subquery lose its lexical route
        for sub in plan.subqueries:
            if _ID_TOKEN.search(sub["q"]) and sub["intent"] != "id_lookup":
                sub["intent"] = "id_lookup"
        # mode consensus: intent candidates × strategy bandit, keyed by SHAPE (①)
        skel = skeleton(plan.original)
        for sub in plan.subqueries:
            if sub.get("_complement"):
                # synthetic facet text is never in the LLM keyword-extraction
                # cache — graph modes would stall the hot path. Lexical only.
                sub["mode"] = "hybrid"
                continue
            candidates = INTENT_MODES.get(sub["intent"], INTENT_MODES["concept"])
            sub["mode"] = self.memory.select(skel, candidates)
        return plan

    async def analyze(self, query: str, min_cache_quality: float | None = None) -> QueryPlan:
        """Hot path: cache hit or rules. NEVER an LLM call (uniform latency)."""
        plan = self.cache.get(query, min_quality=min_cache_quality)
        if plan is not None:
            return plan
        return self._finalize(rule_plan(query))

    async def llm_plan(self, query: str) -> QueryPlan | None:
        """Background-only: full LLM analysis, finalized like any other plan.
        Called by the evolver's shadow planner, never from the query path."""
        if self.llm_func is None:
            return None
        plan = await llm_analyze(query, self.llm_func)
        if plan is None:
            return None
        return self._finalize(plan)

    def feedback(self, query: str, plan: QueryPlan, quality: float,
                 promote_gate: float | None = None, promote_floor: float | None = None):
        """Post-retrieval learning: shape-keyed bandit + source-value + promotion."""
        skel = skeleton(query)
        for sub in plan.subqueries:
            if sub.get("mode"):
                self.memory.update(skel, sub["mode"], quality)
        self.memory.update_source(skel, plan.origin or "rules", quality)
        self.cache.promote(query, plan, quality, gate=promote_gate, floor=promote_floor)

    def save(self):
        self.memory.save()
        self.cache.save()
