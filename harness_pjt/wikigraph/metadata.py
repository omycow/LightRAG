"""JSON-based persistence for query logs and entity metadata."""

from __future__ import annotations

import json
import os
from dataclasses import asdict

from wikigraph.state import EntityMeta, QueryLogEntry


class MetadataStore:
    def __init__(self, working_dir: str, max_log_size: int = 1000):
        self.meta_dir = os.path.join(working_dir, "wikigraph_meta")
        os.makedirs(self.meta_dir, exist_ok=True)
        self._entity_path = os.path.join(self.meta_dir, "entity_metadata.json")
        self._log_path = os.path.join(self.meta_dir, "query_log.json")
        self._max_log_size = max_log_size

    def save_entity_metadata(self, metadata: dict[str, EntityMeta]) -> None:
        data = {k: asdict(v) for k, v in metadata.items()}
        self._atomic_write(self._entity_path, data)

    def load_entity_metadata(self) -> dict[str, EntityMeta]:
        raw = self._read_json(self._entity_path, {})
        return {k: EntityMeta(**v) for k, v in raw.items()}

    def save_query_log(self, entries: list[QueryLogEntry]) -> None:
        data = [asdict(e) for e in entries[-self._max_log_size :]]
        self._atomic_write(self._log_path, data)

    def load_query_log(self) -> list[QueryLogEntry]:
        raw = self._read_json(self._log_path, [])
        return [QueryLogEntry(**e) for e in raw]

    def _atomic_write(self, path: str, data) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def _read_json(self, path: str, default):
        if not os.path.exists(path):
            return default
        with open(path, "r") as f:
            return json.load(f)
