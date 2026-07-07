"""WikiGraph Agent configuration.

Thresholds are grouped by the three categories described in
harness_pjt/README.md: 쿼리분석 및 전략선택·유저응답, 로깅 및 로그분석, 그래프 개선
(log-driven mutation + structural mining, merged into one category/module —
see evolve.py), plus linting (a manual maintenance tool, not part of the
three).
"""

from dataclasses import dataclass


@dataclass
class WikiGraphConfig:
    working_dir: str = "/tmp/wikigraph_demo"

    # --- 1. 쿼리분석 및 전략선택, 유저응답 (ANALYZE / RESPOND) ---
    query_decompose_max_subqueries: int = 3
    # RESPOND scopes to a document's entities (via ll_keywords) when ANALYZE
    # flags mentioned_docs and structural mining has mined that doc node.
    doc_scope_max_entities: int = 30

    # --- 2. 로깅 및 로그분석 (EVALUATE, informational quality score, no LLM call) ---
    quality_evolve_threshold: float = 0.5
    gap_quality_threshold: float = 0.3

    # --- 3. 그래프 개선, tier 1: per-query background pass (log-driven) ---
    co_retrieval_min_count: int = 3
    shortcut_path_min_count: int = 3
    evolve_max_mutations: int = 10
    inferred_edge_weight: float = 0.5

    # --- 3. 그래프 개선, tier 2: batch/comprehensive pass (log-driven) ---
    # Fires every N queries, re-scans the accumulated query log with wider
    # scope, and runs the corpus-wide checks (source verification,
    # contradiction resolution) that are too costly to run on every query.
    batch_evolve_interval: int = 50
    batch_evolve_max_mutations: int = 30

    # --- 3. 그래프 개선, structural mining (doc-doc, chunk-chunk) ---
    # Runs alongside tier 2, since it needs the same wide, corpus-level scope.
    structural_doc_shared_entity_min: int = 2
    structural_chunk_adjacency_max_gap: int = 1
    structural_max_mutations: int = 20
    structural_edge_weight: float = 0.3

    # --- Linting ---
    hub_degree_threshold: int = 50
    duplicate_similarity_threshold: float = 0.95
    stale_query_threshold: int = 20

    # Query log
    max_query_log_size: int = 1000
