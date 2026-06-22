"""Source collection utilities for easy document ingestion."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def collect_files(
    directory: str, extensions: tuple[str, ...] = (".md", ".txt", ".pdf")
) -> list[tuple[str, str]]:
    """Scan directory and return [(filepath, content)] for supported files."""
    results = []
    for root, _, files in os.walk(directory):
        for fname in sorted(files):
            if fname.endswith(extensions):
                path = os.path.join(root, fname)
                with open(path, "r", errors="ignore") as f:
                    results.append((fname, f.read()))
    return results


def content_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def detect_changed_files(
    directory: str,
    hash_store_path: str,
    extensions: tuple[str, ...] = (".md", ".txt"),
) -> list[tuple[str, str]]:
    """Return only files whose content changed since last scan."""
    import json

    old_hashes: dict[str, str] = {}
    if os.path.exists(hash_store_path):
        with open(hash_store_path, "r") as f:
            old_hashes = json.load(f)

    new_hashes: dict[str, str] = {}
    changed: list[tuple[str, str]] = []

    for root, _, files in os.walk(directory):
        for fname in sorted(files):
            if not fname.endswith(extensions):
                continue
            path = os.path.join(root, fname)
            with open(path, "r", errors="ignore") as f:
                text = f.read()
            h = content_hash(text)
            new_hashes[path] = h
            if old_hashes.get(path) != h:
                changed.append((fname, text))

    with open(hash_store_path, "w") as f:
        json.dump(new_hashes, f)

    return changed
