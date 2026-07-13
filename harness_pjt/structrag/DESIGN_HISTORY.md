# StructRAG (ver5) — Design History & Ground Rules

> **이 문서는 반드시 읽고 작업할 것.** 과거 실패를 반복하지 않기 위한 기록이다.
> 모든 설계 변경·evolve 사이클 결과는 이 문서 하단 History에 append한다.

## 그라운드룰 (불변)

1. **정답 미참조**: evolving 시 벤치마크 정답(expected_card/expected_linked/expected_sources)을 보고 반영하는 방식 절대 금지. 어떤 데이터가 와도 제너럴하게 동작해야 함.
2. **객관적 증거만 사용**: 쿼리 텍스트, 청크 내용, 쿼리-리트리브 매칭 결과, 룰기반 quality 점수만 학습 입력으로 사용.
3. **돌릴수록 좋아져야 함**: 벤치마크 반복 실행 시 지표가 개선 추세여야 함 (백그라운드 evolver의 존재 이유).
4. **모든 전략은 근거 필수**: research_notes.md에 문헌 근거 기록. 근거 없는 전략 도입 금지.
5. **모든 변경은 히스토리 필수**: evolution_log.jsonl(기계) + 이 문서 History(사람) 양쪽에 사유·증거·변경점 기록.
6. **골든 RAG 데이터 불변**: `harness_pjt/rag_data/`는 읽기 전용. 실험은 카피본에서만.

## ver1~ver4 실패/교훈 요약

| ver | 전략 | 결과 | 교훈 |
|---|---|---|---|
| ver1 | LangGraph evolve 5전략 (co-retrieval 강화, gap fill, shortcut, …) | ValB co@20 +1.1% | 엔티티 레벨 소규모 수선은 문서 간 co-retrieval 문제에 효과 미미 |
| ver2 | 벤치마크 실패 문항 기반 브리지 청크 주입 | co@20 +43.3% | **그라운드룰 1 위반(정답 참조) → 폐기.** 수치가 좋아도 무효 |
| ver3 | 엔티티 간 크로스 문서 엣지 주입 | +0.0% | 엔티티 엣지로는 문서 레벨 구조가 검색 랭킹에 도달하지 못함 |
| ver4 | DA(문서앵커) + DCSG + CDRB를 **원본 KG/relation VDB에 직접 주입** | co@20 +2.3%, **All@20 99.1→98.1 하락** | 구조 통계를 KG에 녹이면 relation VDB가 오염되어 base 검색에 노이즈. 구조와 의미는 저장소를 분리해야 함 |

## ver5 핵심 결정과 근거

### D1. 투트랙 진화 (SG / KG 분리)
- **경계 규칙**: 문서 레벨 구조관계·공동검색 통계 → SG(별도 저장소)로만. 청크 증거가 있는 의미적 엔티티/릴레이션 지식 → KG로.
- 근거: ver4에서 구조 엣지의 KG 주입이 All@20을 하락시킴 (관측 사실). ver4의 실패는 "KG 진화" 자체가 아니라 경계 위반.

### D2. 구조그래프(SG) 3층 증거 모델
- L1 `explicit_ref`: 청크 텍스트 내 파일명/ID 언급 정규식 스캔 (결정적, LLM 없음).
  검증(2026-07-09): 골든 청크 912개 스캔 → doc→doc 엣지 1,699개, Val-B 90쌍 중 89쌍 커버.
  이는 벤치마크 정답이 아니라 문서 원본 콘텐츠에서 나온 것 — 그라운드룰 1 충족.
- L2 `co_retrieval`: quality ≥ τ인 쿼리의 공동검색 통계로 강화/감쇠. 크로스레퍼런스가 없는 코퍼스에서도 작동하는 일반 경로.
- L3 `llm_curated`: 백그라운드 LLM이 증거번들(쿼리+청크)을 보고 타입드 관계 제안. 증거 인용 필수.
- **저품질 쿼리 결과로는 학습하지 않는다** (L2 quality gate) — 나쁜 검색이 구조를 오염시키는 것 방지.

### D3. Analyzer는 LLM 기반, 단 티어드
- Tier 0: PlanCache 히트 시 LLM 0회 (고속). Tier 1: LLM 1콜(rewrite+분해+전략+스코프 힌트 단일 JSON). Tier 2: 만성 저품질만 백그라운드 심층 분석.
- 속도 요구(1순위)와 agentic 요구를 티어링으로 양립.

### D4. 룰기반 퀄리티 점수 (LLM 없음)
- QPP post-retrieval 예측자(NQC/WIG 계열) + 융합 합의도 + 텀 커버리지 조합. research_notes.md §3.

### D5. 스코프는 soft-boost, 하드필터 금지
- 구조그래프 힌트가 틀렸을 때 리콜이 죽는 것을 방지. 스코프 확신이 높을 때만 boost 강화.

### D6. LLMwiki baked-in
- LLM 큐레이션 산출물은 그래프에 저장: KG 노드 설명 위키화(임베딩 품질↑), 엔티티 병합, SG 타입드 엣지·문서 프로필.
- 쿼리 시점에는 위키 탐색 없이 이미 반영된 그래프에서 디터미니스틱하게 결과가 나옴.

### D7. 회귀 가드
- KG 변경은 사이클 단위 체크포인트. All@20/ValA@20 하락 시 해당 배치 롤백 + 사유 기록.

---

## History

### 2026-07-09 — ver5 설계 확정
- ver1~4 결과 분석 후 전면 재설계. 위 D1~D7 결정.
- 검증 스파이크: 골든 청크 스캔으로 L1 커버리지 확인 (1,699 엣지, Val-B 89/90).
- 다음: structure_graph.py 구현 → quality.py → analyzer.py → retriever.py → evolver.py → ver5 eval.

### 2026-07-09 09:18:31 — ver5 iteration 1 (rules only)
- All@20 99.0% · ValA@20 86.1% · ValB co@20/10/5 77.8/52.2/26.7%
- latency p50/p95: all=167.3/189.1ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 2426, 'l1_explicit': 1673, 'l2_co_retrieval': 1369, 'l3_llm_curated': 0, 'profiles': 0} · plan_cache: {'entries': 80, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2282}, 's_llm': {}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 0, 'good': 179, 'attention': 103, 'elapsed_s': 0.0}

### 2026-07-09 — iteration 1 진단 및 파이프라인 수정 (fresh 재시작)
- 관측: ValB co@20 54.4→77.8 (+23.4pt, SG 확장 효과 확인) / All@20 99.0 유지 / **ValA@20 86.1 (기준선 99.4 대비 회귀)**
- 원인 3건 (query_log 실패 25건 분석):
  1. 융합 top-20 청크가 소수 문서에 집중 → 문서 레벨 리콜 손실 (ver4는 파일 단위 union이었음)
  2. 키워드-헤비 한국어 쿼리(SECDED, RD_MAC 등 희귀 토큰)가 mix 단독 라우팅되고 BM25 투표가 top-10로 제한되어 희석
  3. 파일명 토큰 2개 겹침만으로 스코프 시드 인정 → 무관 문서 soft-boost
- 수정 (일반 원리 기반, 정답 미참조):
  1. 최종 선택에 문서당 청크 상한 2 (IR 표준 diversification) + backfill
  2. bm25/vector 독립 랭킹을 top_k 전폭으로 융합 (RRF 동등 투표)
  3. match_docs_by_tokens min_overlap 2→3 (시드 정밀도)
- 조치: 상태 초기화 후 --fresh 재실행 (iteration 비교가능성 유지)

### 2026-07-09 09:21:54 — ver5 iteration 1 (rules only)
- All@20 98.1% · ValA@20 86.1% · ValB co@20/10/5 78.9/47.8/26.7%
- latency p50/p95: all=165.2/204.3ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 2417, 'l1_explicit': 1673, 'l2_co_retrieval': 1311, 'l3_llm_curated': 0, 'profiles': 0} · plan_cache: {'entries': 82, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2220}, 's_llm': {}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 0, 'good': 179, 'attention': 104, 'elapsed_s': 0.0}

### 2026-07-09 — 2차 진단: RRF 딜루션 → 채널 대표성 보장
- 관측: 1차 수정 후에도 ValA 86.1 동일. 실패 15건 모드 분해 결과 **bm25 단독 14/15 히트, vector/mix 0/15**.
- 원인: 다중 리스트 RRF에서 한 채널만 찾은 정답 문서가 약한 랭커 2개가 합의한 오답에 투표로 밀림 (RRF 딜루션).
- 수정: 각 랭킹 채널의 top-3 문서는 최종 top_k 윈도우에 반드시 포함 (fused tail 교체, floor top_k/2).
  근거: 상보적 랭커 융합에서 정밀 채널 보호 — hybrid(2-list)는 14/15를 살렸다는 관측이 직접 증거.

### 2026-07-09 09:25:06 — ver5 iteration 1 (rules only)
- All@20 98.1% · ValA@20 85.6% · ValB co@20/10/5 82.2/50.0/26.7%
- latency p50/p95: all=163.3/183.6ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 2417, 'l1_explicit': 1673, 'l2_co_retrieval': 1305, 'l3_llm_curated': 0, 'profiles': 0} · plan_cache: {'entries': 83, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2191}, 's_llm': {}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 0, 'good': 181, 'attention': 102, 'elapsed_s': 0.0}

### 2026-07-09 — 3차 진단: concept 기본 모드 mix→hybrid
- 관측: 채널 top-3 보장 후에도 ValA 85.6 (카드가 bm25 랭크 3 밖). co@20은 82.2로 상승 — co-retrieval 개선은 mix가 아니라 SG EXPAND가 견인함을 확인.
- 원인: mix 랭킹을 융합에 포함하는 것 자체가 키워드 리콜형 쿼리에서 딜루션 유발 (실패 분해: hybrid 14/15 vs mix 0/15).
- 수정: INTENT_MODES concept 기본을 hybrid로. mix는 cross_doc 인텐트와 밴딧 탐험으로만 진입. mix의 문서횡단 가치는 SG EXPAND가 대체.

### 2026-07-09 09:27:24 — ver5 iteration 1 (rules only)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 83.3/50.0/30.0%
- latency p50/p95: all=38.4/44.6ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 2468, 'l1_explicit': 1672, 'l2_co_retrieval': 1380, 'l3_llm_curated': 0, 'profiles': 0} · plan_cache: {'entries': 78, 'hits': 1, 'hit_rate': 0.003}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2053}, 's_llm': {}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 0, 'good': 180, 'attention': 102, 'elapsed_s': 0.0}

### 2026-07-09 09:45:14 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 90.0/50.0/38.9%
- latency p50/p95: all=40.8/49.6ms · tiers(valB)={'cache': 7, 'llm': 76, 'rules': 7}
- SG: {'docs': 572, 'edges': 2921, 'l1_explicit': 1672, 'l2_co_retrieval': 1962, 'l3_llm_curated': 5, 'profiles': 3} · plan_cache: {'entries': 133, 'hits': 79, 'hit_rate': 0.21}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2732}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 168, 'attention': 110, 'elapsed_s': 140.4}

### 2026-07-09 — 4차 진단: ValB 잔여 미스 9건 (iter2 시점) 분석
- 패턴: 9건 중 8건이 "카드 히트, linked(jira) 미스".
- 원인 1: L1 빌더의 ID→소유파일 매핑이 단일 (FA-2245가 FA-2245.html과 FA-2245 리포트 docx 양쪽 파일명에 있으면 한쪽만 엣지). → 오너 최대 3개 전부에 엣지.
- 원인 2: 카드의 참조가 4~6개일 때 EXPAND 이웃 top-3에 linked가 밀림. → EXPAND_NEIGHBORS 3→4.
- 적용 시점: iteration 5부터 (L1 rebuild 필요 — eval_v5에 rebuild 스텝 추가).

### 2026-07-09 14:19:02 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 92.2/46.7/33.3%
- latency p50/p95: all=40.5/11518.4ms · tiers(valB)={'cache': 23, 'llm': 66, 'rules': 1}
- SG: {'docs': 572, 'edges': 3134, 'l1_explicit': 1672, 'l2_co_retrieval': 2238, 'l3_llm_curated': 9, 'profiles': 6} · plan_cache: {'entries': 155, 'hits': 133, 'hit_rate': 0.354}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2550}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 169, 'attention': 67, 'elapsed_s': 112.2}

### 2026-07-09 14:43:32 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 93.3/45.6/32.2%
- latency p50/p95: all=41.0/11235.3ms · tiers(valB)={'cache': 29, 'llm': 59, 'rules': 2}
- SG: {'docs': 572, 'edges': 3257, 'l1_explicit': 1672, 'l2_co_retrieval': 2386, 'l3_llm_curated': 12, 'profiles': 9} · plan_cache: {'entries': 171, 'hits': 155, 'hit_rate': 0.412}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2284}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 173, 'attention': 69, 'elapsed_s': 147.2}

## 그라운드룰 추가 (2026-07-09, 유저 피드백)

7. **무개입 프로토콜**: "돌릴수록 좋아진다"의 증명은 코드 동결 상태에서 시스템 자체 진화(evolver)만으로 이뤄져야 한다. iteration 사이에 사람이 로직을 수정하면 그 곡선은 자기진화 증거로 무효. 개발 단계(버그픽스·설계수정)와 검증 단계(동결+무개입 N회 실행)를 명확히 분리하고, 검증 런은 항상 --fresh부터 무개입으로 수행한다.

### 2026-07-09 — 개발 단계 종료, 코드 동결 (v5.1)
- 위 그라운드룰 7에 따라 지금까지의 iter1~4 곡선(83.3→90.0→92.2→93.3)은 "개발 참고용"으로 격하.
  (iter2~4는 단일 프로세스/동결 코드로 돌아 자기진화 효과가 맞지만, iter1 재베이스라인 3회에 개발 수정이 섞임)
- 동결 시점 코드: L1 멀티오너(v2) + EXPAND_NEIGHBORS=4 포함. 이후 수정 금지.
- 검증 프로토콜: --fresh로 골든에서 시작, 5회 연속 무개입 실행. 이 곡선만이 자기진화의 증거.

### 2026-07-09 15:11:57 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 95.6/46.7/38.9%
- latency p50/p95: all=40.2/52.4ms · tiers(valB)={'cache': 0, 'llm': 83, 'rules': 7}
- SG: {'docs': 572, 'edges': 2737, 'l1_explicit': 1719, 'l2_co_retrieval': 1685, 'l3_llm_curated': 5, 'profiles': 3} · plan_cache: {'entries': 104, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2396}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 162, 'attention': 109, 'elapsed_s': 133.4}

### 2026-07-09 15:26:29 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 93.3/45.6/33.3%
- latency p50/p95: all=39.6/46.3ms · tiers(valB)={'cache': 25, 'llm': 58, 'rules': 7}
- SG: {'docs': 572, 'edges': 2920, 'l1_explicit': 1719, 'l2_co_retrieval': 1916, 'l3_llm_curated': 10, 'profiles': 6} · plan_cache: {'entries': 135, 'hits': 104, 'hit_rate': 0.277}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2613}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 160, 'attention': 110, 'elapsed_s': 136.1}

### 2026-07-09 15:50:59 — ver5 iteration 3 (LLM on)
- All@20 99.0% · ValA@20 98.9% · ValB co@20/10/5 91.1/45.6/23.3%
- latency p50/p95: all=42.6/15567.0ms · tiers(valB)={'cache': 32, 'llm': 24, 'rules': 34}
- SG: {'docs': 572, 'edges': 3183, 'l1_explicit': 1719, 'l2_co_retrieval': 2237, 'l3_llm_curated': 10, 'profiles': 6} · plan_cache: {'entries': 164, 'hits': 135, 'hit_rate': 0.359}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2389}, 's_llm': {'typed_edges': 0, 'profiles': 0}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 173, 'attention': 56, 'elapsed_s': 20.4}
- **REGRESSION GUARD fired → KG rolled back** (사유는 evolution_log 참조)

### 2026-07-09 15:54:34 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 86.7/51.1/30.0%
- latency p50/p95: all=41.4/1870.8ms · tiers(valB)={'cache': 32, 'llm': 0, 'rules': 58}
- SG: {'docs': 572, 'edges': 3202, 'l1_explicit': 1719, 'l2_co_retrieval': 2257, 'l3_llm_curated': 10, 'profiles': 6} · plan_cache: {'entries': 165, 'hits': 164, 'hit_rate': 0.436}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2258}, 's_llm': {'typed_edges': 0, 'profiles': 0}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 178, 'attention': 89, 'elapsed_s': 19.3}

### 2026-07-09 15:58:27 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 88.9/54.4/31.1%
- latency p50/p95: all=42.8/1957.0ms · tiers(valB)={'cache': 32, 'llm': 0, 'rules': 58}
- SG: {'docs': 572, 'edges': 3230, 'l1_explicit': 1719, 'l2_co_retrieval': 2289, 'l3_llm_curated': 10, 'profiles': 6} · plan_cache: {'entries': 165, 'hits': 165, 'hit_rate': 0.439}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2295}, 's_llm': {'typed_edges': 0, 'profiles': 0}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 182, 'attention': 89, 'elapsed_s': 20.5}

### 2026-07-09 — 검증 런 완료 (동결 v5.1, --fresh 무개입 5회)
- 결과표·상세 분석: evaluation/scenario2-ver5/results/summary_report.md
- 요약: co@20 전구간 86.7~95.6 (ver4 54.4 대비 압도), All/ValA 유지, iter3 회귀가드 발동→롤백→회복 (가드 실증).
  자기진화 신호: co@10 +7.7pt, 캐시히트 0→44%, p50 12s→1.9s. 
- 문제: ① co@20 하락추세 (L2가 EXPAND 슬롯에서 L1과 경합) ② quality 신호의 co-retrieval 무감지
  ③ LLM 장애(CLI 사용량한도) 미가시화 ④ 승격게이트 보수적.
- 다음 개발 사이클: C1(L1 EXPAND 우선권) → C2(구조 정합 quality 신호) → C3(LLM 헬스) → C4(재조우 비교 승격).
  개발 후 재동결 → --fresh 무개입 런으로 재검증 (그라운드룰 7).

### 2026-07-09 — 개발 사이클 2 (v5.2): 검증 런 발견 문제 수정 후 재동결
- C5 (신규, co@5/10 직접 원인): EXPAND 청크를 결과 뒤에 append하던 방식 폐기.
  SG 이웃 문서가 앵커 문서의 융합 점수를 엣지 강도로 할인 상속(score propagation)하여
  단일 doc 랭킹에서 경쟁. 근거: 하이퍼링크 그래프 경로점수 전파 (research_notes §1).
  → linked 문서가 카드 바로 뒤 랭크로 진입 가능, co@5/10 개선 겨냥.
- C1: get_neighbors에서 explicit_ref(L1) 엣지가 학습 레이어보다 무조건 우선 (문서 자신의
  크로스레퍼런스는 사실, co-retrieval 통계는 상관관계일 뿐).
- C2: quality에 struct_coverage(상위 base 문서의 강한 L1 이웃 동반율, 사전확장 기준) 추가,
  가중치 0.20. 승격/강화 게이트가 co-retrieval을 감지하게 됨.
- C4: PlanCache — 미드퀄리티 LLM 플랜은 재사용(같은 패턴 재-LLM 낭비 제거),
  미드퀄리티 룰 플랜은 LLM 업그레이드 기회 유지.
- C6: ε-greedy annealing (ε_eff = ε/(1+visits/5)) — 학습된 클러스터의 무작위 모드 플립 제거.
- C3: eval 하네스 LLM 헬스 카운터 + 연속실패 8회 백오프 + iteration 레코드에 노출.
- 스모크 10/10 (이전 9/10), 유닛 테스트 전체 통과. **v5.2 동결 — --fresh 무개입 5회 재검증 시작.**

### 2026-07-09 20:52:01 — ver5 iteration 1 (LLM on)
- All@20 98.1% · ValA@20 97.8% · ValB co@20/10/5 68.9/53.3/41.1%
- latency p50/p95: all=44.9/63.6ms · tiers(valB)={'cache': 0, 'llm': 83, 'rules': 7}
- SG: {'docs': 572, 'edges': 2672, 'l1_explicit': 1719, 'l2_co_retrieval': 1858, 'l3_llm_curated': 4, 'profiles': 3} · plan_cache: {'entries': 332, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2287}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 153, 'attention': 44, 'elapsed_s': 110.1}

### 2026-07-09 20:55:36 — ver5 iteration 2 (LLM on)
- All@20 99.0% · ValA@20 98.9% · ValB co@20/10/5 68.9/50.0/37.8%
- latency p50/p95: all=46.5/86.0ms · tiers(valB)={'cache': 79, 'llm': 4, 'rules': 7}
- SG: {'docs': 572, 'edges': 2846, 'l1_explicit': 1719, 'l2_co_retrieval': 2125, 'l3_llm_curated': 6, 'profiles': 6} · plan_cache: {'entries': 335, 'hits': 169, 'hit_rate': 0.449}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2286}, 's_llm': {'typed_edges': 2, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 151, 'attention': 41, 'elapsed_s': 147.9}

### 2026-07-09 21:16:34 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 72.2/52.2/41.1%
- latency p50/p95: all=47.1/9513.6ms · tiers(valB)={'cache': 36, 'llm': 51, 'rules': 3}
- SG: {'docs': 572, 'edges': 3024, 'l1_explicit': 1719, 'l2_co_retrieval': 2372, 'l3_llm_curated': 8, 'profiles': 9} · plan_cache: {'entries': 361, 'hits': 115, 'hit_rate': 0.306}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2101}, 's_llm': {'typed_edges': 2, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 151, 'attention': 17, 'elapsed_s': 178.0}

### 2026-07-09 21:27:06 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 75.6/54.4/40.0%
- latency p50/p95: all=48.0/136.4ms · tiers(valB)={'cache': 65, 'llm': 22, 'rules': 3}
- SG: {'docs': 572, 'edges': 3066, 'l1_explicit': 1719, 'l2_co_retrieval': 2426, 'l3_llm_curated': 12, 'profiles': 12} · plan_cache: {'entries': 369, 'hits': 166, 'hit_rate': 0.441}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1987}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 152, 'attention': 9, 'elapsed_s': 134.9}

### 2026-07-09 21:40:49 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 98.3% · ValB co@20/10/5 76.7/53.3/40.0%
- latency p50/p95: all=47.0/133.3ms · tiers(valB)={'cache': 42, 'llm': 42, 'rules': 6}
- SG: {'docs': 572, 'edges': 3144, 'l1_explicit': 1719, 'l2_co_retrieval': 2522, 'l3_llm_curated': 16, 'profiles': 15} · plan_cache: {'entries': 372, 'hits': 138, 'hit_rate': 0.367}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1927}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 153, 'attention': 20, 'elapsed_s': 166.8}
- **REGRESSION GUARD fired → KG rolled back** (사유는 evolution_log 참조)

### 2026-07-09 — v5.2 검증 런 결과 및 개발 사이클 3 (v5.3)
- v5.2 검증 (무개입 5회): **안정화 성공** — co@5 stdev 5.7→1.35, co@10 stdev→1.67, co@20 단조상승 68.9→76.7 (v5.1의 하락 추세 해소). co@5 평균 31→40, co@10 평균 ~49→52.6. LLM 308콜 무장애(C3 검증), 회귀가드 iter5 발동·롤백 정상.
- **문제**: co@20 레벨 후퇴 (v5.1 평균 91 → v5.2 평균 72.5). 원인 — C5 점수전파에서 확장 후보(최대 20개)가 앵커 점수 0.76배로 top-20 윈도우에 과잉 편입, 올바른 base 문서를 랭크 밖으로 밀어냄. co@5↑/co@20↓ 패턴이 이 기전의 시그니처.
- v5.3 수정 (확장 절제, 쿼리 근거 기반):
  1. 관련성 변조: 상속점수 × (0.4+0.6·min(1,hits/3)) — 쿼리 텀 매치 없는 이웃은 base를 못 이김
  2. 윈도우 예산: 확장 문서 상위 EXPAND_BUDGET(6)개만 랭킹 편입
- 유닛 전체 + 스모크 10/10 통과. **v5.3 동결 — --fresh 무개입 5회 재검증.**

### 2026-07-09 22:04:43 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 65.6/48.9/35.6%
- latency p50/p95: all=44.8/56.2ms · tiers(valB)={'cache': 0, 'llm': 83, 'rules': 7}
- SG: {'docs': 572, 'edges': 2682, 'l1_explicit': 1719, 'l2_co_retrieval': 1780, 'l3_llm_curated': 5, 'profiles': 3} · plan_cache: {'entries': 330, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2333}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 152, 'attention': 46, 'elapsed_s': 144.7}

### 2026-07-09 22:09:20 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 64.4/44.4/36.7%
- latency p50/p95: all=45.6/52.2ms · tiers(valB)={'cache': 78, 'llm': 5, 'rules': 7}
- SG: {'docs': 572, 'edges': 2809, 'l1_explicit': 1719, 'l2_co_retrieval': 1963, 'l3_llm_curated': 9, 'profiles': 6} · plan_cache: {'entries': 336, 'hits': 167, 'hit_rate': 0.444}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2280}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 150, 'attention': 41, 'elapsed_s': 138.7}

### 2026-07-09 22:11:22 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 98.3% · ValB co@20/10/5 67.8/46.7/32.2%
- latency p50/p95: all=45.8/2046.8ms · tiers(valB)={'cache': 43, 'llm': 0, 'rules': 47}
- SG: {'docs': 572, 'edges': 2861, 'l1_explicit': 1719, 'l2_co_retrieval': 2036, 'l3_llm_curated': 9, 'profiles': 6} · plan_cache: {'entries': 341, 'hits': 125, 'hit_rate': 0.332}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2029}, 's_llm': {'typed_edges': 0, 'profiles': 0}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 159, 'attention': 40, 'elapsed_s': 0.2}
- **REGRESSION GUARD fired → KG rolled back** (사유는 evolution_log 참조)

### 2026-07-09 22:12:03 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 71.1/48.9/30.0%
- latency p50/p95: all=44.2/133.5ms · tiers(valB)={'cache': 38, 'llm': 0, 'rules': 52}
- SG: {'docs': 572, 'edges': 2918, 'l1_explicit': 1719, 'l2_co_retrieval': 2102, 'l3_llm_curated': 9, 'profiles': 6} · plan_cache: {'entries': 341, 'hits': 125, 'hit_rate': 0.332}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2064}, 's_llm': {'typed_edges': 0, 'profiles': 0}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 156, 'attention': 45, 'elapsed_s': 0.2}

### 2026-07-09 22:12:48 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 98.3% · ValB co@20/10/5 68.9/48.9/33.3%
- latency p50/p95: all=45.2/143.2ms · tiers(valB)={'cache': 38, 'llm': 0, 'rules': 52}
- SG: {'docs': 572, 'edges': 2927, 'l1_explicit': 1719, 'l2_co_retrieval': 2115, 'l3_llm_curated': 9, 'profiles': 6} · plan_cache: {'entries': 341, 'hits': 128, 'hit_rate': 0.34}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2025}, 's_llm': {'typed_edges': 0, 'profiles': 0}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 157, 'attention': 43, 'elapsed_s': 0.2}
- **REGRESSION GUARD fired → KG rolled back** (사유는 evolution_log 참조)

### 2026-07-10 — v5.3 검증 결과 및 개발 사이클 4 (v5.4)
- v5.3 검증 (무개입 5회): co@20 평균 67.6 / co@10 47.6 / co@5 33.6 — **v5.2보다 전면 후퇴.**
- **교훈**: 관련성(어휘) 변조를 모든 확장에 일괄 적용한 것이 오류. linked 문서는 원래 쿼리와
  어휘적으로 멀다 — 그게 구조 확장의 존재 이유인데 어휘 게이트가 진짜 링크를 노이즈와 함께 차단.
- 부가: iter3부터 CLI 사용량 한도 재발 — C3 백오프·헬스 카운터가 설계대로 작동 (9→25 실패 기록,
  iteration 그라인딩 없이 완주). 단 비교는 LLM 가용성에 부분 오염.
- v5.4 수정 (출처 차등 확장):
  - L1(explicit_ref) 이웃: 문서 자신의 선언 → 어휘 게이트 없이 상속점수 ×(0.5+0.5w)로 편입
  - L2/L3(학습) 이웃: 통계적 상관 → 관련성 변조 유지
  - EXPAND_BUDGET(6) 전역 상한 유지 (v5.2 과잉편입 교훈)
- 유닛 전체 + 스모크 10/10. **v5.4 동결 — --fresh 무개입 5회 재검증 (CLI 복구 확인 후 시작).**

### 2026-07-10 01:01:01 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 98.3% · ValB co@20/10/5 70.0/52.2/37.8%
- latency p50/p95: all=44.1/59.0ms · tiers(valB)={'cache': 0, 'llm': 83, 'rules': 7}
- SG: {'docs': 572, 'edges': 2710, 'l1_explicit': 1719, 'l2_co_retrieval': 1901, 'l3_llm_curated': 3, 'profiles': 3} · plan_cache: {'entries': 329, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2253}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 151, 'attention': 47, 'elapsed_s': 155.0}

### 2026-07-10 01:05:03 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 72.2/51.1/36.7%
- latency p50/p95: all=46.6/60.0ms · tiers(valB)={'cache': 80, 'llm': 3, 'rules': 7}
- SG: {'docs': 572, 'edges': 2835, 'l1_explicit': 1719, 'l2_co_retrieval': 2095, 'l3_llm_curated': 7, 'profiles': 6} · plan_cache: {'entries': 333, 'hits': 175, 'hit_rate': 0.465}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2212}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 151, 'attention': 44, 'elapsed_s': 175.0}

### 2026-07-10 01:26:47 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 75.6/48.9/41.1%
- latency p50/p95: all=46.0/9869.2ms · tiers(valB)={'cache': 31, 'llm': 56, 'rules': 3}
- SG: {'docs': 572, 'edges': 3008, 'l1_explicit': 1719, 'l2_co_retrieval': 2343, 'l3_llm_curated': 10, 'profiles': 9} · plan_cache: {'entries': 362, 'hits': 111, 'hit_rate': 0.295}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2141}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 152, 'attention': 18, 'elapsed_s': 174.5}

### 2026-07-10 01:38:08 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 77.8/53.3/41.1%
- latency p50/p95: all=46.7/75.9ms · tiers(valB)={'cache': 56, 'llm': 31, 'rules': 3}
- SG: {'docs': 572, 'edges': 3111, 'l1_explicit': 1719, 'l2_co_retrieval': 2471, 'l3_llm_curated': 13, 'profiles': 12} · plan_cache: {'entries': 370, 'hits': 159, 'hit_rate': 0.423}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1890}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 153, 'attention': 10, 'elapsed_s': 130.1}

### 2026-07-10 01:50:21 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 78.9/56.7/42.2%
- latency p50/p95: all=48.7/112.1ms · tiers(valB)={'cache': 41, 'llm': 42, 'rules': 7}
- SG: {'docs': 572, 'edges': 3167, 'l1_explicit': 1719, 'l2_co_retrieval': 2550, 'l3_llm_curated': 15, 'profiles': 15} · plan_cache: {'entries': 372, 'hits': 140, 'hit_rate': 0.372}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1863}, 's_llm': {'typed_edges': 2, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 152, 'attention': 23, 'elapsed_s': 149.8}

### 2026-07-10 — v5.4 검증 결과 및 개발 사이클 5 (v5.5)
- v5.4 검증 (무개입 5회): **최고의 진화 곡선** — co@20 70.0→78.9 5회 연속 단조상승,
  co@10 52.2→56.7, co@5 37.8→42.2, All 100 고정, ValA 99.4 수렴, 롤백 0, LLM 318콜 무장애.
  출처 차등 확장(L1 무게이트)이 v5.3 문제를 해결.
- 남은 갭: co@20 절대 레벨(70~79)이 v5.1(86~95)보다 낮음. 기전 — EXPAND_BUDGET=6 하드컷이
  허브성 앵커의 다수 L1 이웃에 밀린 진짜 링크를 잘라냄 (v5.1은 확장 전부 append라 co@20은 잡았음).
- v5.5 수정 (하이브리드 안전망): 상위 6개는 랭킹 경쟁(co@5/10 담당) 유지 +
  예산 밖 확장 후보 최대 8개(EXPAND_TAIL)는 윈도우 뒤에 1청크씩 append (co@20 안전망).
- 유닛 전체 + 스모크 10/10. **v5.5 동결 — --fresh 무개입 5회 재검증.**

### 2026-07-10 02:08:45 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 73.3/58.9/42.2%
- latency p50/p95: all=39.8/50.0ms · tiers(valB)={'cache': 0, 'llm': 75, 'rules': 15}
- SG: {'docs': 572, 'edges': 2675, 'l1_explicit': 1719, 'l2_co_retrieval': 1914, 'l3_llm_curated': 0, 'profiles': 0} · plan_cache: {'entries': 328, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2301}, 's_llm': {'typed_edges': 0, 'profiles': 0}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 155, 'attention': 48, 'elapsed_s': 13.1}

### 2026-07-10 02:09:29 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 72.2/55.6/42.2%
- latency p50/p95: all=40.6/48.2ms · tiers(valB)={'cache': 71, 'llm': 0, 'rules': 19}
- SG: {'docs': 572, 'edges': 2759, 'l1_explicit': 1719, 'l2_co_retrieval': 2028, 'l3_llm_curated': 0, 'profiles': 0} · plan_cache: {'entries': 330, 'hits': 169, 'hit_rate': 0.449}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2321}, 's_llm': {'typed_edges': 0, 'profiles': 0}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 155, 'attention': 47, 'elapsed_s': 0.1}

### 2026-07-10 02:10:11 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 81.1/55.6/36.7%
- latency p50/p95: all=40.8/1783.1ms · tiers(valB)={'cache': 34, 'llm': 0, 'rules': 56}
- SG: {'docs': 572, 'edges': 2819, 'l1_explicit': 1719, 'l2_co_retrieval': 2108, 'l3_llm_curated': 0, 'profiles': 0} · plan_cache: {'entries': 331, 'hits': 124, 'hit_rate': 0.33}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2161}, 's_llm': {'typed_edges': 0, 'profiles': 0}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 156, 'attention': 55, 'elapsed_s': 0.2}

### 2026-07-10 02:10:54 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 98.3% · ValB co@20/10/5 76.7/52.2/33.3%
- latency p50/p95: all=40.5/1784.9ms · tiers(valB)={'cache': 33, 'llm': 0, 'rules': 57}
- SG: {'docs': 572, 'edges': 2835, 'l1_explicit': 1719, 'l2_co_retrieval': 2131, 'l3_llm_curated': 0, 'profiles': 0} · plan_cache: {'entries': 331, 'hits': 123, 'hit_rate': 0.327}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2103}, 's_llm': {'typed_edges': 0, 'profiles': 0}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 157, 'attention': 54, 'elapsed_s': 0.2}
- **REGRESSION GUARD fired → KG rolled back** (사유는 evolution_log 참조)

### 2026-07-10 02:11:37 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 98.3% · ValB co@20/10/5 77.8/53.3/35.6%
- latency p50/p95: all=42.1/1785.0ms · tiers(valB)={'cache': 32, 'llm': 0, 'rules': 58}
- SG: {'docs': 572, 'edges': 2856, 'l1_explicit': 1719, 'l2_co_retrieval': 2155, 'l3_llm_curated': 0, 'profiles': 0} · plan_cache: {'entries': 331, 'hits': 126, 'hit_rate': 0.335}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2110}, 's_llm': {'typed_edges': 0, 'profiles': 0}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 156, 'attention': 54, 'elapsed_s': 0.2}

### 2026-07-10 — v5.5 1차 런 판정 보류 → 재검증
- 1차 런: iter1부터 CLI 한도로 LLM 사망 (124콜 중 47실패, L3=0) — v5.4와 공정 비교 불가.
  LLM 없이도 co@20 평균 76.2 (피크 81.1) / co@10 평균 55.1 → tail 안전망 자체는 긍정 신호.
  단 co@5 stdev 4.0, ValA 가드 1회 발동. CLI 복구 확인 후 동일 코드로 재검증 (코드 무변경).

### 2026-07-10 10:00:54 — ver5 iteration 1 (LLM on)
- All@20 99.0% · ValA@20 97.2% · ValB co@20/10/5 72.2/60.0/45.6%
- latency p50/p95: all=38.1/43.8ms · tiers(valB)={'cache': 0, 'llm': 82, 'rules': 8}
- SG: {'docs': 572, 'edges': 2683, 'l1_explicit': 1719, 'l2_co_retrieval': 1896, 'l3_llm_curated': 3, 'profiles': 3} · plan_cache: {'entries': 327, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2279}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 154, 'attention': 49, 'elapsed_s': 116.1}

### 2026-07-10 10:05:10 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 97.8% · ValB co@20/10/5 72.2/57.8/46.7%
- latency p50/p95: all=39.2/44.9ms · tiers(valB)={'cache': 76, 'llm': 7, 'rules': 7}
- SG: {'docs': 572, 'edges': 2801, 'l1_explicit': 1719, 'l2_co_retrieval': 2058, 'l3_llm_curated': 3, 'profiles': 6} · plan_cache: {'entries': 332, 'hits': 166, 'hit_rate': 0.441}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2382}, 's_llm': {'typed_edges': 0, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 152, 'attention': 45, 'elapsed_s': 143.3}

### 2026-07-10 — 개발 사이클 6 (v5.6): 예산 슬롯 출처 예약
- v5.5 재검증 런은 유저 지시로 중도 폐기(예외 1회) — co@10 하락 기전이 명확해 바로 수정 진행.
- 기전: 확장 예산 6석의 순수 점수 경쟁에서, iteration마다 강화되는 L2(유효 0.6)가 L1 단일언급
  (유효 0.6)과 동점까지 추격 → 문서 선언 링크가 랭킹석에서 tail로 강등 → co@20↑/co@10↓ 시그니처.
- 수정: EXPAND_L1_SLOTS=4 — 예산 6석 중 4석은 L1 후보 전용, 2석만 학습 엣지 경쟁.
  한쪽이 비면 잔여석은 반대쪽에 개방. tail 안전망(v5.5) 유지.
- 유닛 전체 + 스모크 10/10. **v5.6 동결 — --fresh 무개입 5회 검증.**

### 2026-07-10 10:40:20 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 74.4/57.8/41.1%
- latency p50/p95: all=42.2/49.4ms · tiers(valB)={'cache': 0, 'llm': 83, 'rules': 7}
- SG: {'docs': 572, 'edges': 2681, 'l1_explicit': 1719, 'l2_co_retrieval': 1888, 'l3_llm_curated': 4, 'profiles': 3} · plan_cache: {'entries': 328, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2221}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 151, 'attention': 48, 'elapsed_s': 168.8}

### 2026-07-10 10:43:59 — ver5 iteration 2 (LLM on)
- All@20 99.0% · ValA@20 99.4% · ValB co@20/10/5 75.6/53.3/40.0%
- latency p50/p95: all=42.9/53.7ms · tiers(valB)={'cache': 78, 'llm': 5, 'rules': 7}
- SG: {'docs': 572, 'edges': 2808, 'l1_explicit': 1719, 'l2_co_retrieval': 2069, 'l3_llm_curated': 6, 'profiles': 6} · plan_cache: {'entries': 334, 'hits': 170, 'hit_rate': 0.452}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2209}, 's_llm': {'typed_edges': 2, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 149, 'attention': 43, 'elapsed_s': 140.4}
- **REGRESSION GUARD fired → KG rolled back** (사유는 evolution_log 참조)

### 2026-07-10 11:04:15 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 74.4/56.7/44.4%
- latency p50/p95: all=42.8/138.5ms · tiers(valB)={'cache': 31, 'llm': 56, 'rules': 3}
- SG: {'docs': 572, 'edges': 3022, 'l1_explicit': 1719, 'l2_co_retrieval': 2380, 'l3_llm_curated': 10, 'profiles': 9} · plan_cache: {'entries': 363, 'hits': 109, 'hit_rate': 0.29}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2039}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 155, 'attention': 16, 'elapsed_s': 128.0}

### 2026-07-10 11:17:16 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 70.0/54.4/43.3%
- latency p50/p95: all=43.8/10631.4ms · tiers(valB)={'cache': 56, 'llm': 31, 'rules': 3}
- SG: {'docs': 572, 'edges': 3111, 'l1_explicit': 1719, 'l2_co_retrieval': 2488, 'l3_llm_curated': 14, 'profiles': 12} · plan_cache: {'entries': 368, 'hits': 157, 'hit_rate': 0.418}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1934}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 157, 'attention': 12, 'elapsed_s': 166.5}

### 2026-07-13 08:05:06 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 73.3/52.2/43.3%
- latency p50/p95: all=43.7/134.6ms · tiers(valB)={'cache': 41, 'llm': 44, 'rules': 5}
- SG: {'docs': 572, 'edges': 3178, 'l1_explicit': 1719, 'l2_co_retrieval': 2562, 'l3_llm_curated': 18, 'profiles': 15} · plan_cache: {'entries': 370, 'hits': 138, 'hit_rate': 0.367}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1834}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 156, 'attention': 21, 'elapsed_s': 117.7}

### 2026-07-10 — v5.6 검증 완료 및 승격 판정
- v5.6 (무개입 5회, LLM 328콜 무장애): co@5 42.4±1.81 / co@10 54.9±2.33 / co@20 73.5±2.14.
- vs v5.4: co@5 +2.6, co@10 +2.5, 전 지표 stdev 감소. **co@10 하락 시그니처 소멸** (L1 슬롯 예약 효과 확증).
  co@20 평균은 -1.4 (74.9→73.5) — tail 안전망에도 소폭 양보.
- **판정: v5.6 승격** — 목표(소형 top-k 정답률 + iteration 안정성)에 직접 부합.
  co@20 최우선 시나리오에서는 structrag-v5.4 태그 사용.
- 다음 개선 후보(미착수): co@20 회복 — EXPAND_TAIL 후보의 doc-rank 삽입 위치 최적화,
  iter2 유형의 Track K 회귀(가드 1회 발동) 원인 분석.

### 2026-07-13 — v5.6 검증 완주 및 판정
- v5.6 (무개입 5회): co@20 평균 73.5 (stdev 2.14) / co@10 평균 54.9 (2.33) / co@5 평균 42.4 (1.81)
- vs v5.4: **소수 탑케이 정확도와 분산 모두 v5.6 우세** (co@5 +2.6, co@10 +2.5, 전 지표 stdev 축소:
  co@20 3.75→2.14). 반면 **추세는 v5.4 우세** (v5.4 co@20 +8.9 단조상승 vs v5.6 횡보,
  co@10 기울기 v5.4 +1.2/iter vs v5.6 −1.0/iter — 하락 시그니처 완전 제거엔 실패).
- 판정 (유저 확정): **v5.6 = 현재 운영본** (태그 structrag-v5.6) — 소형 top-k 정확도와 전지표
  분산 축소가 서비스 우선순위에 부합 (co@10 등락은 v5.4와 동일 최저점 52.2 내 stdev 수준 wobble로
  해석). v5.4는 co@20 최우선 운영점 대안으로 태그 보존 (structrag-v5.4). 두 프로파일 병합
  (co@10 드리프트 근원 — scope boost의 L2 성장 의존성 의심)은 차기 사이클 과제.
- 부기: iter2 회귀가드 1회 발동·롤백 정상. LLM 328콜 무장애. iter5 elapsed는 세션 유휴 아티팩트.

### 2026-07-10 — v5.6 재판정 및 개발 사이클 7 (v5.7): 구조 에코 루프 차단
- 재판정 (목적 = "iteration 반복할수록 상향"): v5.4는 우상향 마감 (co@20 70→78.9, co@10 →56.7 회복),
  v5.6은 높게 시작해 하락 마감 (co@10 57.8→52.2, co@20 74.4→73.3). **목적 기준으론 v5.4 우위.**
- v5.6 하락 기전 (L2 성장량은 두 런 동등 +649/+674 → 양이 아니라 구성 문제):
  ① **에코 학습**: L2 강화 입력 retrieved_docs[:10]에 L1 예약석이 넣은 확장 문서가 상시 포함
     → 쿼리의 실제 공동검색이 아닌 "확장이 붙여놓은 쌍"을 학습. 구 wikigraph DRG의
     "BASE retrieval only" 원칙이 재작성에서 누락된 회귀.
  ② learned 잔여석 개방이 에코로 강해진 L2 후보에게 top-10 좌석 제공 (가속).
  ③ 스코프 1-hop에 learned 엣지(유효 0.3+) 유입 → SCOPE_BOOST가 base 랭킹 왜곡.
- v5.7 수정: I1 L2 강화는 base 검색 문서만 (확장 제외) / I2 스코프 learned 자격 0.45+ /
  I3 L1 잔여석 learned 미개방 (빈 좌석은 base로 반환).
- 기대: v5.6의 높은 시작점 유지 + v5.4의 우상향 회복.
- 유닛 전체 + 스모크 10/10. **v5.7 동결 — --fresh 무개입 5회 검증.**

### 2026-07-13 08:39:17 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 71.1/56.7/46.7%
- latency p50/p95: all=38.5/44.7ms · tiers(valB)={'cache': 0, 'llm': 83, 'rules': 7}
- SG: {'docs': 572, 'edges': 2771, 'l1_explicit': 1719, 'l2_co_retrieval': 1761, 'l3_llm_curated': 4, 'profiles': 3} · plan_cache: {'entries': 329, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2357}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 156, 'attention': 47, 'elapsed_s': 153.3}

### 2026-07-13 08:43:09 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 75.6/57.8/41.1%
- latency p50/p95: all=39.8/52.3ms · tiers(valB)={'cache': 79, 'llm': 4, 'rules': 7}
- SG: {'docs': 572, 'edges': 2924, 'l1_explicit': 1719, 'l2_co_retrieval': 1927, 'l3_llm_curated': 7, 'profiles': 6} · plan_cache: {'entries': 332, 'hits': 170, 'hit_rate': 0.452}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2278}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 155, 'attention': 45, 'elapsed_s': 158.0}

### 2026-07-13 08:48:20 — ver5 iteration 3 (LLM on)
- All@20 99.0% · ValA@20 98.9% · ValB co@20/10/5 78.9/55.6/31.1%
- latency p50/p95: all=39.8/9879.2ms · tiers(valB)={'cache': 38, 'llm': 0, 'rules': 52}
- SG: {'docs': 572, 'edges': 2977, 'l1_explicit': 1719, 'l2_co_retrieval': 2003, 'l3_llm_curated': 7, 'profiles': 6} · plan_cache: {'entries': 345, 'hits': 126, 'hit_rate': 0.335}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2125}, 's_llm': {'typed_edges': 0, 'profiles': 0}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 163, 'attention': 42, 'elapsed_s': 0.2}
- **REGRESSION GUARD fired → KG rolled back** (사유는 evolution_log 참조)

### 2026-07-13 08:49:06 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 75.6/50.0/32.2%
- latency p50/p95: all=42.0/67.9ms · tiers(valB)={'cache': 37, 'llm': 0, 'rules': 53}
- SG: {'docs': 572, 'edges': 3019, 'l1_explicit': 1719, 'l2_co_retrieval': 2050, 'l3_llm_curated': 7, 'profiles': 6} · plan_cache: {'entries': 345, 'hits': 139, 'hit_rate': 0.37}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2087}, 's_llm': {'typed_edges': 0, 'profiles': 0}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 164, 'attention': 42, 'elapsed_s': 0.2}

### 2026-07-13 08:49:53 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 76.7/53.3/33.3%
- latency p50/p95: all=40.8/68.4ms · tiers(valB)={'cache': 38, 'llm': 0, 'rules': 52}
- SG: {'docs': 572, 'edges': 3031, 'l1_explicit': 1719, 'l2_co_retrieval': 2062, 'l3_llm_curated': 7, 'profiles': 6} · plan_cache: {'entries': 345, 'hits': 130, 'hit_rate': 0.346}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2058}, 's_llm': {'typed_edges': 0, 'profiles': 0}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 163, 'attention': 48, 'elapsed_s': 0.1}

### 2026-07-10 — v5.7 검증 결과: 로직 유망, 검증은 LLM 장애로 오염 (판정 보류)
- LLM 정상 구간(iter1~2): co@20 71.1→75.6, co@10 56.7→57.8 — **동반 상승은 전 버전 최초** (에코 차단 효과 신호).
  co@5 iter1 46.7 역대 최고 시작점. 평균 co@20 75.6 전 버전 최고.
- LLM 사망 구간(iter3~5, CLI 한도, 실패 24 누적): co@5 41→31 붕괴, co@10 50까지 하락, iter3 가드 롤백.
  하락 시점 = 장애 시점 일치 → 로직 판정 불가.
- **CLI 한도로 인한 검증 오염 3회째 재발 → LLM 인프라가 검증 블로커.**
- 다음: 인프라 하드닝 (검색 로직 무변경) — analyzer/evolver LLM에 프롬프트 해시 키 디스크 영속 캐시.
  같은 쿼리 → 같은 플랜 재사용 (그라운드룰 합치: 정답 미참조, LLM 산출물 캐싱일 뿐).
  반복 런의 CLI 콜 ~90% 절감 + 런 간 비교가능성 향상. 적용 후 v5.7 재검증.

### 2026-07-13 13:31:52 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 98.3% · ValB co@20/10/5 66.7/52.2/40.0%
- latency p50/p95: all=36.6/48.1ms · tiers(valB)={'cache': 0, 'llm': 83, 'rules': 7}
- SG: {'docs': 572, 'edges': 2780, 'l1_explicit': 1719, 'l2_co_retrieval': 1766, 'l3_llm_curated': 4, 'profiles': 3} · plan_cache: {'entries': 331, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2347}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 150, 'attention': 45, 'elapsed_s': 147.1}

### 2026-07-13 13:34:36 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 71.1/54.4/40.0%
- latency p50/p95: all=40.0/48.4ms · tiers(valB)={'cache': 80, 'llm': 3, 'rules': 7}
- SG: {'docs': 572, 'edges': 2899, 'l1_explicit': 1719, 'l2_co_retrieval': 1921, 'l3_llm_curated': 8, 'profiles': 6} · plan_cache: {'entries': 332, 'hits': 170, 'hit_rate': 0.452}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2379}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 150, 'attention': 45, 'elapsed_s': 135.1}

### 2026-07-13 13:45:45 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 68.9/52.2/37.8%
- latency p50/p95: all=40.8/9144.6ms · tiers(valB)={'cache': 26, 'llm': 61, 'rules': 3}
- SG: {'docs': 572, 'edges': 3082, 'l1_explicit': 1719, 'l2_co_retrieval': 2147, 'l3_llm_curated': 12, 'profiles': 9} · plan_cache: {'entries': 365, 'hits': 106, 'hit_rate': 0.282}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2199}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 157, 'attention': 11, 'elapsed_s': 145.8}

### 2026-07-13 13:49:11 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 68.9/52.2/36.7%
- latency p50/p95: all=41.4/65.4ms · tiers(valB)={'cache': 79, 'llm': 8, 'rules': 3}
- SG: {'docs': 572, 'edges': 3092, 'l1_explicit': 1719, 'l2_co_retrieval': 2156, 'l3_llm_curated': 16, 'profiles': 12} · plan_cache: {'entries': 367, 'hits': 189, 'hit_rate': 0.503}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2093}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 155, 'attention': 9, 'elapsed_s': 138.8}

### 2026-07-13 13:51:53 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 70.0/53.3/37.8%
- latency p50/p95: all=40.7/66.4ms · tiers(valB)={'cache': 35, 'llm': 49, 'rules': 6}
- SG: {'docs': 572, 'edges': 3102, 'l1_explicit': 1719, 'l2_co_retrieval': 2163, 'l3_llm_curated': 20, 'profiles': 15} · plan_cache: {'entries': 367, 'hits': 134, 'hit_rate': 0.356}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2049}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 158, 'attention': 26, 'elapsed_s': 132.3}

### 2026-07-10 — v5.7 재검증 (최초 완전 클린 런) 및 판정
- LLM 캐시 도입 후 5회 무장애 완주 (fail 0, 캐시히트 iter5 133, 롤백 0, All 100 전 구간).
- 결과: co@20 69.1±1.63 (66.7→70.0) / co@10 52.9±0.98 / co@5 38.5±1.48 — **역대 최고 안정성, 그러나 성장 정체.**
- **판정: v5.4 챔피언 유지** (74.9, 70→78.9 상승 마감).
- 통찰: v5.4의 우상향은 에코 루프가 **진짜 링크(L1 근거 쌍)를 강화한 효과**가 상당 부분.
  확장이 L1 주도이므로 에코 쌍의 다수가 실제 card↔linked. I1의 전면 차단이 좋은 신호까지 제거 → 정체.
  v5.6의 실패는 에코가 learned 좌석 경유로 무근거 쌍까지 증폭한 부분.
- 부차 발견: haiku 샘플링 비결정성으로 iter1 레벨이 런마다 ±5pt — 판정은 궤적 중심이 옳음 (기존 기준 재확인).
  LLM 캐시 적재 완료로 이후 런은 LLM 축 결정적.
- v5.8: **선택적 에코** — 확장 문서 포함 쌍은 explicit_ref(L1) 레이어가 있는 경우에만 L2 강화 허용.
  L1 근거 쌍의 사용 증거 강화는 정당(문서 선언 링크의 활용 확인), 무근거 쌍은 base 전용 유지.

### 2026-07-10 — 개발 사이클 8 (v5.8): 선택적 에코
- v5.7 통찰 반영: 확장 문서 포함 쌍의 L2 강화를 전면 금지가 아니라 **explicit_ref(L1) 보유 쌍에만 허용**.
  문서가 선언한 링크의 사용 증거 강화는 정당(성장 엔진 복원), 무근거 쌍 생성은 계속 차단(에코 챔버 방지).
  I2(스코프 learned 0.45+)·I3(잔여석 미개방)는 유지.
- 유닛(선택적 에코 케이스 포함) + 스모크 10/10. **v5.8 동결 — --fresh 무개입 5회 검증.**
- 기대: v5.7의 안정성(stdev ~1.5) + v5.4의 우상향 (L1 쌍만 강화되므로 상승분이 전부 정당 신호).

### 2026-07-13 13:56:01 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 72.2/57.8/43.3%
- latency p50/p95: all=37.8/45.7ms · tiers(valB)={'cache': 0, 'llm': 83, 'rules': 7}
- SG: {'docs': 572, 'edges': 2179, 'l1_explicit': 1719, 'l2_co_retrieval': 1408, 'l3_llm_curated': 3, 'profiles': 3} · plan_cache: {'entries': 331, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1182}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 151, 'attention': 45, 'elapsed_s': 18.0}

### 2026-07-13 13:56:42 — ver5 iteration 2 (LLM on)
- All@20 99.0% · ValA@20 98.3% · ValB co@20/10/5 76.7/58.9/44.4%
- latency p50/p95: all=37.9/42.6ms · tiers(valB)={'cache': 80, 'llm': 3, 'rules': 7}
- SG: {'docs': 572, 'edges': 2240, 'l1_explicit': 1719, 'l2_co_retrieval': 1527, 'l3_llm_curated': 6, 'profiles': 6} · plan_cache: {'entries': 331, 'hits': 172, 'hit_rate': 0.457}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1199}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 146, 'attention': 45, 'elapsed_s': 14.2}
- **REGRESSION GUARD fired → KG rolled back** (사유는 evolution_log 참조)

### 2026-07-13 13:57:33 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 73.3/54.4/41.1%
- latency p50/p95: all=39.0/63.4ms · tiers(valB)={'cache': 23, 'llm': 64, 'rules': 3}
- SG: {'docs': 572, 'edges': 2308, 'l1_explicit': 1719, 'l2_co_retrieval': 1671, 'l3_llm_curated': 10, 'profiles': 9} · plan_cache: {'entries': 362, 'hits': 100, 'hit_rate': 0.266}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1118}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 158, 'attention': 14, 'elapsed_s': 21.6}

### 2026-07-13 13:58:20 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 75.6/55.6/42.2%
- latency p50/p95: all=38.2/59.3ms · tiers(valB)={'cache': 80, 'llm': 7, 'rules': 3}
- SG: {'docs': 572, 'edges': 2317, 'l1_explicit': 1719, 'l2_co_retrieval': 1681, 'l3_llm_curated': 13, 'profiles': 12} · plan_cache: {'entries': 366, 'hits': 189, 'hit_rate': 0.503}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1099}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 158, 'attention': 11, 'elapsed_s': 8.6}

### 2026-07-13 14:00:17 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 98.3% · ValB co@20/10/5 75.6/55.6/42.2%
- latency p50/p95: all=38.0/59.5ms · tiers(valB)={'cache': 27, 'llm': 57, 'rules': 6}
- SG: {'docs': 572, 'edges': 2319, 'l1_explicit': 1719, 'l2_co_retrieval': 1683, 'l3_llm_curated': 15, 'profiles': 15} · plan_cache: {'entries': 366, 'hits': 129, 'hit_rate': 0.343}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1046}, 's_llm': {'typed_edges': 2, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 157, 'attention': 26, 'elapsed_s': 87.3}
- **REGRESSION GUARD fired → KG rolled back** (사유는 evolution_log 참조)

### 2026-07-10 — v5.8 검증 완료: 신규 챔피언 승격
- 클린 런 (LLM 캐시 결정적, fail 0): co@20 74.7±1.86 (72.2→75.6, 피크 76.7) /
  co@10 **56.5±1.84 역대 최고** / co@5 **42.6±1.25 역대 최고**. All 100 (가드 2회 발동 제외), ValA ~99.
- vs v5.4: co@10 +4.1, co@5 +2.8, co@20 동급(−0.2)에 stdev 절반. vs v5.7: 안정성 유지하며 성장 복원.
- **판정: v5.8 승격** — "높은 시작 + 우상향 + 안정" 3조건 최초 동시 충족. 선택적 에코 설계 확증.
- 잔여 저해 요인: Track K 가드 롤백 2회 (iter2·iter5, ValA 1~2문항 흔들림) — 롤백마다 co@10 3~4pt 손실 후 회복.
  다음 사이클 1순위: Track K 회귀 원인 특정 (위키화 vs entity VDB 갱신 vs stale-edge 제거 중 범인 분리).
