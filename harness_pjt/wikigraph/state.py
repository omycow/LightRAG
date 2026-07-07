"""LangGraph state schema for WikiGraph Agent."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Optional, TypedDict


@dataclass
class QueryLogEntry:
    query: str
    parent_query: str
    timestamp: str
    retrieved_entities: list[str]
    retrieved_relations: list[tuple[str, str]]
    retrieved_chunks: list[str]
    result_quality: Optional[float] = None
    mode: str = "mix"


@dataclass
class EntityMeta:
    entity_name: str
    access_count: int = 0
    last_accessed: Optional[str] = None
    confidence: float = 1.0
    evolution_count: int = 0


@dataclass
class LintFinding:
    finding_type: str  # orphan, hub_overload, duplicate, stale, contradiction, stale_evolved_edge
    severity: str  # info, warning, critical
    entity_name: str
    details: str
    suggested_action: str  # delete, split, merge, update, investigate, deleted
    auto_fixable: bool = False


class WikiGraphState(TypedDict, total=False):
    operation: Literal["ingest", "query", "evolve", "lint", "done"]
    messages: Annotated[list[str], operator.add]

    # INGEST
    documents: list[str]
    file_paths: list[str]
    last_track_id: Optional[str]

    # ANALYZE (query improvement) — runs inline, feeds both paths below
    current_query: Optional[str]
    optimized_query: Optional[str]
    sub_queries: list[str]
    mentioned_docs: list[str]

    # Response path (foreground, returned to the caller immediately)
    final_answer: Optional[str]

    # Evolving path (background): per-sub-query retrieve + analysis
    sub_query_results: list[dict[str, Any]]
    query_result: Optional[dict[str, Any]]

    # Knowledge-graph improvement (tier 1 + tier 2)
    evolve_mutations: list[dict[str, Any]]
    evolve_applied: int
    batch_triggered: bool
    batch_evolve_mutations: list[dict[str, Any]]
    batch_evolve_applied: int

    # Structural-relation improvement
    structural_mutations: list[dict[str, Any]]
    structural_applied: int

    # LINT
    lint_findings: list[dict[str, Any]]
    lint_fixes: int

    # Persistent state
    query_log: list[dict[str, Any]]
    entity_metadata: dict[str, dict[str, Any]]

    # Control
    should_evolve: bool
