"""
Deterministic retrieval-quality scoring — no LLM, benchmark-answer-free.

Post-retrieval Query Performance Prediction (QPP) signals, adapted from IR
literature (research_notes.md §3):

  sim_strength   NQC/WIG-flavoured: top-1 vector similarity and mean of top-k.
                 Strong, tightly-grouped top scores → confident retrieval.
  fusion_agree   Overlap between independent BM25 and vector top-k lists.
                 Two unrelated rankers agreeing is strong evidence of relevance.
  term_coverage  Fraction of salient query terms present in the top chunk texts.
  scope_align    Fraction of top docs inside the structure-graph scope (when given).

Composite quality ∈ [0,1] with fixed documented weights. Queries below
ATTENTION_THRESHOLD go to the evolver's attention queue.
"""

from __future__ import annotations

import os
import re

WEIGHTS = {
    "sim_strength": 0.30,
    "fusion_agree": 0.25,
    "term_coverage": 0.30,
    "scope_align": 0.15,
}
ATTENTION_THRESHOLD = 0.45   # absolute fallback; adaptive gates below are primary
REINFORCE_GATE = 0.60        # absolute fallback for L2 reinforcement


class RunningQuality:
    """
    Self-calibrating quality gates from the recent quality distribution.

    Absolute thresholds don't transfer across corpora/embedders (the score scale
    shifts), so gates are percentiles of a sliding window:
      reinforce_gate = p60 (structure learns from the better ~40% of retrievals)
      promote_gate   = p70 (plan cache keeps only clearly-good plans)
      attention_gate = p30 (worst ~30% go to the evolver's attention queue)
    Floors keep early/degenerate windows sane.
    """

    def __init__(self, working_dir: str, window: int = 200):
        import json as _json
        import os as _os
        meta = _os.path.join(working_dir, "structrag_meta")
        _os.makedirs(meta, exist_ok=True)
        self._path = _os.path.join(meta, "quality_stats.json")
        self.window = window
        try:
            self.values = _json.load(open(self._path, encoding="utf-8"))[-window:]
        except (OSError, ValueError):
            self.values = []

    def add(self, q: float):
        self.values.append(round(q, 4))
        self.values = self.values[-self.window:]

    def save(self):
        import json as _json
        _json.dump(self.values, open(self._path, "w", encoding="utf-8"))

    def _pct(self, p: float, default: float) -> float:
        if len(self.values) < 20:
            return default
        s = sorted(self.values)
        return s[min(len(s) - 1, int(p * len(s)))]

    @property
    def reinforce_gate(self) -> float:
        return max(0.35, self._pct(0.60, REINFORCE_GATE))

    @property
    def promote_gate(self) -> float:
        return max(0.40, self._pct(0.70, 0.65))

    @property
    def attention_gate(self) -> float:
        return min(0.45, self._pct(0.30, ATTENTION_THRESHOLD))

_STOP = {"the", "and", "for", "that", "this", "are", "was", "what", "how", "why",
         "when", "where", "which", "with", "from", "have", "has", "test", "code",
         "있나요", "있는지", "관련", "대한", "어떤", "무엇", "테스트", "코드"}

# Common Korean particles glued onto content words ("Hub의", "엔진으로") — strip for
# matching so coverage measures the content term, not the morphology.
_KO_PARTICLE = re.compile(r"(?:의|을|를|이|가|은|는|에|에서|으로|로|와|과|도|만)$")


def salient_terms(query: str) -> list[str]:
    terms = re.findall(r"[A-Za-z0-9가-힣_\-]{3,}", query)
    out = []
    for t in terms:
        t = t.lower()
        if re.search(r"[가-힣]", t):
            t = _KO_PARTICLE.sub("", t)
        if len(t) >= 3 and t not in _STOP:
            out.append(t)
    return out


def _term_in(term: str, blob: str) -> bool:
    """Literal match, falling back to a stem prefix for long/inflected tokens."""
    if term in blob:
        return True
    return len(term) >= 5 and term[:4] in blob


def score_retrieval(
    query: str,
    chunks: list[dict],
    vector_sims: list[float] | None = None,
    bm25_ids: list[str] | None = None,
    vector_ids: list[str] | None = None,
    scope: dict[str, float] | None = None,
    k: int = 10,
    chunk_docs=None,
) -> dict:
    """
    Args:
      chunks:      fused final chunk dicts (need "content", "file_path")
      vector_sims: cosine similarities of the vector ranking (normalized embeddings)
      bm25_ids / vector_ids: top-k chunk ids of each independent ranking
      scope:       {doc_basename: confidence} from the structure graph, optional
      chunk_docs:  callable chunk_id → doc basename (for doc-level fusion agreement)
    Returns: {"quality": float, "signals": {...}}
    """
    signals: dict[str, float] = {}

    if not chunks:
        return {"quality": 0.0, "signals": {"empty": 1.0}}

    # sim_strength — NQC/WIG flavour on the vector ranking. MiniLM cosine sims for
    # relevant matches live in ~[0.3, 0.8]; rescale that band to [0,1].
    if vector_sims:
        top = sorted(vector_sims, reverse=True)[:k]
        rescale = lambda s: max(0.0, min(1.0, (s - 0.3) / 0.5))
        top1 = rescale(top[0])
        mean_k = sum(rescale(s) for s in top) / len(top)
        signals["sim_strength"] = 0.5 * top1 + 0.5 * mean_k

    # fusion_agree — doc-level overlap coefficient of two independent rankers.
    # Chunk-level Jaccard is misleadingly low because BM25 and dense retrievers are
    # complementary by design; agreeing on the same *documents* is the real signal.
    if bm25_ids and vector_ids and chunk_docs is not None:
        a = {chunk_docs(c) for c in bm25_ids[:k] if chunk_docs(c)}
        b = {chunk_docs(c) for c in vector_ids[:k] if chunk_docs(c)}
        if a and b:
            signals["fusion_agree"] = len(a & b) / min(len(a), len(b))

    # term_coverage — salient query terms found in top chunk texts (stem-tolerant)
    terms = salient_terms(query)
    if terms:
        blob = " ".join(c.get("content", "")[:2000] for c in chunks[:5]).lower()
        signals["term_coverage"] = sum(1 for t in set(terms) if _term_in(t, blob)) / len(set(terms))

    # scope_align — top docs inside the SG scope
    if scope:
        top_docs = [os.path.basename(c.get("file_path", "")) for c in chunks[:k]]
        top_docs = [d for d in top_docs if d]
        if top_docs:
            signals["scope_align"] = sum(1 for d in top_docs if d in scope) / len(top_docs)

    # weighted composite over available signals (renormalize missing weights)
    total_w = sum(WEIGHTS[s] for s in signals if s in WEIGHTS)
    if total_w == 0:
        return {"quality": 0.0, "signals": signals}
    quality = sum(signals[s] * WEIGHTS[s] for s in signals if s in WEIGHTS) / total_w
    return {"quality": round(quality, 4), "signals": {n: round(v, 4) for n, v in signals.items()}}
