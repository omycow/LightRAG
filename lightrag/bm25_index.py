"""BM25 keyword search index and RRF fusion utilities for hybrid retrieval."""

from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

from lightrag.utils import logger


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


class BM25Index:
    """In-memory BM25 index for keyword search."""

    def __init__(self) -> None:
        self.corpus_ids: list[str] = []
        self.bm25: BM25Okapi | None = None

    def build(self, documents: dict[str, str]) -> None:
        """Build index from {id: text} mapping."""
        if not documents:
            self.corpus_ids = []
            self.bm25 = None
            return
        self.corpus_ids = list(documents.keys())
        tokenized = [_tokenize(doc) for doc in documents.values()]
        self.bm25 = BM25Okapi(tokenized)
        logger.info(f"BM25 index built with {len(self.corpus_ids)} documents")

    def query(self, query_text: str, top_k: int = 20) -> list[dict]:
        """BM25 search returning [{id, score}, ...]."""
        if not self.bm25 or not self.corpus_ids:
            return []
        tokens = _tokenize(query_text)
        if not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        top_indices = scores.argsort()[-top_k:][::-1]
        return [
            {"id": self.corpus_ids[i], "score": float(scores[i])}
            for i in top_indices
            if scores[i] > 0
        ]

    @property
    def is_built(self) -> bool:
        return self.bm25 is not None and len(self.corpus_ids) > 0


def reciprocal_rank_fusion(
    vector_results: list[dict],
    bm25_results: list[dict],
    k: int = 60,
    vector_id_field: str = "id",
    bm25_id_field: str = "id",
) -> list[dict]:
    """Merge vector + BM25 results using Reciprocal Rank Fusion.

    RRF(d) = Σ 1/(k + rank_r(d)), k=60 by default.
    """
    rrf_scores: dict[str, float] = {}
    result_map: dict[str, dict] = {}

    for rank, item in enumerate(vector_results, 1):
        doc_id = str(item.get(vector_id_field, ""))
        if not doc_id:
            continue
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + 1.0 / (k + rank)
        result_map[doc_id] = item

    for rank, item in enumerate(bm25_results, 1):
        doc_id = str(item.get(bm25_id_field, ""))
        if not doc_id:
            continue
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + 1.0 / (k + rank)
        if doc_id not in result_map:
            result_map[doc_id] = item

    sorted_ids = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)
    return [result_map[did] for did in sorted_ids]
