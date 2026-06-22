"""LINT operation: graph health checks using graph algorithms."""

from __future__ import annotations

from dataclasses import asdict

import numpy as np

from lightrag import LightRAG
from wikigraph.config import WikiGraphConfig
from wikigraph.state import LintFinding, WikiGraphState


async def lint_node(
    state: WikiGraphState,
    rag: LightRAG,
    config: WikiGraphConfig,
) -> dict:
    """Run graph health checks. Mostly graph algorithms, no LLM calls."""
    graph = rag.chunk_entity_relation_graph
    findings: list[LintFinding] = []
    messages: list[str] = []

    all_labels = await graph.get_all_labels()
    messages.append(f"LINT: scanning {len(all_labels)} entities")

    # CHECK 1: Orphan nodes (degree == 0)
    orphan_count = 0
    for node_id in all_labels:
        degree = await graph.node_degree(node_id)
        if degree == 0:
            findings.append(LintFinding(
                finding_type="orphan",
                severity="warning",
                entity_name=node_id,
                details=f"Entity has no connections (degree=0)",
                suggested_action="delete",
                auto_fixable=True,
            ))
            orphan_count += 1
    if orphan_count:
        messages.append(f"LINT: {orphan_count} orphan node(s) found")

    # CHECK 2: Hub overload (degree > threshold)
    hub_count = 0
    for node_id in all_labels:
        degree = await graph.node_degree(node_id)
        if degree > config.hub_degree_threshold:
            findings.append(LintFinding(
                finding_type="hub_overload",
                severity="warning",
                entity_name=node_id,
                details=f"Entity has {degree} connections (threshold={config.hub_degree_threshold})",
                suggested_action="split",
            ))
            hub_count += 1
    if hub_count:
        messages.append(f"LINT: {hub_count} hub overload(s) found")

    # CHECK 3: Duplicate entities (embedding similarity > threshold)
    if len(all_labels) > 1 and hasattr(rag, "embedding_func"):
        try:
            # Pre-filter: only compare entities sharing a common token
            from collections import defaultdict
            token_to_entities: dict[str, list[str]] = defaultdict(list)
            for name in all_labels:
                for token in name.lower().split():
                    if len(token) > 2:
                        token_to_entities[token].append(name)

            candidate_pairs: set[tuple[str, str]] = set()
            for entities in token_to_entities.values():
                if 1 < len(entities) <= 20:
                    for i in range(len(entities)):
                        for j in range(i + 1, len(entities)):
                            candidate_pairs.add(
                                tuple(sorted((entities[i], entities[j])))
                            )

            if candidate_pairs:
                unique_names = list({n for pair in candidate_pairs for n in pair})
                embeddings = await rag.embedding_func(unique_names)
                embed_map = dict(zip(unique_names, embeddings))

                dup_count = 0
                for a, b in candidate_pairs:
                    ea, eb = embed_map.get(a), embed_map.get(b)
                    if ea is not None and eb is not None:
                        sim = float(np.dot(ea, eb) / (np.linalg.norm(ea) * np.linalg.norm(eb)))
                        if sim > config.duplicate_similarity_threshold:
                            findings.append(LintFinding(
                                finding_type="duplicate",
                                severity="warning",
                                entity_name=f"{a} / {b}",
                                details=f"Possible duplicates (similarity={sim:.3f})",
                                suggested_action="merge",
                            ))
                            dup_count += 1
                if dup_count:
                    messages.append(f"LINT: {dup_count} potential duplicate(s) found")
        except Exception as e:
            messages.append(f"LINT: duplicate check failed: {e}")

    # CHECK 4: Stale entities (not accessed in query log)
    entity_meta = state.get("entity_metadata", {})
    total_queries = len(state.get("query_log", []))
    stale_count = 0
    if total_queries > config.stale_query_threshold:
        for ename, meta in entity_meta.items():
            if meta.get("access_count", 0) < 2:
                findings.append(LintFinding(
                    finding_type="stale",
                    severity="info",
                    entity_name=ename,
                    details=f"Accessed {meta.get('access_count', 0)} times in {total_queries} queries",
                    suggested_action="investigate",
                ))
                stale_count += 1
    if stale_count:
        messages.append(f"LINT: {stale_count} stale entity(ies) found")

    if not findings:
        messages.append("LINT: no issues found")

    return {
        "lint_findings": [asdict(f) for f in findings],
        "lint_fixes": 0,
        "messages": messages,
    }
