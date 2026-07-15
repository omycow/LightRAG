"""Main benchmark runner for Evolving LightRAG vs shs-poc-ragflow corpus.

Scenarios:
  1. Initial state — vector vs BM25 vs hybrid source_hit comparison
  2. Post-EVOLVE — after N query cycles, measure evolution effect on hybrid source_hit

Usage:
    python benchmark/run_benchmark.py \
        --data-root ../../shs-poc-ragflow/data \
        --work-dir /tmp/wikigraph_benchmark \
        --scenario 1          # or 2 or all
        --ingest-mode full    # or priority (benchmark-referenced files only)

Outputs:
    benchmark_results_scenario1.md
    benchmark_results_scenario2.md
    benchmark_results_all.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # harness_pjt/
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # LightRAG/

LLM_PROVIDER  = os.environ.get("RAG_LLM", "qwen")              # "claude" | "claude-cli" | "groq" | "qwen"
LLM_BASE_URL  = os.environ.get("LLM_BASE_URL", "http://222.117.133.162:30010/v1")
LLM_MODEL     = os.environ.get("LLM_MODEL", "qwen-task-pool")
LLM_API_KEY   = os.environ.get("LLM_API_KEY", "asdf")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL  = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
CLAUDE_CLI_MODEL = os.environ.get("CLAUDE_CLI_MODEL", "claude-haiku-4-5-20251001")
GROQ_API_KEY  = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL    = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
EMBED_MODEL   = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM     = 384


class RateLimitError(RuntimeError):
    """Raised when the LLM API usage limit is hit. Carries optional retry_after (seconds)."""
    def __init__(self, message: str, retry_after: int = 0):
        super().__init__(message)
        self.retry_after = retry_after


# ── LightRAG setup ───────────────────────────────────────────────────────────

async def build_rag(
    work_dir: str,
    llm_provider: str = None,
    claude_model: str = None,
    kg_chunk_pick_method: str = "VECTOR",
    enable_llm_cache: bool = True,
):
    """Create and initialize a LightRAG instance.

    llm_provider: "claude" uses Anthropic API (fast); "qwen" uses Qwen (default).
    kg_chunk_pick_method: "VECTOR" (default, cosine-sim filter) or "WEIGHT" (rank-based, includes more KG-edge chunks).
    enable_llm_cache: set False to disable keyword-extraction caching (slower but ensures fresh results after EVOLVE).
    """
    from sentence_transformers import SentenceTransformer
    from lightrag import LightRAG
    from lightrag.utils import EmbeddingFunc

    provider = llm_provider or LLM_PROVIDER
    model = claude_model or CLAUDE_MODEL

    print(f"[rag] Loading embedding model {EMBED_MODEL}...")
    embed_model = SentenceTransformer(EMBED_MODEL)
    print("[rag] Embedding model ready.")

    if provider == "claude-cli":
        import signal as _signal
        print(f"[rag] LLM: Claude CLI ({CLAUDE_CLI_MODEL}) via subprocess — max-turns 1, no-agent mode")

        import re as _re

        async def llm_func(prompt, system_prompt=None, history_messages=None, **kwargs):
            import tempfile, signal as _sig
            combined = ""
            if system_prompt:
                # Strip ---SectionName--- markers: claude CLI treats them as
                # structured section headers and triggers agentic/tool behavior.
                # Replace with plain text equivalents so claude treats content as chat.
                cleaned_sp = _re.sub(r'^---\w+---\n?', '', system_prompt, flags=_re.MULTILINE)
                combined = f"{cleaned_sp.strip()}\n\n"
            # Same cleanup for user prompt
            cleaned_p = _re.sub(r'^---\w+---\n?', '', prompt, flags=_re.MULTILINE)
            combined += cleaned_p
            # Write to temp file; feed as direct stdin to avoid bash quoting issues
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False,
                                             encoding="utf-8") as f:
                f.write(combined)
                tmp_path = f.name
            try:
                # Use bash file-redirect: identical to `claude ... < file` in shell.
                # Direct stdin=file_obj hangs; bash redirect works reliably.
                # start_new_session=True → bash+claude are in new process group →
                # os.killpg() on timeout kills both, no orphans.
                proc = await asyncio.create_subprocess_exec(
                    "bash", "-c",
                    f"claude -p --output-format text --no-session-persistence --disable-slash-commands --model {CLAUDE_CLI_MODEL} < '{tmp_path}'",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
                except asyncio.TimeoutError:
                    try:
                        os.killpg(os.getpgid(proc.pid), _sig.SIGKILL)
                    except ProcessLookupError:
                        pass
                    raise TimeoutError("claude CLI timed out after 180s")
            finally:
                try:
                    os.unlink(tmp_path)
                except FileNotFoundError:
                    pass
            out_text = stdout.decode(errors="replace").strip()
            err_text = stderr.decode(errors="replace").strip()
            if proc.returncode != 0:
                # claude CLI reports usage limit in stdout, not stderr
                combined = (out_text + " " + err_text).lower()
                if any(k in combined for k in ("usage limit", "rate limit", "resets at", "try again")):
                    # Parse "resets in N hours M minutes" or "try again in Ns"
                    import re as _re2
                    m = _re2.search(r"(\d+)\s*hour", combined)
                    h = int(m.group(1)) if m else 0
                    m2 = _re2.search(r"(\d+)\s*minute", combined)
                    mins = int(m2.group(1)) if m2 else 0
                    retry_after = (h * 3600 + mins * 60) or 3600
                    raise RateLimitError(
                        f"claude CLI usage limit (retry in {h}h{mins}m): {out_text[:200]}",
                        retry_after=retry_after,
                    )
                raise RuntimeError(f"claude CLI error (rc={proc.returncode}): {err_text[:200] or out_text[:200]}")
            result = out_text
            if not result:
                raise RuntimeError("claude CLI returned empty response")
            return result

        max_async = int(os.environ.get("MAX_ASYNC", "1"))  # 1 sequential: avoids claude.ai rate limit cascades
        timeout = 600  # 3 × 180s retries + overhead budget

    elif provider == "groq":
        from lightrag.llm.openai import openai_complete_if_cache
        if not GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY not set — export GROQ_API_KEY=gsk_...")
        print(f"[rag] LLM: Groq ({GROQ_MODEL}) via Groq API")

        async def llm_func(prompt, system_prompt=None, history_messages=None, **kwargs):
            return await openai_complete_if_cache(
                GROQ_MODEL, prompt,
                system_prompt=system_prompt,
                history_messages=history_messages or [],
                api_key=GROQ_API_KEY,
                base_url="https://api.groq.com/openai/v1",
                **kwargs,
            )
        # Groq compound: 30 RPM, 250 RPD, 70K context — sequential to stay within RPM
        max_async = int(os.environ.get("MAX_ASYNC", "1"))
        timeout = 120

    elif provider == "claude" and ANTHROPIC_KEY:
        from lightrag.llm.anthropic import anthropic_complete_if_cache
        print(f"[rag] LLM: Claude ({model}) via Anthropic API")

        async def llm_func(prompt, system_prompt=None, history_messages=None, **kwargs):
            return await anthropic_complete_if_cache(
                model, prompt,
                system_prompt=system_prompt,
                history_messages=history_messages or [],
                api_key=ANTHROPIC_KEY,
                **kwargs,
            )
        max_async = 8
        timeout = 60
    else:
        from lightrag.llm.openai import openai_complete_if_cache
        if provider == "claude" and not ANTHROPIC_KEY:
            print("[rag] WARNING: --llm-provider claude but ANTHROPIC_API_KEY not set → falling back to Qwen")
        print(f"[rag] LLM: Qwen ({LLM_MODEL} @ {LLM_BASE_URL})")

        async def llm_func(prompt, system_prompt=None, history_messages=None, **kwargs):
            return await openai_complete_if_cache(
                LLM_MODEL, prompt,
                system_prompt=system_prompt,
                history_messages=history_messages or [],
                api_key=LLM_API_KEY,
                base_url=LLM_BASE_URL,
                **kwargs,
            )
        max_async = int(os.environ.get("MAX_ASYNC", "4"))
        timeout = int(os.environ.get("LLM_TIMEOUT", "600"))

    async def embed_func(texts):
        return embed_model.encode(texts, normalize_embeddings=True)

    rag = LightRAG(
        working_dir=work_dir,
        llm_model_func=llm_func,
        llm_model_max_async=max_async,
        default_llm_timeout=timeout,
        kg_chunk_pick_method=kg_chunk_pick_method,
        enable_llm_cache=enable_llm_cache,
        embedding_func=EmbeddingFunc(
            embedding_dim=EMBED_DIM,
            max_token_size=8192,
            func=embed_func,
        ),
        addon_params={
            "enable_hybrid_search": True,
            "hybrid_search_mode": "hybrid",
        },
    )
    await rag.initialize_storages()
    return rag, llm_func


async def build_agent(rag, llm_func, work_dir: str):
    """Create WikiGraphAgent wrapping the LightRAG instance."""
    from wikigraph.agent import WikiGraphAgent
    from wikigraph.config import WikiGraphConfig

    config = WikiGraphConfig(
        working_dir=work_dir,
        auto_evolve_interval=10,   # evolve every 10 queries
        co_retrieval_min_count=2,  # lower threshold for small query log
        shortcut_path_min_count=2,
    )
    agent = WikiGraphAgent(rag, config, llm_func=llm_func)
    return agent


# ── Core evaluation ──────────────────────────────────────────────────────────

async def run_eval_set(rag, questions: list[dict], label: str, top_k: int = 20) -> list[dict]:
    """Run retrieval evaluation — all questions in parallel via asyncio.gather."""
    import asyncio
    from benchmark.retrieval import evaluate_single

    completed = 0

    async def _eval(i: int, q: dict):
        nonlocal completed
        try:
            r = await evaluate_single(rag, q, top_k=top_k)
            completed += 1
            if completed % 30 == 0 or completed == len(questions):
                mb = "✓" if r.get("mix_bm25", {}).get("hit") else "✗"
                h  = "✓" if r.get("hybrid",   {}).get("hit") else "✗"
                print(f"  [{completed}/{len(questions)}] {label}  mix_bm25={mb} hybrid={h}")
            return i, r
        except Exception as e:
            print(f"  [{i+1}] {q.get('id','')} ERROR: {e}")
            return i, {
                "id": q.get("id", ""), "question": q.get("question", ""),
                "error": str(e), "category": q.get("category", ""),
                "expected_sources": q.get("expected_sources", []),
                "vector":   {"hit": False, "sources": []},
                "bm25":     {"hit": False, "sources": []},
                "hybrid":   {"hit": False, "sources": []},
                "mix_bm25": {"hit": False, "sources": []},
            }

    pairs = await asyncio.gather(*[_eval(i, q) for i, q in enumerate(questions)])
    return [r for _, r in sorted(pairs, key=lambda x: x[0])]


def compute_metrics(results: list[dict], mode: str) -> dict:
    """Compute aggregate metrics for one retrieval mode."""
    total = len(results)
    if not total:
        return {"total": 0, "hit": 0, "recall": 0.0}

    hits = sum(1 for r in results if r.get(mode, {}).get("hit", False))
    return {
        "total": total,
        "hit": hits,
        "recall": round(hits / total * 100, 1),
    }


def compute_co_retrieval_metrics(results: list[dict], mode: str) -> dict:
    """Metrics for co-retrieval questions (card_hit, linked_hit, co_hit)."""
    co_results = [r for r in results if r.get("category") == "co_retrieval" and "co_retrieval" in r]
    total = len(co_results)
    if not total:
        return {"total": 0, "card_hit": 0, "linked_hit": 0, "co_hit": 0}

    card_hits = sum(1 for r in co_results if r["co_retrieval"].get(mode, {}).get("card_hit", False))
    linked_hits = sum(1 for r in co_results if r["co_retrieval"].get(mode, {}).get("linked_hit", False))
    co_hits = sum(1 for r in co_results if r["co_retrieval"].get(mode, {}).get("co_hit", False))
    return {
        "total": total,
        "card_hit": card_hits,
        "card_recall": round(card_hits / total * 100, 1),
        "linked_hit": linked_hits,
        "linked_recall": round(linked_hits / total * 100, 1),
        "co_hit": co_hits,
        "co_recall": round(co_hits / total * 100, 1),
    }


# ── EVOLVE cycle ─────────────────────────────────────────────────────────────

async def run_evolve_cycles(agent, questions: list[dict], n_cycles: int = 3, verbose: bool = True):
    """Run benchmark questions through WikiGraphAgent to build query log, then EVOLVE.

    Each cycle: run all questions → trigger explicit EVOLVE.
    """
    for cycle in range(n_cycles):
        if verbose:
            print(f"\n[EVOLVE] Cycle {cycle + 1}/{n_cycles}: running {len(questions)} queries...")

        for q in questions:
            try:
                result = await agent.query(q["question"])
                if verbose and result.get("evolved"):
                    print(f"  Auto-evolved after: {q['id']}")
            except Exception as e:
                if verbose:
                    print(f"  [WARN] query failed for {q['id']}: {e}")

        # Explicit evolve after each cycle
        if verbose:
            print(f"[EVOLVE] Triggering explicit evolve...")
        try:
            eres = await agent.evolve()
            applied = eres.get("applied", 0)
            if verbose:
                print(f"[EVOLVE] Applied {applied} mutations")
                for msg in eres.get("messages", [])[-5:]:
                    print(f"  {msg}")
        except Exception as e:
            if verbose:
                print(f"[EVOLVE] Error: {e}")


# ── Reporting ─────────────────────────────────────────────────────────────────

def render_scenario_table(label: str, results: list[dict], prefix: str = "") -> list[str]:
    """Render a markdown table for all retrieval modes."""
    m_mb = compute_metrics(results, "mix_bm25")
    m_v  = compute_metrics(results, "vector")
    m_b  = compute_metrics(results, "bm25")
    m_h  = compute_metrics(results, "hybrid")

    lines = [
        f"\n### {label}", "",
        "**Graph-enhanced (entity graph guided, BM25+VDB fill):**", "",
        "| Mode | Hit | Total | Recall@20 |",
        "|---|---:|---:|---:|",
        f"| **Mix+BM25 — graph-enhanced hybrid** | **{m_mb['hit']}** | **{m_mb['total']}** | **{m_mb['recall']}%** |",
        "",
        "*Chunk-level retrieval (no entity graph):*", "",
        "| Mode | Hit | Total | Recall@20 |",
        "|---|---:|---:|---:|",
        f"| Vector (VDB) | {m_v['hit']} | {m_v['total']} | {m_v['recall']}% |",
        f"| BM25 (keyword) | {m_b['hit']} | {m_b['total']} | {m_b['recall']}% |",
        f"| Hybrid RRF | {m_h['hit']} | {m_h['total']} | {m_h['recall']}% |",
        "",
    ]
    return lines


def render_co_retrieval_table(label: str, results: list[dict]) -> list[str]:
    """Render co-retrieval metrics (card AND linked both found)."""
    m_v  = compute_co_retrieval_metrics(results, "vector")
    m_b  = compute_co_retrieval_metrics(results, "bm25")
    m_h  = compute_co_retrieval_metrics(results, "hybrid")
    m_mb = compute_co_retrieval_metrics(results, "mix_bm25")

    if not m_v["total"]:
        return []

    return [
        f"\n### {label} (Co-retrieval — BOTH card AND linked found)", "",
        "| Mode | Card | Linked | **Both** | Co-Recall@20 |",
        "|---|---:|---:|---:|---:|",
        f"| Vector | {m_v['card_hit']} | {m_v['linked_hit']} | {m_v['co_hit']} | {m_v['co_recall']}% |",
        f"| BM25 | {m_b['card_hit']} | {m_b['linked_hit']} | {m_b['co_hit']} | {m_b['co_recall']}% |",
        f"| Hybrid RRF | {m_h['card_hit']} | {m_h['linked_hit']} | {m_h['co_hit']} | {m_h['co_recall']}% |",
        f"| **Mix+BM25 (graph)** | **{m_mb['card_hit']}** | **{m_mb['linked_hit']}** | **{m_mb['co_hit']}** | **{m_mb['co_recall']}%** |",
        "",
    ]


def render_evolution_delta(
    before: list[dict],
    after: list[dict],
    label: str,
) -> list[str]:
    """Compare before/after EVOLVE metrics."""
    lines = [f"\n### {label} — EVOLVE Effect (Before → After)", ""]
    lines.append("| Mode | Before Recall | After Recall | Δ |")
    lines.append("|---|---:|---:|---:|")
    for mode, name in [("vector", "Vector"), ("bm25", "BM25"), ("hybrid", "Hybrid")]:
        mb = compute_metrics(before, mode)
        ma = compute_metrics(after, mode)
        delta = ma["recall"] - mb["recall"]
        sign = "+" if delta >= 0 else ""
        lines.append(f"| {name} | {mb['recall']}% | {ma['recall']}% | **{sign}{delta:.1f}%** |")
    lines.append("")

    # Co-retrieval delta
    co_b = compute_co_retrieval_metrics(before, "hybrid")
    co_a = compute_co_retrieval_metrics(after, "hybrid")
    if co_b["total"]:
        lines.append("**Co-retrieval Hybrid delta:**")
        delta_co = co_a["co_recall"] - co_b["co_recall"]
        delta_card = co_a["card_recall"] - co_b["card_recall"]
        delta_linked = co_a["linked_recall"] - co_b["linked_recall"]
        sign_co = "+" if delta_co >= 0 else ""
        lines.append(f"- Card Recall: {co_b['card_recall']}% → {co_a['card_recall']}% (Δ{'+' if delta_card>=0 else ''}{delta_card:.1f}%)")
        lines.append(f"- Linked Recall: {co_b['linked_recall']}% → {co_a['linked_recall']}% (Δ{'+' if delta_linked>=0 else ''}{delta_linked:.1f}%)")
        lines.append(f"- Co-Hit (both): {co_b['co_recall']}% → {co_a['co_recall']}% (Δ{sign_co}{delta_co:.1f}%)")
        lines.append("")

    return lines


def render_markdown(payload: dict) -> str:
    lines = [
        "# Evolving LightRAG — SHS Benchmark Report",
        "",
        f"- Generated: {payload.get('generated_at', '')}",
        f"- Data root: `{payload.get('data_root', '')}`",
        f"- Work dir: `{payload.get('work_dir', '')}`",
        f"- LLM: `{LLM_MODEL}` @ `{LLM_BASE_URL}`",
        f"- Embedding: `{EMBED_MODEL}` (local, {EMBED_DIM}-dim)",
        "",
        "## Benchmark Design",
        "",
        "| Stage | Description |",
        "|---|---|",
        "| **Scenario 1A** | Initial graph, Vector vs BM25 vs Hybrid source_hit@20 |",
        "| **Scenario 1B** | BM25 keyword advantage on proper nouns / issue IDs |",
        "| **Scenario 2** | After 3×EVOLVE cycles — graph evolution effect on hybrid recall |",
        "",
        "**Source matching**: expected_source basename vs retrieved chunk file_path.",
        "A hit = expected file's basename appears in any top-20 retrieved chunk's file_path.",
        "",
    ]

    # Scenario 1
    if "scenario1" in payload:
        s1 = payload["scenario1"]
        lines += [
            "## Scenario 1 — Initial State (Pre-EVOLVE)",
            "",
            "Vector similarity vs BM25 keyword vs Hybrid (RRF fusion).",
            "",
        ]
        # All-data
        if s1.get("all_data"):
            lines += render_scenario_table("All-Data Benchmark (17 core + golden + spec)", s1["all_data"])
        # Validation Set A
        if s1.get("validation_a"):
            lines += render_scenario_table("Validation Recall — Set A (card recall, 180Q)", s1["validation_a"])
        # Validation Set B (co-retrieval)
        if s1.get("validation_b"):
            lines += render_scenario_table("Validation Recall — Set B (co-retrieval, 90Q)", s1["validation_b"])
            lines += render_co_retrieval_table("Set B Co-Retrieval Breakdown", s1["validation_b"])

    # Scenario 2
    if "scenario2" in payload:
        s2 = payload["scenario2"]
        lines += [
            "## Scenario 2 — Post-EVOLVE (After Query Cycles)",
            "",
            f"After {s2.get('evolve_cycles', 0)} EVOLVE cycles on "
            f"{s2.get('queries_per_cycle', 0)} queries/cycle.",
            f"Total mutations applied: {s2.get('total_mutations', 0)}",
            "",
        ]
        if s2.get("all_data_before") and s2.get("all_data_after"):
            lines += render_evolution_delta(s2["all_data_before"], s2["all_data_after"],
                                            "All-Data Benchmark")
        if s2.get("validation_a_before") and s2.get("validation_a_after"):
            lines += render_evolution_delta(s2["validation_a_before"], s2["validation_a_after"],
                                            "Validation Set A")
        if s2.get("validation_b_before") and s2.get("validation_b_after"):
            lines += render_evolution_delta(s2["validation_b_before"], s2["validation_b_after"],
                                            "Validation Set B")

    lines += [
        "## Notes & Limitations",
        "",
        "- **source_hit metric**: LightRAG stores file_path as basename only (normalize_document_file_path).",
        "  Matching is basename-level. Ambiguity risk is low since files have unique IDs.",
        "- **EVOLVE co-retrieval improvement**: Graph edges added by strategy 1 (co-retrieval)",
        "  improve multi-hop entity traversal but don't directly boost pure vector recall.",
        "  Co-retrieval improvement shows in hybrid mode where graph edges add context.",
        "- **BM25 advantage**: Issue IDs (LYR-455, AUR-905), product codes (EN9100, UF4100),",
        "  and Korean tech terms benefit from keyword matching over vector similarity.",
        "- **Large corpus**: Full 563-file ingest is slow (LLM extraction per chunk).",
        "  Use `--ingest-mode priority` for a quick run on benchmark-referenced files only.",
        "",
    ]

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Evolving LightRAG SHS Benchmark")
    parser.add_argument("--data-root",
                        default=os.environ.get("DATA_ROOT",
                            str(Path(__file__).resolve().parents[3] / "shs-poc-ragflow" / "data")))
    parser.add_argument("--work-dir", default="/tmp/wikigraph_benchmark")
    parser.add_argument("--scenario", choices=["1", "2", "all"], default="all")
    parser.add_argument("--ingest-mode", choices=["priority", "full", "skip"], default="priority",
                        help="priority=benchmark files only, full=all 563 files, skip=no ingest")
    parser.add_argument("--evolve-cycles", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--out-dir", default=".")
    parser.add_argument("--llm-provider", choices=["claude", "claude-cli", "groq", "qwen"], default=None,
                        help="LLM backend: claude-cli | groq | qwen (default)")
    parser.add_argument("--claude-model", default=None,
                        help="Claude model ID (default: claude-haiku-4-5-20251001)")
    args = parser.parse_args()

    data_root = args.data_root
    work_dir = args.work_dir
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(args.out_dir, exist_ok=True)

    # Check data root
    if not Path(data_root).is_dir():
        print(f"[ERROR] data_root not found: {data_root}")
        return

    print(f"[benchmark] data_root={data_root}")
    print(f"[benchmark] work_dir={work_dir}")

    # Load question sets
    from benchmark.questions import load_all_data_questions, load_validation_set_a, load_validation_set_b
    q_all = load_all_data_questions(data_root)
    q_va = load_validation_set_a(data_root)
    q_vb = load_validation_set_b(data_root)

    print(f"[benchmark] Questions: all_data={len(q_all)}, val_A={len(q_va)}, val_B={len(q_vb)}")

    # Build LightRAG
    rag, llm_func = await build_rag(work_dir,
                                    llm_provider=args.llm_provider,
                                    claude_model=args.claude_model)

    # Ingest data
    if args.ingest_mode != "skip":
        print(f"\n[ingest] mode={args.ingest_mode}")
        from benchmark.ingest_data import ingest_all, ingest_priority_subset
        progress_path = os.path.join(work_dir, "ingest_progress.json")
        if args.ingest_mode == "priority":
            await ingest_priority_subset(rag, data_root, progress_path)
        else:
            await ingest_all(rag, data_root, progress_path)

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "data_root": data_root,
        "work_dir": work_dir,
        "scenario": args.scenario,
        "ingest_mode": args.ingest_mode,
    }

    # ── Scenario 1: Initial evaluation ──────────────────────────────────────
    if args.scenario in ("1", "all"):
        print("\n" + "="*60)
        print("SCENARIO 1 — Initial State")
        print("="*60)

        print(f"\n[S1] All-Data ({len(q_all)} questions)...")
        r_all = await run_eval_set(rag, q_all, "all_data", args.top_k)

        print(f"\n[S1] Validation Set A ({len(q_va)} questions)...")
        r_va = await run_eval_set(rag, q_va, "val_A", args.top_k)

        print(f"\n[S1] Validation Set B / Co-retrieval ({len(q_vb)} questions)...")
        r_vb = await run_eval_set(rag, q_vb, "val_B", args.top_k)

        payload["scenario1"] = {
            "all_data": r_all,
            "validation_a": r_va,
            "validation_b": r_vb,
        }

        # Quick summary
        for label, results in [("All-Data", r_all), ("Val-A", r_va), ("Val-B", r_vb)]:
            mv = compute_metrics(results, "vector")
            mb = compute_metrics(results, "bm25")
            mh = compute_metrics(results, "hybrid")
            print(f"\n  {label}: vec={mv['recall']}% bm25={mb['recall']}% hybrid={mh['recall']}%")

    # ── Scenario 2: EVOLVE cycles then re-evaluate ───────────────────────────
    if args.scenario in ("2", "all"):
        print("\n" + "="*60)
        print("SCENARIO 2 — Post-EVOLVE")
        print("="*60)

        # Backup RAG data before EVOLVE modifies the graph
        import shutil
        backup_dir = work_dir.rstrip("/") + "_pre_evolve"
        if not os.path.exists(backup_dir):
            print(f"[S2] Backing up RAG data → {backup_dir}")
            shutil.copytree(work_dir, backup_dir)
            print(f"[S2] Backup complete ({backup_dir})")
        else:
            print(f"[S2] Backup already exists at {backup_dir}, skipping copy")

        agent = await build_agent(rag, llm_func, work_dir)

        # Save pre-EVOLVE results (from S1 if available)
        if "scenario1" in payload:
            before_all = payload["scenario1"]["all_data"]
            before_va = payload["scenario1"]["validation_a"]
            before_vb = payload["scenario1"]["validation_b"]
        else:
            # Run pre-evolve eval first
            print("[S2] Running pre-EVOLVE evaluation...")
            before_all = await run_eval_set(rag, q_all, "all_data_pre", args.top_k)
            before_va = await run_eval_set(rag, q_va, "val_A_pre", args.top_k)
            before_vb = await run_eval_set(rag, q_vb, "val_B_pre", args.top_k)

        # Run EVOLVE cycles
        # Use a subset of questions for cycling (all_data + first 30 validation)
        cycle_questions = q_all + q_va[:30] + q_vb[:15]
        total_mutations = 0

        for cycle in range(args.evolve_cycles):
            print(f"\n[S2] Cycle {cycle+1}/{args.evolve_cycles}: querying {len(cycle_questions)} questions...")
            for q in cycle_questions:
                try:
                    await agent.query(q["question"])
                except Exception as e:
                    print(f"  [WARN] {q.get('id', '')}: {e}")

            print(f"[S2] Triggering EVOLVE...")
            try:
                eres = await agent.evolve()
                applied = eres.get("applied", 0)
                total_mutations += applied
                print(f"[S2] Mutations applied this cycle: {applied}")
                for msg in eres.get("messages", [])[-5:]:
                    print(f"  {msg}")
            except Exception as e:
                print(f"[S2] EVOLVE error: {e}")

        # Post-EVOLVE evaluation
        print(f"\n[S2] Post-EVOLVE evaluation (total mutations: {total_mutations})...")
        print(f"\n[S2] All-Data ({len(q_all)} questions)...")
        after_all = await run_eval_set(rag, q_all, "all_data_post", args.top_k)

        print(f"\n[S2] Validation Set A ({len(q_va)} questions)...")
        after_va = await run_eval_set(rag, q_va, "val_A_post", args.top_k)

        print(f"\n[S2] Validation Set B ({len(q_vb)} questions)...")
        after_vb = await run_eval_set(rag, q_vb, "val_B_post", args.top_k)

        payload["scenario2"] = {
            "evolve_cycles": args.evolve_cycles,
            "queries_per_cycle": len(cycle_questions),
            "total_mutations": total_mutations,
            "all_data_before": before_all,
            "all_data_after": after_all,
            "validation_a_before": before_va,
            "validation_a_after": after_va,
            "validation_b_before": before_vb,
            "validation_b_after": after_vb,
        }

        # Quick delta summary
        for label, before, after in [
            ("All-Data", before_all, after_all),
            ("Val-A", before_va, after_va),
            ("Val-B", before_vb, after_vb),
        ]:
            mb = compute_metrics(before, "hybrid")
            ma = compute_metrics(after, "hybrid")
            delta = ma["recall"] - mb["recall"]
            sign = "+" if delta >= 0 else ""
            print(f"  {label} hybrid: {mb['recall']}% → {ma['recall']}% ({sign}{delta:.1f}%)")

    # ── Finalize storages & write output ────────────────────────────────────
    await rag.finalize_storages()

    out_json = os.path.join(args.out_dir, "benchmark_results.json")
    Path(out_json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[output] JSON: {out_json}")

    md = render_markdown(payload)
    out_md = os.path.join(args.out_dir, "benchmark_results.md")
    Path(out_md).write_text(md, encoding="utf-8")
    print(f"[output] Markdown: {out_md}")

    print("\n[benchmark] Done!")


if __name__ == "__main__":
    asyncio.run(main())
