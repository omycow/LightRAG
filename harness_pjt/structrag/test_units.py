"""Unit sanity tests for structrag components (no LLM, no RAG instance needed).

Run: python3 harness_pjt/structrag/test_units.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness_pjt.structrag.quality import score_retrieval, ATTENTION_THRESHOLD
from harness_pjt.structrag.structure_graph import StructureGraph


def test_quality_good_vs_bad():
    q = "NVMe DMA PRP descriptor engine validation test"
    good_chunks = [{"content": "NVMe DMA PRP descriptor engine validation test suite covering SGL", "file_path": "a/doc1.html"}]
    bad_chunks = [{"content": "unrelated garbage about cooking recipes", "file_path": "a/doc9.html"}]

    docs = {"c1": "doc1.html", "c2": "doc1.html", "c3": "doc2.html",
            "c4": "doc2.html", "c7": "doc7.html", "c8": "doc8.html", "c9": "doc9.html"}
    good = score_retrieval(
        q, good_chunks,
        vector_sims=[0.85, 0.80, 0.78],
        bm25_ids=["c1", "c2", "c3"], vector_ids=["c1", "c2", "c4"],
        scope={"doc1.html": 1.0},
        chunk_docs=docs.get,
    )
    bad = score_retrieval(
        q, bad_chunks,
        vector_sims=[0.30, 0.22, 0.18],
        bm25_ids=["c1", "c2", "c3"], vector_ids=["c7", "c8", "c9"],
        scope={"doc1.html": 1.0},
        chunk_docs=docs.get,
    )
    assert good["quality"] > 0.7, good
    assert bad["quality"] < ATTENTION_THRESHOLD, bad
    assert score_retrieval(q, [])["quality"] == 0.0
    print(f"  quality: good={good['quality']} bad={bad['quality']} ✓")


def test_quality_partial_signals():
    # only term coverage available → still scores, renormalized
    r = score_retrieval("PCIe hub link training", [{"content": "PCIe hub link training procedure", "file_path": "x/p.html"}])
    assert 0.9 <= r["quality"] <= 1.0, r
    print(f"  partial-signal renormalization ✓ ({r['quality']})")


def test_sg_layers_and_decay():
    with tempfile.TemporaryDirectory() as td:
        sg = StructureGraph(td)
        chunks = {
            "c1": {"file_path": "specs/SPEC-FW-0001_alpha.md", "content": "see beta_notes.md and GAMMA-100"},
            "c2": {"file_path": "notes/beta_notes.md", "content": "refs SPEC-FW-0001 details"},
            "c3": {"file_path": "jira/GAMMA-100.html", "content": "standalone"},
        }
        sg.build_l1(chunks, rebuild=True)
        s = sg.stats
        assert s["docs"] == 3 and s["l1_explicit"] >= 2, s

        # L2 gate: low-quality co-retrieval must NOT reinforce
        sg.reinforce_co_retrieval(["beta_notes.md", "GAMMA-100.html"], quality=0.3)
        assert sg.stats["l2_co_retrieval"] == 0
        sg.reinforce_co_retrieval(["beta_notes.md", "GAMMA-100.html"], quality=0.9)
        assert sg.stats["l2_co_retrieval"] == 1

        # decay removes unused learned layers eventually, keeps L1
        for _ in range(40):
            sg.decay(factor=0.5)
        assert sg.stats["l2_co_retrieval"] == 0
        assert sg.stats["l1_explicit"] >= 2

        # neighbors + scope
        nbs = sg.get_neighbors("SPEC-FW-0001_alpha.md", top_n=5)
        assert {d for d, _ in nbs} >= {"beta_notes.md", "GAMMA-100.html"}, nbs
        scope = sg.get_scope(["SPEC-FW-0001_alpha.md"], budget=5)
        assert "beta_notes.md" in scope

        # seed matching by ID token in query
        seeds = sg.match_docs_by_tokens("GAMMA-100 이슈 관련 스펙")
        assert "GAMMA-100.html" in seeds, seeds

        # v5.8 selective echo: expansion-involved pair reinforces ONLY on L1 edges
        # (alpha↔beta has L1 from build; alpha↔GAMMA has L1 too; use a fresh doc pair)
        sg2 = StructureGraph(td + "/sg2")
        sg2.build_l1({
            "c1": {"file_path": "a/left.md", "content": "mentions right.md here"},
            "c2": {"file_path": "a/right.md", "content": "plain"},
            "c3": {"file_path": "a/lone.md", "content": "plain"},
        }, rebuild=True)
        # left↔right has L1; left↔lone does not
        sg2.reinforce_co_retrieval(["left.md", "right.md", "lone.md"], quality=0.9,
                                   expansion_docs={"right.md", "lone.md"})
        edges = sg2._data["edges"]
        assert "co_retrieval" in edges["left.md||right.md"]["layers"]      # L1-backed → allowed
        assert "lone.md||left.md" not in edges or \
               "co_retrieval" not in edges.get("left.md||lone.md", {"layers": {}})["layers"]
        # base-only pairs unaffected by the rule
        sg2.reinforce_co_retrieval(["left.md", "lone.md"], quality=0.9, expansion_docs=set())
        assert "co_retrieval" in edges["left.md||lone.md"]["layers"]
    print("  structure graph layers/gate/decay/traversal/selective-echo ✓")


def test_analyzer_tiers_and_rules():
    import asyncio
    from harness_pjt.structrag.analyzer import QueryAnalyzer, QueryPlan

    async def fake_llm(prompt, **kw):
        return ('{"rewrite": "PCIe hub link training spec", '
                '"subqueries": [{"q": "PCIe hub link training", "intent": "concept"}, '
                '{"q": "AUR-905 issue details", "intent": "concept"}], '
                '"doc_hints": ["AUR-905.html"]}')

    complex_q = ("PCIe hub link training 절차가 스펙과 일치하는지 그리고 AUR-905 이슈에서 "
                 "보고된 게이트 협상 실패가 어떤 조건에서 재현되는지 알려주세요")

    with tempfile.TemporaryDirectory() as td:
        an = QueryAnalyzer(td, llm_func=fake_llm)
        # v5.15: the hot path NEVER calls the LLM — even complex queries get
        # rules on a cache miss (uniform latency); LLM plans are background-only
        hot = asyncio.run(an.analyze(complex_q))
        assert hot.source == "rules"
        assert an.needs_background_analysis(complex_q)
        assert not an.needs_background_analysis("AUR-905 이슈 상세")

        plan = asyncio.run(an.llm_plan(complex_q))
        assert plan is not None and plan.source == "llm" and plan.origin == "llm"

        # ① skeleton: keyword-different, shape-alike queries share a strategy key
        from harness_pjt.structrag.analyzer import skeleton
        s1 = skeleton("Helios GC valid migration 검증하는 테스트 있나요?")
        s2 = skeleton("Lyra thermal gear downgrade 테스트 코드 있어?")
        assert s1 == s2, (s1, s2)
        assert skeleton("AUR-905 이슈 상세") != s1

        # ② LLM-worth gate: once both sources have evidence and llm ≈ rules,
        # background analysis for that shape is skipped
        for _ in range(3):
            an.memory.update_source(s1, "rules", 0.60)
            an.memory.update_source(s1, "llm", 0.61)
        assert not an.memory.llm_adds_value(s1)
        same_shape_q = "Nova UTP mphy 링크업 게이트니고 검증하는 테스트 있나요?"
        assert skeleton(same_shape_q) == s1
        assert not an.needs_background_analysis(same_shape_q)
        for _ in range(5):
            an.memory.update_source(s1, "llm", 0.75)
        assert an.memory.llm_adds_value(s1)
        # rule validation forces the ID-bearing subquery back to id_lookup → lexical mode
        id_sub = next(s for s in plan.subqueries if "AUR-905" in s["q"])
        assert id_sub["intent"] == "id_lookup" and id_sub["mode"] in ("bm25", "hybrid"), id_sub

        # high-quality feedback promotes the plan → Tier 0 cache hit next time
        an.feedback(plan.original, plan, quality=0.9)
        plan2 = asyncio.run(an.analyze(complex_q))
        assert plan2.source == "cache"

        # low-quality plans are never promoted
        an2 = QueryAnalyzer(td, llm_func=None)
        p3 = asyncio.run(an2.analyze("완전히 새로운 NAND 셀 특성 질문입니다"))
        assert p3.source == "rules"
        an2.feedback(p3.original, p3, quality=0.2)
        p4 = asyncio.run(an2.analyze("완전히 새로운 NAND 셀 특성 질문입니다"))
        assert p4.source == "rules"

        # bandit: repeated feedback should steer mode selection
        an.memory.epsilon = 0.0
        for _ in range(3):
            an.memory.update("cl", "bm25", 0.9)
            an.memory.update("cl", "hybrid", 0.3)
        assert an.memory.select("cl", ["hybrid", "bm25"]) == "bm25"

        # C4: a mid-quality LLM plan is reused (no re-LLM for the same pattern),
        # a mid-quality rules plan is NOT (keeps its chance to be LLM-upgraded)
        from harness_pjt.structrag.analyzer import PlanCache, rule_plan
        pc = PlanCache(td)
        lp = rule_plan("아주 복잡한 미드퀄리티 엘엘엠 플랜 쿼리")
        lp.source = "llm"
        pc.promote(lp.original, lp, quality=0.5, gate=0.7, floor=0.4)
        assert pc.get(lp.original, min_quality=0.7) is not None
        rp = rule_plan("아주 복잡한 미드퀄리티 룰즈 플랜 쿼리입니다")
        pc.promote(rp.original, rp, quality=0.5, gate=0.7, floor=0.4)
        assert pc.get(rp.original, min_quality=0.7) is None
    print("  analyzer tiers/rule-validation/cache/bandit/C4 ✓")


if __name__ == "__main__":
    test_quality_good_vs_bad()
    test_quality_partial_signals()
    test_sg_layers_and_decay()
    test_analyzer_tiers_and_rules()
    print("all unit tests passed")
