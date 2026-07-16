---
# ══════════════════════════════════════════════════════════════
#  SHS Final Benchmark 100 — 리더보드 제출 템플릿
#
#  이 파일 하나만 채워서 올리면 리더보드에 자동 등록됩니다.
#   · 웹: "＋ 리포트 추가" → "제출 템플릿(.md) 업로드"
#   · CLI: curl -F "file=@내제출.md" http://<host>:8040/api/submissions
#  등록 시 제출자 코드가 1회 발급됩니다 — 어필 코멘트 작성에 필요하니 보관하세요.
#
#  '#' 뒤는 주석입니다. 값만 바꾸면 되고, 선택 항목은 줄째 지워도 됩니다.
# ══════════════════════════════════════════════════════════════

# ── 필수 ──
title: StructRAG v5.21 — Two-Track Evolution, Wide Observation

# ── 기본 정보 (선택) ──
submitter: bwkim
date: 2026-07-16
badge: structrag-v5.21
report_path: harness_pjt/structrag/DESIGN_HISTORY.md (tag structrag-v5.21)

# ── 기본 성능: SHS Final Benchmark 100 (judge gpt-5.3-codex) ──
final_score: 0.7641

# 판정 분포 — 세 값을 모두 채우거나, 모두 지울 것
pass: 0                                # TODO
partial: 0                             # TODO
fail: 0                                # TODO

# 4-metric — 네 값을 모두 채우거나, 모두 지울 것 (각 0~1)
evidence_recall: 0.0000                # TODO (가중 30%)
evidence_precision: 0.0000             # TODO (가중 20%)
answer_completeness: 0.0000            # TODO (가중 25%)
faithfulness: 0.0000                   # TODO (가중 25%)
---

## 한 줄 요약

핫패스 LLM 0~1콜 티어드 플래너와 quality-gated 구조그래프(SG)를 KG와 분리 진화시키는 자기진화 RAG. v5.21은 "서빙 좁게, 관찰 넓게"를 복원해 학습 시야를 랭킹 40위까지 확장.

## 주요 아이디어

- **투트랙 진화 (SG/KG 분리)** — 문서 레벨 구조관계·공동검색 통계는 별도 저장소인 구조그래프(SG)로만, 청크 증거가 있는 의미 지식만 KG로. 구조 통계를 KG/relation VDB에 직접 주입하면 base 검색이 오염된다는 ver4 교훈(All@20 99.1→98.1 하락)에서 도출.
- **SG 3층 증거 모델** — L1 `explicit_ref`(청크 내 파일명/ID 언급 결정적 스캔) / L2 `co_retrieval`(quality-gated 공동검색 통계, 강화·감쇠) / L3 `llm_curated`(백그라운드 LLM 제안, 증거 인용 필수). 저품질 쿼리 결과로는 학습하지 않음.
- **티어드 analyzer + PlanCache** — 캐시 히트 시 핫패스 LLM 0회(p50 ~40ms), 신규 복잡 쿼리만 LLM 1콜(rewrite+분해+전략 단일 JSON). ε-greedy StrategyMemory가 쿼리 클러스터별 검색 모드를 학습.
- **v5.21 핵심: 넓은 관찰 창 복원** — 서빙 출력은 불변으로 두고, 내부 랭킹 max(20, top_k), 문서랭킹 40위까지 observed 로그, L2 강화 16문서·evolver 후보 12문서로 확대. 선택적 에코와 quality 게이트가 확대된 창의 잡음을 방어.
- **회귀 가드** — KG 변경은 사이클 체크포인트, All/ValA 지표 하락 시 자동 롤백. 벤치마크 정답 미참조(그라운드룰 1) 하에서만 진화.

## 장점

- 반복 실행 시 지표가 개선 추세: v5.20 장기런(10-iter) 기준 ValB co@10 61.1→74.4 마감, 10회 무롤백 — 장기 자기진화 실증. v5.21은 이 학습 루프의 관찰 창을 넓혀 진화 재료를 확대.
- 균일 저지연: 검증 런 p50 37~43ms / p95 44~59ms, ValB 핫패스 LLM 호출 0회 (cache/rules 티어만으로 서빙).
- All@20 100 / ValA@20 99.4 유지 — 구조 진화가 base 검색을 오염시키지 않음(투트랙 분리 효과).

## 단점

- co@5(상위 정밀)는 33~57%로 변동 폭이 큼 — 상위 랭킹 안정화 미해결.
- v5.21 단독 10-iter 완주 검증 미완: 5-iter까지 co@10 60.0→72.2→64.4로 변동, 이후 G-시리즈 캠페인으로 전환되며 넓은 관찰창은 G1(v5.22) 스택에 흡수됨.
- L1 `explicit_ref`는 참조가 기계친화적으로 표기된 코퍼스에 특화된 지름길 — 일반화를 위해 후속 G-시리즈에서 L1 배제 스택을 별도 검증 중.

## 노트

- 재현: `python3 harness_pjt/evaluation/scenario2-ver5/eval_v5.py --fresh --iterations N` (골든 `harness_pjt/rag_data/`는 읽기 전용, `--fresh`가 작업 카피 생성)
- 코드 스냅샷: 태그 `structrag-v5.21` (d3ba38f), 사이클별 판정·수치는 `harness_pjt/structrag/DESIGN_HISTORY.md` 및 `harness_pjt/evaluation/scenario2-ver5/results/`
- 내부 검증 지표(All@20/ValA/ValB co@k)는 SHS Final Benchmark 100과 별개의 retrieval 벤치마크 — frontmatter 수치는 SHS 공식 런 결과로 기입할 것.
