"""Load all benchmark question sets from shs-poc-ragflow corpus."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# ── Hardcoded all-data questions (from evaluate-all-data-rag.py) ─────────────
_HARDCODED_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "issue_lyr455",
        "question": "LYR-455의 근본 원인과 제안된 flush 정책은?",
        "expected_sources": ["jira/LYR-455.html"],
        "expected_terms": ["LYR-455", "WriteBooster", "Bypass"],
    },
    {
        "id": "issue_nova322",
        "question": "NOVA-322는 어느 회의록에서 논의됐고 목표 latency는?",
        "expected_sources": ["jira/NOVA-322.html", "confluence/15_UFS_FW_Weekly_Meeting.html"],
        "expected_terms": ["NOVA-322", "latency"],
    },
    {
        "id": "linked_aur905",
        "question": "AUR-905와 관련된 SoC 문서와 회의록을 찾아줘.",
        "expected_sources": [
            "jira/AUR-905.html",
            "confluence/09_SoC_Design_Review.html",
            "specs/internal_specs/04_SPEC-RTL-AUR-0007_Aurora_FlashChannelController_v2.1.docx",
        ],
        "expected_terms": ["AUR-905", "Aurora"],
    },
    {
        "id": "backlog_helios",
        "question": "Helios Sprint 24.3에서 In Progress 항목은?",
        "expected_sources": [
            "confluence/10_Sprint_Backlog.html",
            "ms_office/excel/16_sprint_backlog_Helios_24.3.xlsx",
        ],
        "expected_terms": ["Helios", "In Progress"],
    },
    {
        "id": "spreadsheet_perf",
        "question": "EN9100 Gen5 benchmark에서 Seq Read 목표와 측정값은?",
        "expected_sources": ["ms_office/excel/01_perf_benchmark_EN9100_Gen5.xlsx"],
        "expected_terms": ["EN9100", "Seq Read"],
    },
    {
        "id": "customer_req",
        "question": "Customer-M UF4100 boot performance 요구사항은?",
        "expected_sources": [
            "specs/internal_specs/12_SPEC-CR-CUSTM-UF4100_Mobile_BootPerf_v1.2.docx"
        ],
        "expected_terms": ["UF4100", "Boot"],
    },
    {
        "id": "fa_report",
        "question": "FA-2231의 증상과 관련 PLP 기준은?",
        "expected_sources": [
            "jira/FA-2231.html",
            "specs/internal_specs/16_FA-2231_Report_SSD_SPO_NSID_MetadataCorrupt_v2.0.docx",
        ],
        "expected_terms": ["FA-2231", "PLP"],
    },
    {
        "id": "ppt_review",
        "question": "Aurora A0 bringup 회의에서 power나 성능 이슈는?",
        "expected_sources": [
            "ms_office/ppt/06_Aurora_A0_Bringup_Mtg.pptx",
            "ms_office/excel/03_power_measurement_Aurora_A0.xlsx",
        ],
        "expected_terms": ["Aurora", "A0"],
    },
    {
        "id": "tech_ftl_l2p",
        "question": "FTL의 L2P 매핑 구조와 동작 방식은?",
        "expected_sources": [
            "confluence/01_FTL_Architecture.html",
            "specs/internal_specs/01_SPEC-FW-HEL-0012_Helios_FTL_L2P_Mapping_v1.3.docx",
        ],
        "expected_terms": ["FTL", "L2P"],
    },
    {
        "id": "tech_gc",
        "question": "Garbage Collection의 Victim Block 선택 기준과 WAF에 미치는 영향은?",
        "expected_sources": [
            "confluence/02_Garbage_Collection.html",
            "specs/internal_specs/02_SPEC-FW-HEL-0021_Helios_GC_WearLeveling_v1.2.docx",
        ],
        "expected_terms": ["Garbage Collection", "Victim", "WAF"],
    },
    {
        "id": "tech_wear",
        "question": "Wear leveling은 P/E 사이클을 어떻게 균등화하는가?",
        "expected_sources": [
            "confluence/03_Wear_Leveling.html",
            "specs/internal_specs/02_SPEC-FW-HEL-0021_Helios_GC_WearLeveling_v1.2.docx",
        ],
        "expected_terms": ["Wear Leveling", "P/E"],
    },
    {
        "id": "tech_plp",
        "question": "Power Loss Protection은 SPO 상황에서 어떻게 동작하며 capacitor의 역할은?",
        "expected_sources": [
            "confluence/04_Power_Loss_Protection.html",
            "specs/internal_specs/03_SPEC-FW-HEL-0034_Helios_PowerLossProtection_v1.1.docx",
        ],
        "expected_terms": ["Power Loss", "capacitor"],
    },
    {
        "id": "tech_nvme",
        "question": "NVMe host interface의 submission/completion queue 구조는?",
        "expected_sources": ["confluence/07_NVMe_Host_Interface.html"],
        "expected_terms": ["NVMe", "Queue"],
    },
    {
        "id": "tech_writebooster",
        "question": "UFS WriteBooster와 HPB의 동작 원리와 성능 효과는?",
        "expected_sources": [
            "confluence/12_UFS_WriteBooster_HPB.html",
            "ms_office/excel/08_writebooster_hpb_perf_UF4100.xlsx",
        ],
        "expected_terms": ["WriteBooster", "HPB"],
    },
    {
        "id": "tech_rpmb",
        "question": "UFS RPMB는 replay attack을 어떻게 방어하고 authentication은 어떻게 하나?",
        "expected_sources": [
            "confluence/14_UFS_RPMB_Security.html",
            "specs/internal_specs/07_SPEC-FW-LYR-0017_Lyra_UFS_RPMB_Security_v1.1.docx",
        ],
        "expected_terms": ["RPMB", "Replay"],
    },
    {
        "id": "fa_2245_thermal",
        "question": "FA-2245에서 thermal로 인한 gear downgrade의 원인과 대응은?",
        "expected_sources": [
            "jira/FA-2245.html",
            "specs/internal_specs/17_FA-2245_Report_UF4100_GearDowngrade_Thermal_v1.1.docx",
        ],
        "expected_terms": ["FA-2245", "thermal"],
    },
    {
        "id": "no_context",
        "question": "이 문서들에서 화성 농업용 로봇의 토양 센서 규격은?",
        "expected_sources": [],
        "expected_terms": [],
        "no_context": True,
    },
]


def load_all_data_questions(data_root: str | Path) -> list[dict[str, Any]]:
    """Load all-data benchmark questions (hardcoded + golden code + official spec)."""
    data_root = Path(data_root)
    questions = list(_HARDCODED_QUESTIONS)

    # Golden code questions
    gc_path = data_root / "project" / "golden_eval_questions.json"
    if gc_path.is_file():
        data = json.loads(gc_path.read_text(encoding="utf-8"))
        for e in data:
            if e.get("question") and e.get("expected_sources"):
                questions.append({
                    "id": e.get("id") or e.get("golden_id"),
                    "question": e["question"],
                    "expected_sources": e["expected_sources"],
                    "expected_terms": e.get("expected_terms", []),
                    "category": "golden_code",
                })

    # Official spec questions
    os_path = data_root / "specs" / "official_specs" / "official_eval_questions.json"
    if os_path.is_file():
        data = json.loads(os_path.read_text(encoding="utf-8"))
        for e in data:
            if e.get("question") and e.get("expected_sources"):
                questions.append({
                    "id": e.get("id") or e.get("spec_id"),
                    "question": e["question"],
                    "expected_sources": e["expected_sources"],
                    "expected_terms": e.get("expected_terms", []),
                    "category": "official_spec",
                })

    return questions


def load_validation_set_a(data_root: str | Path) -> list[dict[str, Any]]:
    """Load validation card-recall questions (Set A, 180 questions)."""
    data_root = Path(data_root)
    path = data_root / "validation" / "validation_eval_questions.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for e in data:
        if e.get("question") and e.get("expected_sources"):
            out.append({
                "id": e.get("id") or e.get("test_id"),
                "question": e["question"],
                "expected_card": e["expected_sources"][0],
                "expected_sources": e["expected_sources"],
                "expected_terms": e.get("expected_terms", []),
                "product": e.get("product", ""),
                "category": "validation_card",
            })
    return out


def load_validation_set_b(data_root: str | Path) -> list[dict[str, Any]]:
    """Load co-retrieval questions (Set B, 90 questions).

    Each question must retrieve BOTH the validation card AND the linked doc.
    """
    data_root = Path(data_root)
    path = data_root / "validation" / "validation_coretrieval_questions.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for e in data:
        if e.get("question") and e.get("expected_card"):
            out.append({
                "id": e.get("id") or e.get("test_id"),
                "question": e["question"],
                "expected_card": e["expected_card"],
                "expected_linked": e.get("expected_linked", ""),
                "expected_sources": [e["expected_card"]] + (
                    [e["expected_linked"]] if e.get("expected_linked") else []
                ),
                "product": e.get("product", ""),
                "category": "co_retrieval",
            })
    return out


def source_hit(retrieved_sources: list[str], expected_sources: list[str]) -> bool:
    """Check if any expected source matches any retrieved source.

    LightRAG stores file_path as basename only (normalize_document_file_path strips dirs).
    We match expected basename against retrieved basename to handle both
    the stored form and the expected relative path form.
    """
    if not expected_sources:
        return True
    for expected in expected_sources:
        expected_bn = os.path.basename(expected).lower()
        for retrieved in retrieved_sources:
            retrieved_bn = os.path.basename(retrieved).lower()
            retrieved_lower = retrieved.lower()
            # Direct basename match OR expected substring in retrieved (full path)
            if expected_bn == retrieved_bn or expected_bn in retrieved_lower:
                return True
    return False


def co_retrieval_hit(retrieved_sources: list[str], expected_card: str, expected_linked: str) -> dict:
    """Check co-retrieval: BOTH card AND linked doc must appear in results."""
    card_hit = source_hit(retrieved_sources, [expected_card])
    linked_hit = source_hit(retrieved_sources, [expected_linked]) if expected_linked else False
    return {
        "card_hit": card_hit,
        "linked_hit": linked_hit,
        "co_hit": card_hit and linked_hit,
    }
