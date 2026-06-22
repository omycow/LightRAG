"""WikiGraph Agent configuration."""

from dataclasses import dataclass, field


@dataclass
class WikiGraphConfig:
    working_dir: str = "/tmp/wikigraph_demo"

    # EVOLVE thresholds
    co_retrieval_min_count: int = 3
    gap_quality_threshold: float = 0.3
    shortcut_path_min_count: int = 3
    evolve_max_mutations: int = 10
    inferred_edge_weight: float = 0.5

    # LINT thresholds
    hub_degree_threshold: int = 50
    duplicate_similarity_threshold: float = 0.95
    stale_query_threshold: int = 20

    # EVALUATE
    quality_evolve_threshold: float = 0.5

    # Auto-evolve: run EVOLVE every N queries regardless of quality
    auto_evolve_interval: int = 5

    # Query log
    max_query_log_size: int = 1000
