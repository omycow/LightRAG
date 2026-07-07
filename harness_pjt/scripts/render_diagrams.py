"""Regenerates the architecture diagrams embedded in README.md.

Run with: python harness_pjt/scripts/render_diagrams.py
Outputs to harness_pjt/images/*.png. Requires matplotlib and a CJK-capable
font (Noto Sans CJK KR) for the Korean labels.
"""

from __future__ import annotations

import os

import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, FancyArrowPatch, FancyBboxPatch

plt.rcParams["font.family"] = "Noto Sans CJK KR"

IMG_DIR = os.path.join(os.path.dirname(__file__), "..", "images")
os.makedirs(IMG_DIR, exist_ok=True)

COLOR_ANALYZE = "#5B7FDB"
COLOR_RESPOND = "#3FA66B"
COLOR_EVOLVE = "#E08A3C"
COLOR_STRUCTURAL = "#8E5BC4"
COLOR_KG = "#4A5568"
COLOR_BG = "#FFFFFF"
TEXT_DARK = "#1A202C"


def _box(ax, xy, w, h, text, facecolor, edgecolor=None, fontsize=11, fontweight="bold",
         textcolor="white", dashed=False, zorder=2):
    edgecolor = edgecolor or facecolor
    style = "round,pad=0.12,rounding_size=0.08"
    box = FancyBboxPatch(
        xy, w, h, boxstyle=style, linewidth=1.8,
        edgecolor=edgecolor, facecolor=facecolor,
        linestyle="dashed" if dashed else "solid", zorder=zorder,
    )
    ax.add_patch(box)
    ax.text(xy[0] + w / 2, xy[1] + h / 2, text, ha="center", va="center",
             fontsize=fontsize, fontweight=fontweight, color=textcolor, zorder=zorder + 1)
    return box


def _arrow(ax, start, end, color="#4A5568", lw=2.0, style="-|>", dashed=False, connectionstyle="arc3,rad=0.0"):
    arrow = FancyArrowPatch(
        start, end, arrowstyle=style, mutation_scale=18, linewidth=lw,
        color=color, linestyle="dashed" if dashed else "solid",
        connectionstyle=connectionstyle, zorder=1,
    )
    ax.add_patch(arrow)


def render_core_cycle():
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")
    ax.set_title("계속 진화하는 그래프 기반 에이전트 RAG", fontsize=15, fontweight="bold", color=TEXT_DARK, pad=16)

    positions = {
        "docs": (5, 8.6, "문서 삽입\n(INGEST)"),
        "graph": (8.2, 5, "지식 그래프\n(LightRAG)"),
        "query": (5, 1.4, "사용자 질문\n(ANALYZE → RESPOND)"),
        "evolve": (1.8, 5, "백그라운드 EVOLVING\n(그래프 개선)"),
    }
    colors = {"docs": COLOR_KG, "graph": "#2D3748", "query": COLOR_RESPOND, "evolve": COLOR_EVOLVE}
    w, h = 2.6, 1.3
    boxes = {}
    for key, (x, y, label) in positions.items():
        boxes[key] = _box(ax, (x - w / 2, y - h / 2), w, h, label, colors[key], fontsize=11)

    _arrow(ax, (5, 8.6 - h / 2), (7.2, 5.6), connectionstyle="arc3,rad=-0.2")
    _arrow(ax, (8.2, 5 - h / 2), (5.9, 1.9), connectionstyle="arc3,rad=-0.2")
    _arrow(ax, (3.8, 1.6), (1.9, 4.3), connectionstyle="arc3,rad=-0.2")
    _arrow(ax, (1.8, 5 + h / 2), (4.1, 8.3), connectionstyle="arc3,rad=-0.2", color=COLOR_EVOLVE)
    ax.text(5, 5, "질문마다\n조금씩 똑똑해지는\n그래프", ha="center", va="center",
            fontsize=11.5, color="#718096", fontstyle="italic")

    fig.savefig(os.path.join(IMG_DIR, "core_cycle.png"), dpi=170, bbox_inches="tight", facecolor=COLOR_BG)
    plt.close(fig)


def render_agent_flow():
    fig, ax = plt.subplots(figsize=(11, 10))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 15.5)
    ax.axis("off")
    ax.set_title("에이전트 흐름: 분석 → 답변 / Evolving 두 경로", fontsize=15, fontweight="bold", color=TEXT_DARK, pad=14)

    _box(ax, (4.4, 13.8), 3.2, 1.0, "사용자 질문", "#2D3748", fontsize=12)
    _arrow(ax, (6, 13.8), (6, 12.9))

    _box(ax, (3.9, 11.9), 4.2, 1.0, "1. ANALYZE  (분석 페이즈)\n쿼리 재작성 + 서브쿼리 분해", COLOR_ANALYZE, fontsize=10.5)
    ax.text(6, 11.55, "여기서 두 경로로 분기", ha="center", fontsize=10, color="#718096", fontstyle="italic")

    _arrow(ax, (5.0, 11.9), (2.6, 10.6), connectionstyle="arc3,rad=0.15")
    _arrow(ax, (7.0, 11.9), (9.0, 10.6), connectionstyle="arc3,rad=-0.15")

    _box(ax, (0.8, 9.5), 3.6, 1.1, "RESPOND (전경)\n최적화 쿼리로 즉시 답변", COLOR_RESPOND, fontsize=10.5)
    _arrow(ax, (2.6, 9.5), (2.6, 8.5))
    _box(ax, (0.8, 7.3), 3.6, 1.1, "사용자에게 즉시 리턴", "#2D3748", fontsize=11)

    ex, ew = 6.9, 4.4
    outer = FancyBboxPatch((ex, 3.7), ew, 6.2, boxstyle="round,pad=0.15,rounding_size=0.12",
                            linewidth=2.0, edgecolor=COLOR_EVOLVE, facecolor="#FDF3E8",
                            linestyle="dashed", zorder=1)
    ax.add_patch(outer)
    ax.text(ex + ew / 2, 9.55, "EVOLVE 파이프라인 (백그라운드 큐)", ha="center", fontsize=11,
            fontweight="bold", color=COLOR_EVOLVE)

    steps = [
        ("RETRIEVE\n서브쿼리별 리트리브", "#2D3748", 8.65),
        ("EVALUATE\n품질 점수 (0 LLM)", "#2D3748", 7.45),
        ("EVOLVE(light)\n매 쿼리마다", COLOR_EVOLVE, 6.25),
    ]
    sx, sw, sh = ex + 0.5, ew - 1.0, 0.85
    prev_y = None
    for label, color, cy in steps:
        _box(ax, (sx, cy - sh / 2), sw, sh, label, color, fontsize=9.6)
        if prev_y is not None:
            _arrow(ax, (sx + sw / 2, prev_y), (sx + sw / 2, cy + sh / 2), lw=1.6)
        prev_y = cy - sh / 2

    _arrow(ax, (sx + sw / 2, 6.25 - sh / 2), (sx + sw / 2, 4.85), lw=1.6)
    ax.text(sx + sw / 2, 5.15, "N번째 쿼리마다 (기본 50)", ha="center", fontsize=9, color=COLOR_EVOLVE, fontstyle="italic")
    _box(ax, (sx, 3.85), sw, 0.95, "EVOLVE(batch) + STRUCTURAL\n종합 분석 + 구조 관계 마이닝", COLOR_STRUCTURAL, fontsize=9.3)

    _arrow(ax, (2.6, 7.3), (2.6, 2.2), connectionstyle="arc3,rad=0.35", color="#A0AEC0", dashed=True)
    _arrow(ax, (ex + ew / 2 - 1.5, 3.85), (6.5, 2.2), connectionstyle="arc3,rad=-0.2", color=COLOR_EVOLVE)

    _box(ax, (4.0, 1.3), 4.0, 1.0, "지식 그래프\n(다음 쿼리부터 반영)", "#2D3748", fontsize=11)

    fig.savefig(os.path.join(IMG_DIR, "agent_flow.png"), dpi=170, bbox_inches="tight", facecolor=COLOR_BG)
    plt.close(fig)


def render_relation_improvement():
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 6.6)
    ax.axis("off")
    ax.set_title("Evolving → 관계그래프 개선 (지식그래프 + 구조 관계)", fontsize=14.5, fontweight="bold", color=TEXT_DARK, pad=12)

    cx, cy = 6, 2.55
    center = Ellipse((cx, cy), 3.0, 1.5, facecolor=COLOR_KG, edgecolor=COLOR_KG, zorder=2)
    ax.add_patch(center)
    ax.text(cx, cy, "지식 그래프\n(엔티티 + 릴레이션)", ha="center", va="center", fontsize=11, color="white",
            fontweight="bold", zorder=3)

    _box(ax, (0.4, 4.75), 4.7, 1.0, "구조 관계 개선 (STRUCTURAL)\n구조 정보 추가", COLOR_STRUCTURAL, fontsize=11)
    left_items = [
        "문서 노드 + CONTAINS",
        "문서 ↔ 문서 (공유 엔티티)",
        "청크 인접 엔티티 (NEAR)",
    ]
    for i, item in enumerate(left_items):
        ax.text(0.7, 4.32 - i * 0.4, f"• {item}", fontsize=9.6, color=TEXT_DARK)
    _arrow(ax, (2.9, 4.75), (5.0, 3.05), connectionstyle="arc3,rad=-0.15", color=COLOR_STRUCTURAL)

    _box(ax, (6.9, 4.75), 4.7, 1.0, "지식그래프 개선 (EVOLVE)\n멀티홉 · 추론 기반 확장", COLOR_EVOLVE, fontsize=11)
    right_items = [
        "co-retrieval 강화 (링크 예측)",
        "gap filling (추출 누락 보강)",
        "shortcut path (다중 홉 단축)",
        "근거상실 제거 / 모순 통합",
    ]
    for i, item in enumerate(right_items):
        ax.text(7.55, 4.32 - i * 0.4, f"• {item}", fontsize=9.6, color=TEXT_DARK)
    _arrow(ax, (9.1, 4.75), (7.0, 3.05), connectionstyle="arc3,rad=0.15", color=COLOR_EVOLVE)

    ax.text(cx, 0.55, "구조 관계 = 코퍼스 메타데이터(문서/청크) 기반, LLM 호출 없음\n지식그래프 개선 = 쿼리 로그·리트리브 결과 기반, 필요시 LLM 호출",
            ha="center", fontsize=9.8, color="#718096", fontstyle="italic")

    fig.savefig(os.path.join(IMG_DIR, "relation_graph_improvement.png"), dpi=170, bbox_inches="tight", facecolor=COLOR_BG)
    plt.close(fig)


if __name__ == "__main__":
    render_core_cycle()
    render_agent_flow()
    render_relation_improvement()
    print(f"Diagrams written to {os.path.abspath(IMG_DIR)}")
