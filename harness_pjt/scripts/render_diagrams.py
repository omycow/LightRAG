"""Regenerates the architecture diagrams embedded in README.md.

Diagram sources live as Mermaid (.mmd) files under harness_pjt/diagrams/ and
are rendered to PNG via mermaid-cli (mmdc). No hand-drawn/matplotlib styling
-- what you see is what mermaid renders from the .mmd source.

Run with: python harness_pjt/scripts/render_diagrams.py
Requires Node/npx (mermaid-cli is fetched on demand) and a Chromium binary
reachable via CHROMIUM_PATH, PATH, or Playwright's default install location.
Outputs to harness_pjt/images/*.png.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import tempfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIAGRAM_DIR = os.path.join(BASE_DIR, "diagrams")
IMG_DIR = os.path.join(BASE_DIR, "images")

DIAGRAMS = {
    "core_cycle": r"""
flowchart TD
    INGEST["문서 삽입<br/>(INGEST)"]
    GRAPH[("지식 그래프<br/>(LightRAG)")]
    QUERY["사용자 질문<br/>1. 쿼리분석·전략선택·응답"]
    LOG["2. 로깅 및 로그분석<br/>(백그라운드)"]
    IMPROVE["3. 그래프 개선<br/>(백그라운드)"]

    INGEST --> GRAPH
    GRAPH --> QUERY
    QUERY --> LOG
    LOG --> IMPROVE
    IMPROVE --> GRAPH

    style GRAPH fill:#2D3748,color:#fff,stroke:#2D3748
    style QUERY fill:#3FA66B,color:#fff,stroke:#3FA66B
    style LOG fill:#5B7FDB,color:#fff,stroke:#5B7FDB
    style IMPROVE fill:#E08A3C,color:#fff,stroke:#E08A3C
    style INGEST fill:#4A5568,color:#fff,stroke:#4A5568
""",
    "agent_flow": r"""
flowchart TD
    U["사용자 질문"] --> A["1. ANALYZE<br/>쿼리 재작성 + 서브쿼리 분해"]
    A --> F{"전략 선택"}
    F -->|"응답 경로"| R["RESPOND (전경)<br/>최적화 쿼리로 즉시 검색"]
    R --> RET["사용자에게 즉시 응답"]
    F -->|"백그라운드 큐"| Q["EVOLVE 큐 등록"]

    subgraph BG["2. 로깅 및 로그분석 (백그라운드)"]
        direction TB
        RETQ["RETRIEVE<br/>서브쿼리별 리트리브"] --> EV["EVALUATE<br/>quality 점수 산출 (0 LLM)"]
        EV --> LG["쿼리 로그 축적 + 메타데이터 갱신<br/>(access_count, last_accessed)"]
    end
    Q --> RETQ

    subgraph GI["3. 그래프 개선 (백그라운드)"]
        direction TB
        T1["매 쿼리마다<br/>(light)"]
        T2["N번째 쿼리마다, 기본 50<br/>(batch)"]
        T1 -.-> T2
    end
    LG --> T1

    T1 --> KG[("지식 그래프<br/>다음 쿼리부터 반영")]
    T2 --> KG

    style A fill:#5B7FDB,color:#fff,stroke:#5B7FDB
    style R fill:#3FA66B,color:#fff,stroke:#3FA66B
    style RET fill:#2D3748,color:#fff,stroke:#2D3748
    style RETQ fill:#2D3748,color:#fff,stroke:#2D3748
    style EV fill:#2D3748,color:#fff,stroke:#2D3748
    style LG fill:#5B7FDB,color:#fff,stroke:#5B7FDB
    style T1 fill:#E08A3C,color:#fff,stroke:#E08A3C
    style T2 fill:#E08A3C,color:#fff,stroke:#E08A3C
    style KG fill:#2D3748,color:#fff,stroke:#2D3748
""",
    "graph_improvement": r"""
flowchart LR
    subgraph GI["3. 그래프 개선"]
        direction TB
        I1["co-retrieval 강화<br/>(링크 예측)"]
        I2["gap filling<br/>(추출 누락 보강)"]
        I3["shortcut path<br/>(다중 홉 단축)"]
        I4["근거상실 제거 /<br/>모순 통합"]
        I5["문서 노드 + CONTAINS"]
        I6["문서 ↔ 문서<br/>(공유 엔티티)"]
        I7["청크 인접 엔티티<br/>(NEAR)"]
    end
    KG[("지식 그래프<br/>엔티티 + 릴레이션")]

    I1 --> KG
    I2 --> KG
    I3 --> KG
    I4 --> KG
    I5 --> KG
    I6 --> KG
    I7 --> KG

    style KG fill:#2D3748,color:#fff,stroke:#2D3748
    style I1 fill:#E08A3C,color:#fff,stroke:#E08A3C
    style I2 fill:#E08A3C,color:#fff,stroke:#E08A3C
    style I3 fill:#E08A3C,color:#fff,stroke:#E08A3C
    style I4 fill:#E08A3C,color:#fff,stroke:#E08A3C
    style I5 fill:#E08A3C,color:#fff,stroke:#E08A3C
    style I6 fill:#E08A3C,color:#fff,stroke:#E08A3C
    style I7 fill:#E08A3C,color:#fff,stroke:#E08A3C
""",
}


def _puppeteer_config_path() -> str | None:
    """Sandbox-safe Chromium config for containers that run mmdc as root."""
    chromium = (
        os.environ.get("CHROMIUM_PATH")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
    )
    if not chromium:
        matches = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")
        chromium = matches[0] if matches else None
    if not chromium:
        return None
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(
            {"executablePath": chromium, "args": ["--no-sandbox", "--disable-setuid-sandbox"]},
            f,
        )
    return path


def render_all() -> None:
    os.makedirs(DIAGRAM_DIR, exist_ok=True)
    os.makedirs(IMG_DIR, exist_ok=True)
    puppeteer_config = _puppeteer_config_path()
    try:
        for name, source in DIAGRAMS.items():
            mmd_path = os.path.join(DIAGRAM_DIR, f"{name}.mmd")
            with open(mmd_path, "w") as f:
                f.write(source.strip() + "\n")

            png_path = os.path.join(IMG_DIR, f"{name}.png")
            cmd = [
                "npx", "--yes", "@mermaid-js/mermaid-cli",
                "-i", mmd_path, "-o", png_path,
                "-b", "white", "-w", "1100",
            ]
            if puppeteer_config:
                cmd += ["-p", puppeteer_config]
            subprocess.run(cmd, check=True)
    finally:
        if puppeteer_config:
            os.remove(puppeteer_config)

    print(f"Diagrams written to {os.path.abspath(IMG_DIR)}")


if __name__ == "__main__":
    render_all()
