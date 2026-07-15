"""Batch ingest all shs-poc-ragflow data files into LightRAG with progress tracking."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmark.extract import extract_text, find_all_data_files

PROGRESS_FILE = "ingest_progress.json"


def load_progress(progress_path: str) -> dict:
    if os.path.exists(progress_path):
        try:
            return json.loads(Path(progress_path).read_text())
        except Exception:
            pass
    return {"ingested": [], "failed": [], "skipped": []}


def save_progress(progress_path: str, progress: dict) -> None:
    Path(progress_path).write_text(json.dumps(progress, indent=2, ensure_ascii=False))


def prioritize_files(files: list[tuple[str, str]], data_root: str) -> list[tuple[str, str]]:
    """Sort files: text-first (HTML, MD, CSV), then office (xlsx, docx, pptx).

    Within text-first, put validation cards and golden cards first
    since they're needed for the validation benchmark.
    """
    def priority(item):
        _, rel = item
        # Priority 0: validation cards (180 files, small MD)
        if "validation/cards/" in rel:
            return 0
        # Priority 1: project golden cards (68 files, small MD)
        if "project/" in rel and "/cards/" in rel:
            return 1
        # Priority 2: official specs (12 files, MD)
        if "specs/official_specs/" in rel:
            return 2
        # Priority 3: confluence HTML (72 files, key for graph build)
        if "confluence/" in rel:
            return 3
        # Priority 4: jira HTML (81 files)
        if "jira/" in rel:
            return 4
        # Priority 5: internal specs (40 files, docx/pptx)
        if "specs/internal_specs/" in rel:
            return 5
        # Priority 6: office excel (30 files)
        if "ms_office/excel/" in rel:
            return 6
        # Priority 7: office ppt (40 files)
        if "ms_office/ppt/" in rel:
            return 7
        # Priority 8: office word (40 files)
        if "ms_office/word/" in rel:
            return 8
        return 9

    return sorted(files, key=priority)


async def ingest_all(
    rag,
    data_root: str,
    progress_path: str = PROGRESS_FILE,
    batch_size: int = int(os.environ.get("INGEST_BATCH_SIZE", "8")),
    max_text_len: int = 50_000,
    verbose: bool = True,
) -> dict:
    """Ingest all data files into LightRAG.

    Args:
        rag: LightRAG instance (already initialized)
        data_root: path to shs-poc-ragflow/data/
        progress_path: JSON file to track progress (for resume on restart)
        batch_size: documents per ainsert batch
        max_text_len: truncate very long documents to avoid token limits
        verbose: print progress

    Returns:
        Summary dict with counts.
    """
    progress = load_progress(progress_path)
    ingested_set = set(progress["ingested"])
    failed_set = set(progress["failed"])

    all_files = find_all_data_files(data_root)
    all_files = prioritize_files(all_files, data_root)

    remaining = [(abs_p, rel) for abs_p, rel in all_files
                 if rel not in ingested_set and rel not in failed_set]

    if verbose:
        print(f"[ingest] Total files: {len(all_files)}, "
              f"remaining: {len(remaining)}, "
              f"already done: {len(ingested_set)}, "
              f"failed: {len(failed_set)}")

    new_ingested = 0
    new_failed = 0

    # Process in batches
    for batch_start in range(0, len(remaining), batch_size):
        batch = remaining[batch_start: batch_start + batch_size]
        texts = []
        paths = []
        batch_rels = []

        for abs_path, rel_path in batch:
            try:
                text = extract_text(abs_path)
                if not text.strip() or len(text.strip()) < 20:
                    if verbose:
                        print(f"  [SKIP] {rel_path} (empty or too short)")
                    progress["skipped"].append(rel_path)
                    continue
                # Truncate very long documents
                if len(text) > max_text_len:
                    text = text[:max_text_len] + "\n\n[... truncated for token limits]"
                texts.append(text)
                paths.append(rel_path)
                batch_rels.append(rel_path)
            except Exception as e:
                if verbose:
                    print(f"  [ERROR] extract {rel_path}: {e}")
                progress["failed"].append(rel_path)
                failed_set.add(rel_path)
                new_failed += 1

        if not texts:
            continue

        try:
            t0 = time.time()
            await rag.ainsert(texts, file_paths=paths)
            elapsed = time.time() - t0

            for rel in batch_rels:
                progress["ingested"].append(rel)
                ingested_set.add(rel)
                new_ingested += 1

            total_done = len(ingested_set)
            total_all = len(all_files)
            pct = 100 * total_done / max(1, total_all)
            if verbose:
                names = [os.path.basename(p) for p in paths]
                print(f"  [OK {pct:.1f}%] {names} ({elapsed:.1f}s)")

        except Exception as e:
            if verbose:
                print(f"  [FAIL] batch {batch_rels}: {e}")
            for rel in batch_rels:
                progress["failed"].append(rel)
                failed_set.add(rel)
                new_failed += 1

        save_progress(progress_path, progress)

    if verbose:
        print(f"\n[ingest] Done. new_ingested={new_ingested}, new_failed={new_failed}")
        print(f"[ingest] Total ingested={len(ingested_set)}, failed={len(failed_set)}")

    return {
        "total_files": len(all_files),
        "total_ingested": len(ingested_set),
        "total_failed": len(failed_set),
        "new_ingested": new_ingested,
        "new_failed": new_failed,
    }


async def ingest_priority_subset(
    rag,
    data_root: str,
    progress_path: str = PROGRESS_FILE,
    batch_size: int = 8,
    max_text_len: int = 50_000,
    verbose: bool = True,
) -> dict:
    """Ingest only the files referenced in benchmark questions (fast subset).

    Uses batch ingest with progress tracking so it can resume on restart.
    """
    from benchmark.questions import (
        load_all_data_questions,
        load_validation_set_a,
        load_validation_set_b,
    )

    # Collect all expected sources from all question sets
    needed_sources: set[str] = set()
    for q in load_all_data_questions(data_root):
        needed_sources.update(q.get("expected_sources", []))
    for q in load_validation_set_a(data_root):
        needed_sources.update(q.get("expected_sources", []))
    for q in load_validation_set_b(data_root):
        needed_sources.update(q.get("expected_sources", []))

    data_root_path = Path(data_root)
    progress = load_progress(progress_path)
    ingested_set = set(progress["ingested"])
    failed_set = set(progress["failed"])

    # Resolve files
    all_rels = sorted(needed_sources)
    to_ingest = []
    missing = []

    for rel in all_rels:
        abs_path = data_root_path / rel
        if not abs_path.exists():
            missing.append(rel)
            continue
        if rel in ingested_set or rel in failed_set:
            continue
        to_ingest.append((str(abs_path), rel))

    if verbose:
        print(f"[priority ingest] needed={len(needed_sources)}, to_ingest={len(to_ingest)}, "
              f"already_done={len(ingested_set)}, missing={len(missing)}")
        if missing:
            print(f"  Missing files: {missing[:10]}")

    new_ingested = 0
    new_failed = 0

    for batch_start in range(0, len(to_ingest), batch_size):
        batch = to_ingest[batch_start: batch_start + batch_size]
        texts, paths, batch_rels = [], [], []

        for abs_path, rel_path in batch:
            try:
                text = extract_text(abs_path)
                if not text.strip() or len(text.strip()) < 20:
                    progress["skipped"].append(rel_path)
                    continue
                texts.append(text[:max_text_len])
                paths.append(rel_path)
                batch_rels.append(rel_path)
            except Exception as e:
                if verbose:
                    print(f"  [ERROR] extract {rel_path}: {e}")
                progress["failed"].append(rel_path)
                failed_set.add(rel_path)
                new_failed += 1

        if not texts:
            continue

        try:
            t0 = time.time()
            await rag.ainsert(texts, file_paths=paths)
            elapsed = time.time() - t0
            for rel in batch_rels:
                progress["ingested"].append(rel)
                ingested_set.add(rel)
                new_ingested += 1
            done = len(ingested_set)
            total = len(all_rels)
            if verbose:
                names = [os.path.basename(p) for p in paths]
                print(f"  [OK {done}/{total}] {names} ({elapsed:.1f}s)")
        except Exception as e:
            if verbose:
                print(f"  [FAIL] {batch_rels}: {e}")
            for rel in batch_rels:
                progress["failed"].append(rel)
                failed_set.add(rel)
                new_failed += 1

        save_progress(progress_path, progress)

    if verbose:
        print(f"\n[priority ingest] Done. new_ingested={new_ingested}, new_failed={new_failed}")
        print(f"[priority ingest] Total ingested={len(ingested_set)}, missing={len(missing)}")

    return {"ingested": len(ingested_set), "missing": len(missing), "failed": len(failed_set)}
