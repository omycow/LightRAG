"""WikiGraph Agent configuration.

Thresholds are grouped by the four evolution pillars described in
harness_pjt/README.md: query improvement, knowledge-graph improvement,
structural-relation improvement, and linting.
"""

from dataclasses import dataclass


@dataclass
class WikiGraphConfig:
    working_dir: str = "/tmp/wikigraph_demo"

    # --- Query improvement (ANALYZE) ---
    query_decompose_max_subqueries: int = 3

    # --- Knowledge-graph improvement, tier 1: per-query background pass ---
    co_retrieval_min_count: int = 3
    gap_quality_threshold: float = 0.3
    shortcut_path_min_count: int = 3
    evolve_max_mutations: int = 10
    inferred_edge_weight: float = 0.5

    # --- Knowledge-graph improvement, tier 2: batch/comprehensive pass ---
    # Fires every N queries, re-scans the accumulated query log with wider
    # scope, and runs the corpus-wide checks (source verification,
    # contradiction resolution) that are too costly to run on every query.
    batch_evolve_interval: int = 50
    batch_evolve_max_mutations: int = 30

    # --- Structural-relation improvement (doc-doc, chunk-chunk) ---
    # Runs alongside tier 2, since it needs the same wide, corpus-level scope.
    structural_doc_shared_entity_min: int = 2
    structural_chunk_adjacency_max_gap: int = 1
    structural_max_mutations: int = 20
    structural_edge_weight: float = 0.3

    # --- Linting ---
    hub_degree_threshold: int = 50
    duplicate_similarity_threshold: float = 0.95
    stale_query_threshold: int = 20

    # --- Evaluate (informational quality score, no LLM call) ---
    quality_evolve_threshold: float = 0.5

    # Query log
    max_query_log_size: int = 1000
