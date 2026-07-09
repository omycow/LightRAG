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
    source: str = "rules"  # "cache" | "llm" | "rules"

    def to_dict(self) -> dict:
        return {"original": self.original, "rewrite": self.rewrite,
                "subqueries": self.subqueries, "doc_hints": self.doc_hints,
                "source": self.source}

    @staticmethod
    def from_dict(d: dict) -> "QueryPlan":
        return QueryPlan(d["original"], d["rewrite"], d["subqueries"],
                         d.get("doc_hints", []), d.get("source", "cache"))


def fingerprint(query: str) -> str:
    """Stable keyword fingerprint (same scheme as wikigraph.qsm, +Korean tokens)."""
    words = sorted(set(
        w.lower() for w in re.findall(r"[\w가-힣]{3,}", query)
        if w.lower() not in {"the", "and", "for", "that", "this", "are", "was",
                             "what", "how", "why", "when", "where", "which"}
    ))
    return " ".join(words[:12])


class StrategyMemory:
    """Per-query-cluster mode quality EMA with ε-greedy exploration."""

    def __init__(self, working_dir: str, epsilon: float = 0.1, alpha: float = 0.3):
        meta = os.path.join(working_dir, "structrag_meta")
        os.makedirs(meta, exist_ok=True)
        self._path = os.path.join(meta, "strategy_memory.json")
        self.epsilon = epsilon
        self.alpha = alpha
        try:
            self._data = json.load(open(self._path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._data = {}

    def save(self):
        json.dump(self._data, open(self._path, "w", encoding="utf-8"), ensure_ascii=False)

    def select(self, cluster: str, candidates: list[str]) -> str:
        """Best learned mode among candidates, ε-greedy; falls back to first candidate."""
        stats = self._data.get(cluster, {})
        if random.random() < self.epsilon and len(candidates) > 1:
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

    def get(self, query: str) -> QueryPlan | None:
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
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        plan = QueryPlan.from_dict(entry["plan"])
        plan.original = query
        plan.source = "cache"
        return plan

    def promote(self, query: str, plan: QueryPlan, quality: float, gate: float | None = None):
        if quality < (gate if gate is not None else self.PROMOTE_QUALITY):
            return
        fp = fingerprint(query)
        cur = self._data.get(fp)
        if cur is None or quality >= cur["quality"]:
            self._data[fp] = {"plan": plan.to_dict(), "quality": quality, "ts": time.time()}

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

    def _needs_llm(self, query: str, cluster: str) -> bool:
        """LLM escalation is signal-driven, not unconditional (user requirement:
        the analyzer judges from quality/complexity when LLM help is worth it).
          - complex queries: long, multi-intent, or multiple question marks
          - chronically low-quality clusters: every tried mode has a poor EMA
        Simple ID/keyword lookups stay on the fast rule path."""
        parts = [p for p in _CONJ_SPLIT.split(query) if len(p.strip()) >= 8]
        if len(query) >= 80 or len(parts) >= 2:
            return True
        stats = self.memory._data.get(cluster)
        if stats:
            tried = [s for s in stats.values() if s["n"] >= 2]
            if tried and all(s["ema"] < 0.45 for s in tried):
                return True
        return False

    async def analyze(self, query: str) -> QueryPlan:
        # Tier 0
        plan = self.cache.get(query)
        if plan is not None:
            return plan

        # Tier 1 (LLM, signal-gated) with rule fallback
        plan = None
        cluster = fingerprint(query)
        if self.llm_func is not None and self._needs_llm(query, cluster):
            plan = await llm_analyze(query, self.llm_func)
        if plan is None:
            plan = rule_plan(query)

        # rule validation: never let an ID-bearing subquery lose its lexical route
        for sub in plan.subqueries:
            if _ID_TOKEN.search(sub["q"]) and sub["intent"] != "id_lookup":
                sub["intent"] = "id_lookup"

        # mode consensus: intent candidates filtered through the strategy bandit
        for sub in plan.subqueries:
            candidates = INTENT_MODES.get(sub["intent"], INTENT_MODES["concept"])
            sub["mode"] = self.memory.select(cluster, candidates)
        return plan

    def feedback(self, query: str, plan: QueryPlan, quality: float,
                 promote_gate: float | None = None):
        """Post-retrieval learning: bandit update + plan promotion."""
        cluster = fingerprint(query)
        for sub in plan.subqueries:
            if sub.get("mode"):
                self.memory.update(cluster, sub["mode"], quality)
        self.cache.promote(query, plan, quality, gate=promote_gate)

    def save(self):
        self.memory.save()
        self.cache.save()
