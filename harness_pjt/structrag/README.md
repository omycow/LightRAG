# StructRAG (ver5) — Structure-Aware Agentic RAG with Two-Track Evolution

빠른 쿼리 응답(핫패스 LLM 최소화)과 백그라운드 자기진화를 분리한 Agentic RAG.
**작업 전 [DESIGN_HISTORY.md](DESIGN_HISTORY.md) 필독** — 그라운드룰 7개와 ver1~4 실패 교훈.
전략별 문헌 근거는 [research_notes.md](research_notes.md).

## 핵심 설계

- **투트랙 진화**: 문서 레벨 구조관계·공동검색 통계 → **구조그래프(SG, 별도 저장소)**.
  청크 증거가 있는 의미 지식 → **KG**. 구조 통계를 KG/relation VDB에 넣으면 base 검색이
  오염된다는 것이 ver4의 교훈 (All@20 하락).
- **SG 3층 증거 모델**: L1 `explicit_ref`(청크 내 파일명/ID 언급, 결정적) /
  L2 `co_retrieval`(quality-gated 통계, 강화·감쇠) / L3 `llm_curated`(증거 인용 필수 타입드 관계).
- **티어드 analyzer**: PlanCache 히트 → LLM 0회(~40ms). 신규 복잡 쿼리만 LLM 1콜.
  룰 검증이 ID 쿼리의 lexical 라우팅을 강제. ε-greedy StrategyMemory가 클러스터별 모드 학습.
- **결정적 quality**: QPP(NQC/WIG류) + 융합 합의도 + 텀 커버리지, 러닝 분포 퍼센타일 자가보정 게이트.
  저품질 쿼리는 attention queue로 → 백그라운드 LLM 분석 우선 대상.
- **회귀 가드**: KG 변경은 사이클 체크포인트, All/ValA 하락 시 자동 롤백.

## 파일

| 파일 | 역할 |
|---|---|
| `structure_graph.py` | SG 자료구조, L1 빌더(버전 마커로 자동 리빌드), L2/L3 API, 감쇠, 탐색 |
| `analyzer.py` | QueryPlan, StrategyMemory(밴딧), PlanCache, 신호 게이팅 LLM 분석 |
| `retriever.py` | analyze→scope(soft-boost)→retrieve→EXPAND(SG 이웃)→RRF(+채널 top-3 보장, 문서당 청크 2)→score→log |
| `quality.py` | QPP 시그널 + RunningQuality 퍼센타일 게이트 |
| `evolver.py` | 투트랙 백그라운드 진화 (Track S: SG / Track K: KG), LLM 예산, KG 체크포인트/롤백 |
| `verify_sg.py` / `test_units.py` / `smoke_test.py` | 검증 스크립트 |

저장소: `<working_dir>/structrag_meta/` — structure_graph.json, plan_cache.json,
strategy_memory.json, quality_stats.json, query_log.jsonl, **evolution_log.jsonl**(모든 변경의 증거·사유).

## 평가

```bash
cd LightRAG
python3 harness_pjt/evaluation/scenario2-ver5/eval_v5.py --fresh --iterations 5   # 검증 런 (무개입)
python3 harness_pjt/evaluation/scenario2-ver5/make_graphs.py                      # 그래프
```

골든 `harness_pjt/rag_data/`는 읽기 전용 — `--fresh`가 작업 카피를 만든다.

## 검증 결과 (무개입 --fresh 5회 런, 그라운드룰 7)

| 버전 | co@20 | co@10 | co@5 | 특징 |
|---|---|---|---|---|
| v5.1 | 86.7~95.6 (하락 추세) | ~49 | 23~39 널뛰기 | 레벨 높지만 불안정 |
| v5.4 (`structrag-v5.4`) | 74.9±3.75, →78.9 상승 | 52.4±2.88 | 39.8±2.38 | co@20 종점 최고 |
| v5.6 (`structrag-v5.6`) | 73.5±2.14 | 54.9±2.33 | 42.4±1.81 | 에코 오염으로 하락 마감 (교훈용) |
| v5.7 (`structrag-v5.7`) | 69.1±1.63 | 52.9±0.98 | 38.5±1.48 | 에코 전면 차단 — 안정·성장 정체 |
| **v5.8 (`structrag-v5.8`, 현행)** | 74.7±1.86, 72.2→75.6 | **56.5±1.84** | **42.6±1.25** | 선택적 에코 — 높은 시작+우상향+안정 동시 충족 |

공통: All@20 ~100 / ValA@20 ~99 유지, 회귀가드 자동 롤백 실증, LLM 무장애 런 기준.
사이클별 진단·교훈·판정: [DESIGN_HISTORY.md](DESIGN_HISTORY.md), 상세 수치: `evaluation/scenario2-ver5/results/`
