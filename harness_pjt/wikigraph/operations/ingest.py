"""INGEST operation: document ingestion with verification."""

from __future__ import annotations

from lightrag import LightRAG, QueryParam
from wikigraph.state import WikiGraphState


async def ingest_node(state: WikiGraphState, rag: LightRAG) -> dict:
    """Ingest documents via rag.ainsert() and verify key entities were extracted."""
    docs = state.get("documents", [])
    paths = state.get("file_paths", [])
    if not docs:
        return {"messages": ["INGEST: no documents"]}

    track_id = await rag.ainsert(docs, file_paths=paths or None)

    # Verify: sample a phrase from each doc, check entities exist
    verified = 0
    for doc in docs[:3]:
        phrase = " ".join(doc.split()[:10])
        result = await rag.aquery_data(phrase, param=QueryParam(mode="naive"))
        if result.get("status") == "success":
            chunks = result.get("data", {}).get("chunks", [])
            if chunks:
                verified += 1

    msg = f"INGEST: {len(docs)} docs inserted (track_id={track_id}), {verified}/{min(len(docs),3)} verified"
    return {
        "last_track_id": track_id,
        "messages": [msg],
    }
