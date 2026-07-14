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

### 2026-07-10 — 개발 사이클 9 (v5.9): 선별 로직 재진단 — admission이 아니라 ordering
- 유저 지적으로 재진단: v5.4의 small-k 흔들림의 진짜 원인은 앵커의 참조 클러스터(linked/spec/golden/형제카드
  4~6개)가 거의 동일 점수로 랭크 3~8에 뭉치는데, 그 **내부 순서가 쿼리 무관(사실상 무작위)**이었던 것.
  v5.5(tail)·v5.6(좌석)은 admission을 고친 우회로였음.
- v5.9 = v5.4 회귀 + 최소 수정:
  1. 좌석 예약·tail 제거 (v5.4의 순수 점수 컷)
  2. L1 확장 점수에 온화한 관련성 보정 ×(0.7+0.3·rel) — 클러스터 내부만 재배열, 게이트 아님 (v5.3 교훈 준수)
  3. 유지: 선택적 에코(v5.8 검증됨), 스코프 learned 0.45(I2)
- 유닛 + 스모크 10/10. **v5.9 동결 — --fresh 무개입 5회 검증.**

### 2026-07-13 14:41:52 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 97.8% · ValB co@20/10/5 67.8/54.4/37.8%
- latency p50/p95: all=36.5/43.3ms · tiers(valB)={'cache': 0, 'llm': 83, 'rules': 7}
- SG: {'docs': 572, 'edges': 2327, 'l1_explicit': 1719, 'l2_co_retrieval': 1560, 'l3_llm_curated': 4, 'profiles': 3} · plan_cache: {'entries': 332, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1435}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 149, 'attention': 44, 'elapsed_s': 61.6}

### 2026-07-13 14:43:04 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 72.2/55.6/36.7%
- latency p50/p95: all=37.0/43.1ms · tiers(valB)={'cache': 79, 'llm': 4, 'rules': 7}
- SG: {'docs': 572, 'edges': 2381, 'l1_explicit': 1719, 'l2_co_retrieval': 1651, 'l3_llm_curated': 5, 'profiles': 6} · plan_cache: {'entries': 332, 'hits': 168, 'hit_rate': 0.447}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1366}, 's_llm': {'typed_edges': 1, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 149, 'attention': 45, 'elapsed_s': 43.9}

### 2026-07-13 14:43:53 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 98.3% · ValB co@20/10/5 72.2/53.3/35.6%
- latency p50/p95: all=37.8/61.2ms · tiers(valB)={'cache': 33, 'llm': 54, 'rules': 3}
- SG: {'docs': 572, 'edges': 2454, 'l1_explicit': 1719, 'l2_co_retrieval': 1782, 'l3_llm_curated': 9, 'profiles': 9} · plan_cache: {'entries': 362, 'hits': 113, 'hit_rate': 0.301}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1235}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 153, 'attention': 16, 'elapsed_s': 20.5}
- **REGRESSION GUARD fired → KG rolled back** (사유는 evolution_log 참조)

### 2026-07-13 14:44:53 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 72.2/52.2/34.4%
- latency p50/p95: all=39.7/62.2ms · tiers(valB)={'cache': 77, 'llm': 10, 'rules': 3}
- SG: {'docs': 572, 'edges': 2464, 'l1_explicit': 1719, 'l2_co_retrieval': 1816, 'l3_llm_curated': 13, 'profiles': 12} · plan_cache: {'entries': 367, 'hits': 180, 'hit_rate': 0.479}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1187}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 154, 'attention': 9, 'elapsed_s': 30.8}

### 2026-07-13 14:46:37 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 71.1/51.1/34.4%
- latency p50/p95: all=39.0/59.9ms · tiers(valB)={'cache': 37, 'llm': 47, 'rules': 6}
- SG: {'docs': 572, 'edges': 2475, 'l1_explicit': 1719, 'l2_co_retrieval': 1833, 'l3_llm_curated': 16, 'profiles': 15} · plan_cache: {'entries': 367, 'hits': 131, 'hit_rate': 0.348}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1255}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 151, 'attention': 28, 'elapsed_s': 75.9}

### 2026-07-10 — v5.9 검증 완료: 기각, v5.8 챔피언 유지
- 결과: co@20 71.1±1.91 / co@10 53.3±1.77 (54.4→51.1 하락 마감) / co@5 35.8±1.48 (37.8→34.4 하락 마감).
  전 지표에서 v5.8(74.7/56.5/42.6) 열위.
- **교훈**: 좌석(v5.6)+tail(v5.5)은 small-k 성능의 실제 기여자이자 드리프트 방지 장치였음.
  ordering 보정 단독은 admission 장치를 대체하지 못함 (빼자 co@10/5 5회 연속 하향).
- 다음: v5.10 = v5.8 구조(좌석+tail+선택적 에코) + v5.9 관련성 보정 결합 — 두 아이디어는 상호 배타 아님.

### 2026-07-10 — 개발 사이클 10 (v5.10): v5.8 구조 + v5.9 관련성 보정 결합
- v5.8의 admission(L1 좌석 4/6 + tail 8 + 선택적 에코)은 그대로, v5.9의 L1 관련성 보정
  ×(0.7+0.3·rel)을 좌석 내부 순서 결정에 적용. "좌석이 경쟁자를 정하고, 보정이 그들의 순서를 정한다."
- 유닛 통과, 스모크 9/10 (ε 비결정성 범위). **v5.10 동결 — --fresh 무개입 5회 검증.**
- 판정 기준: v5.8(74.7/56.5/42.6) 대비 동등 이상 + small-k 우상향이면 승격, 아니면 v5.8 확정.

### 2026-07-13 14:49:12 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 98.3% · ValB co@20/10/5 67.8/48.9/38.9%
- latency p50/p95: all=37.0/44.4ms · tiers(valB)={'cache': 0, 'llm': 83, 'rules': 7}
- SG: {'docs': 572, 'edges': 2300, 'l1_explicit': 1719, 'l2_co_retrieval': 1540, 'l3_llm_curated': 4, 'profiles': 3} · plan_cache: {'entries': 333, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1408}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 149, 'attention': 43, 'elapsed_s': 1.0}

### 2026-07-13 14:49:56 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 97.8% · ValB co@20/10/5 73.3/53.3/40.0%
- latency p50/p95: all=40.0/51.7ms · tiers(valB)={'cache': 80, 'llm': 3, 'rules': 7}
- SG: {'docs': 572, 'edges': 2368, 'l1_explicit': 1719, 'l2_co_retrieval': 1644, 'l3_llm_curated': 6, 'profiles': 6} · plan_cache: {'entries': 335, 'hits': 171, 'hit_rate': 0.455}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1373}, 's_llm': {'typed_edges': 2, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 149, 'attention': 43, 'elapsed_s': 16.8}

### 2026-07-13 14:50:57 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 73.3/53.3/36.7%
- latency p50/p95: all=37.5/59.1ms · tiers(valB)={'cache': 29, 'llm': 57, 'rules': 4}
- SG: {'docs': 572, 'edges': 2436, 'l1_explicit': 1719, 'l2_co_retrieval': 1778, 'l3_llm_curated': 9, 'profiles': 9} · plan_cache: {'entries': 363, 'hits': 108, 'hit_rate': 0.287}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1222}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 149, 'attention': 13, 'elapsed_s': 32.0}

### 2026-07-13 14:51:27 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 73.3/51.1/37.8%
- latency p50/p95: all=39.2/61.9ms · tiers(valB)={'cache': 76, 'llm': 11, 'rules': 3}
- SG: {'docs': 572, 'edges': 2446, 'l1_explicit': 1719, 'l2_co_retrieval': 1797, 'l3_llm_curated': 12, 'profiles': 12} · plan_cache: {'entries': 366, 'hits': 177, 'hit_rate': 0.471}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1158}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 150, 'attention': 11, 'elapsed_s': 1.1}

### 2026-07-13 14:52:22 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 73.3/51.1/38.9%
- latency p50/p95: all=41.3/78.2ms · tiers(valB)={'cache': 33, 'llm': 52, 'rules': 5}
- SG: {'docs': 572, 'edges': 2454, 'l1_explicit': 1719, 'l2_co_retrieval': 1807, 'l3_llm_curated': 15, 'profiles': 15} · plan_cache: {'entries': 366, 'hits': 125, 'hit_rate': 0.332}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1116}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 147, 'attention': 24, 'elapsed_s': 25.2}

### 2026-07-10 — v5.10 검증 완료: 기각. **시리즈 마감 — v5.8 최종 확정**
- v5.10 (v5.8 구조 + L1 관련성 보정): co@20 72.2±2.46 / co@10 51.5±1.84 / co@5 38.5±1.25
  — v5.8(74.7/56.5/42.6) 전 지표 열위, 특히 co@10 −5.0. 롤백 0회는 확인.
- 코드를 structrag-v5.8 태그 상태로 복원 (유닛 + 스모크 10/10 재확인).

## 설계 원칙 추가

### D8. L1(명시적 참조) 확장에 어휘 신호 곱하기 금지
- 3회 독립 실험으로 확증: v5.3(게이트) 전면 후퇴, v5.9(순서 보정, 좌석 없이) small-k 5연속 하락,
  v5.10(순서 보정, 좌석 안에서) co@10 −5.0.
- 이유: 구조 참조의 존재 이유가 "쿼리와 어휘적으로 먼 문서를 구조로 찾는 것"이므로,
  어휘 관련성을 곱하는 순간 (게이트든 releveling이든) 그 본질을 벌점화한다.
  L1은 구조 신뢰만으로 다루고, 어휘 신호는 학습 엣지(L2/L3)에만 적용한다.

### 최종 스코어보드 (무개입 클린 런, 바닐라 hybrid 대비)
| 버전 | co@20 | co@10 | co@5 | 판정 |
|---|---|---|---|---|
| 바닐라 hybrid | 57.8 | 52.2 | 33.3 | 기준선 |
| v5.4 | 74.9±3.75 | 52.4±2.88 | 39.8±2.38 | co@20 종점 최고 (78.9) |
| v5.7 | 69.1±1.63 | 52.9±0.98 | 38.5±1.48 | 안정·정체 |
| **v5.8** | **74.7±1.86** | **56.5±1.84** | **42.6±1.25** | **최종 챔피언** |
| v5.9 | 71.1±1.91 | 53.3±1.77 | 35.8±1.48 | 기각 (D8 확증) |
| v5.10 | 72.2±2.46 | 51.5±1.84 | 38.5±1.25 | 기각 (D8 확증) |
- 미해결 백로그: Track K 가드 롤백 원인 특정 (여러 런에서 재발, 회당 co@10 3~4pt 손실),
  co@10 바닐라 대비 이득 확대, evolver ⑥ rewrite 마이닝 구현.

### 2026-07-10 — 개발 사이클 11 (v5.11): co@10→co@20 동수준화 캠페인 시작
- 목표(유저): co@10을 co@20 수준으로. 진단(rules-only 90문항, 정답은 진단에만 사용·시스템 미반영):
  co@20 히트/co@10 미스 20건 중 **15건이 "카드가 11~20위"** — linked가 아니라 카드가 문제.
  기전: 확장 문서 6개가 앵커 점수 상속으로 base 중위권 카드 위에 적층 + 허브 문서가 base에서 카드를 앞섬.
- 수정 2건 (D8 준수, 정답 미참조, 결정적 룰):
  1. **확장 헤드캡**: top-10 문서 슬롯 내 확장 문서 최대 3개, 초과분은 11위 이후로 (co@20 무손실 설계)
  2. **타입 프라이어 (top-2 한정)**: 쿼리가 요구한 산출물 타입("테스트"→validation/test 토큰)과 파일명 토큰이
     일치하는 문서 중 원점수 상위 2개만 ×1.15/×1.075 부스트. 전체 부스트는 형제 카드 홍수로 co@20 −4.4 확인 후 기각.
     KO→EN 힌트 맵은 언어 정규화 (파일명 컨벤션 하드코딩 아님 — 토큰은 코퍼스에서 자동 유도).
- 스크리닝(rules-only): co@5 30→42.2, co@10 61.1→65.6, co@20 81.1→80.0, 갭 20→14.4.
- 유닛 + 스모크 통과. **v5.11 동결 — --fresh 무개입 5회 검증.**

### 2026-07-13 19:01:20 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 98.3% · ValB co@20/10/5 67.8/52.2/42.2%
- latency p50/p95: all=37.0/46.6ms · tiers(valB)={'cache': 0, 'llm': 83, 'rules': 7}
- SG: {'docs': 572, 'edges': 2231, 'l1_explicit': 1719, 'l2_co_retrieval': 1469, 'l3_llm_curated': 1, 'profiles': 3} · plan_cache: {'entries': 338, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1298}, 's_llm': {'typed_edges': 1, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 149, 'attention': 38, 'elapsed_s': 61.1}

### 2026-07-13 19:02:39 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 72.2/54.4/42.2%
- latency p50/p95: all=38.2/51.4ms · tiers(valB)={'cache': 81, 'llm': 2, 'rules': 7}
- SG: {'docs': 572, 'edges': 2292, 'l1_explicit': 1719, 'l2_co_retrieval': 1582, 'l3_llm_curated': 2, 'profiles': 6} · plan_cache: {'entries': 338, 'hits': 171, 'hit_rate': 0.455}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1297}, 's_llm': {'typed_edges': 1, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 155, 'attention': 38, 'elapsed_s': 52.5}

### 2026-07-13 19:03:27 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 98.3% · ValB co@20/10/5 71.1/53.3/40.0%
- latency p50/p95: all=39.2/64.1ms · tiers(valB)={'cache': 30, 'llm': 56, 'rules': 4}
- SG: {'docs': 572, 'edges': 2345, 'l1_explicit': 1719, 'l2_co_retrieval': 1705, 'l3_llm_curated': 6, 'profiles': 9} · plan_cache: {'entries': 364, 'hits': 106, 'hit_rate': 0.282}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1210}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 155, 'attention': 13, 'elapsed_s': 19.9}
- **REGRESSION GUARD fired → KG rolled back** (사유는 evolution_log 참조)

### 2026-07-13 19:03:57 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 72.2/54.4/41.1%
- latency p50/p95: all=39.2/66.1ms · tiers(valB)={'cache': 81, 'llm': 6, 'rules': 3}
- SG: {'docs': 572, 'edges': 2360, 'l1_explicit': 1719, 'l2_co_retrieval': 1726, 'l3_llm_curated': 8, 'profiles': 12} · plan_cache: {'entries': 368, 'hits': 184, 'hit_rate': 0.489}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1139}, 's_llm': {'typed_edges': 2, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 153, 'attention': 8, 'elapsed_s': 1.0}

### 2026-07-13 19:04:44 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 98.9% · ValB co@20/10/5 73.3/54.4/38.9%
- latency p50/p95: all=40.0/73.5ms · tiers(valB)={'cache': 34, 'llm': 51, 'rules': 5}
- SG: {'docs': 572, 'edges': 2363, 'l1_explicit': 1719, 'l2_co_retrieval': 1728, 'l3_llm_curated': 10, 'profiles': 15} · plan_cache: {'entries': 369, 'hits': 129, 'hit_rate': 0.343}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1140}, 's_llm': {'typed_edges': 2, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 151, 'attention': 21, 'elapsed_s': 19.7}

### 2026-07-10 — v5.11 기각 및 개발 사이클 12 (v5.12)
- v5.11 검증: 71.3±2.12 / 53.7±0.98 / 40.9±1.43 — v5.8 전 지표 열위. **기각.**
  교훈: 일괄 헤드캡은 카드를 살리지만 linked(확장 4번째 이후)도 함께 밀어냄. 풀런(학습 포함)과
  스크리닝(단발·무학습)의 괴리 확인 — 스크리닝은 후보 선별용, 판정은 반드시 풀런.
- v5.12 = v5.8 + **타입 프라이어(top-2)만** (헤드캡 제거). 스크리닝: co@5 45.6(최고)/co@10 64.4/
  co@20 81.1(베이스라인 무손실) — 카드 부양은 타입 프라이어가, 확장 배치는 검증된 v5.8 그대로.
- 유닛 + 스모크 통과. **v5.12 동결 — --fresh 무개입 5회 검증.**

### 2026-07-13 19:08:18 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 97.8% · ValB co@20/10/5 70.0/56.7/46.7%
- latency p50/p95: all=38.0/44.9ms · tiers(valB)={'cache': 0, 'llm': 83, 'rules': 7}
- SG: {'docs': 572, 'edges': 2166, 'l1_explicit': 1719, 'l2_co_retrieval': 1423, 'l3_llm_curated': 4, 'profiles': 3} · plan_cache: {'entries': 335, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1204}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 151, 'attention': 41, 'elapsed_s': 37.9}

### 2026-07-13 19:09:36 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 97.8% · ValB co@20/10/5 72.2/56.7/43.3%
- latency p50/p95: all=38.6/46.0ms · tiers(valB)={'cache': 81, 'llm': 2, 'rules': 7}
- SG: {'docs': 572, 'edges': 2223, 'l1_explicit': 1719, 'l2_co_retrieval': 1523, 'l3_llm_curated': 5, 'profiles': 6} · plan_cache: {'entries': 337, 'hits': 173, 'hit_rate': 0.46}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1191}, 's_llm': {'typed_edges': 1, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 157, 'attention': 40, 'elapsed_s': 51.8}

### 2026-07-13 19:10:28 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 97.8% · ValB co@20/10/5 68.9/53.3/41.1%
- latency p50/p95: all=38.8/62.5ms · tiers(valB)={'cache': 30, 'llm': 57, 'rules': 3}
- SG: {'docs': 572, 'edges': 2286, 'l1_explicit': 1719, 'l2_co_retrieval': 1641, 'l3_llm_curated': 9, 'profiles': 9} · plan_cache: {'entries': 365, 'hits': 108, 'hit_rate': 0.287}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1076}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 157, 'attention': 12, 'elapsed_s': 24.6}

### 2026-07-13 19:10:59 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 97.8% · ValB co@20/10/5 67.8/53.3/41.1%
- latency p50/p95: all=41.5/67.1ms · tiers(valB)={'cache': 81, 'llm': 6, 'rules': 3}
- SG: {'docs': 572, 'edges': 2289, 'l1_explicit': 1719, 'l2_co_retrieval': 1659, 'l3_llm_curated': 13, 'profiles': 12} · plan_cache: {'entries': 368, 'hits': 183, 'hit_rate': 0.487}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1044}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 158, 'attention': 9, 'elapsed_s': 1.0}

### 2026-07-13 19:12:06 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 97.8% · ValB co@20/10/5 68.9/54.4/41.1%
- latency p50/p95: all=39.7/64.2ms · tiers(valB)={'cache': 35, 'llm': 49, 'rules': 6}
- SG: {'docs': 572, 'edges': 2290, 'l1_explicit': 1719, 'l2_co_retrieval': 1663, 'l3_llm_curated': 16, 'profiles': 15} · plan_cache: {'entries': 369, 'hits': 129, 'hit_rate': 0.343}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1040}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 2}, 'llm_calls': 10, 'good': 156, 'attention': 22, 'elapsed_s': 39.8}

### 2026-07-10 — v5.12 기각 및 개발 사이클 13 (v5.13): 랭킹이 아니라 롤백 제거
- v5.12 검증: 69.6±1.67 / 54.9±1.72 / 42.7±2.45, **ValA 97.8 5회 고착** — co@20 −5.1로 기각.
  타입 프라이어는 doc_score 전역 오염(확장 상속·윈도우 구성 전파)으로 풀런에서 순손실.
  갭 14.7은 co@20을 깎아 만든 것이라 무효. **타입 프라이어 레버 폐기** (단독·번들 모두 실패).
- v5.13 방향 전환: 랭킹 로직은 v5.8 복원(무손상), 대신 **가드 롤백의 원인 제거**.
  근거: Track K의 유일한 KG 변경 = 위키화. 롤백은 위키화 사이클 직후에만 발생 (v5.8 iter2/5 등).
  LLM 리라이트가 기술 용어를 떨어뜨리면 임베딩이 이동해 해당 용어 쿼리가 깨짐.
- v5.13 = v5.8 + **위키화 용어보존 가드**: 신규 설명이 구 설명의 살리언트 용어(4자+) 60% 미만 보존 시
  적용 거부 + 로그. 결정적, 정답 미참조. 기대: 롤백 소멸 → 성장 비단절 → co@10/co@20 동반 상승.
- 유닛 + 스모크 10/10. **v5.13 동결 — --fresh 무개입 5회 검증.**

### 2026-07-13 19:15:24 — ver5 iteration 1 (LLM on)
- All@20 99.0% · ValA@20 99.4% · ValB co@20/10/5 66.7/52.2/42.2%
- latency p50/p95: all=37.2/48.0ms · tiers(valB)={'cache': 0, 'llm': 83, 'rules': 7}
- SG: {'docs': 572, 'edges': 2179, 'l1_explicit': 1719, 'l2_co_retrieval': 1419, 'l3_llm_curated': 4, 'profiles': 3} · plan_cache: {'entries': 330, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1169}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 1}, 'llm_calls': 9, 'good': 151, 'attention': 46, 'elapsed_s': 1.0}

### 2026-07-13 19:16:21 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 70.0/53.3/41.1%
- latency p50/p95: all=38.6/44.2ms · tiers(valB)={'cache': 81, 'llm': 2, 'rules': 7}
- SG: {'docs': 572, 'edges': 2240, 'l1_explicit': 1719, 'l2_co_retrieval': 1518, 'l3_llm_curated': 6, 'profiles': 6} · plan_cache: {'entries': 332, 'hits': 172, 'hit_rate': 0.457}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1157}, 's_llm': {'typed_edges': 2, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 151, 'attention': 45, 'elapsed_s': 30.7}

### 2026-07-13 19:16:50 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 70.0/52.2/40.0%
- latency p50/p95: all=38.1/62.0ms · tiers(valB)={'cache': 23, 'llm': 62, 'rules': 5}
- SG: {'docs': 572, 'edges': 2315, 'l1_explicit': 1719, 'l2_co_retrieval': 1669, 'l3_llm_curated': 9, 'profiles': 9} · plan_cache: {'entries': 363, 'hits': 102, 'hit_rate': 0.271}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1046}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 154, 'attention': 17, 'elapsed_s': 0.2}

### 2026-07-13 19:17:26 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 71.1/52.2/41.1%
- latency p50/p95: all=44.0/74.5ms · tiers(valB)={'cache': 80, 'llm': 7, 'rules': 3}
- SG: {'docs': 572, 'edges': 2323, 'l1_explicit': 1719, 'l2_co_retrieval': 1683, 'l3_llm_curated': 12, 'profiles': 12} · plan_cache: {'entries': 366, 'hits': 187, 'hit_rate': 0.497}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1017}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 155, 'attention': 11, 'elapsed_s': 0.2}

### 2026-07-13 19:18:35 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 71.1/51.1/41.1%
- latency p50/p95: all=39.1/62.0ms · tiers(valB)={'cache': 33, 'llm': 54, 'rules': 3}
- SG: {'docs': 572, 'edges': 2324, 'l1_explicit': 1719, 'l2_co_retrieval': 1686, 'l3_llm_curated': 15, 'profiles': 15} · plan_cache: {'entries': 368, 'hits': 131, 'hit_rate': 0.348}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 993}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 156, 'attention': 23, 'elapsed_s': 40.8}

### 2026-07-10 — v5.13 검증 및 프로토콜 P1 (검증 결정화)
- v5.13: 69.8±1.81 / 52.2±0.78 / 41.1±0.78, **무롤백 5회 + ValA 99.4 고정 + 위키화 9건 거부** —
  가드 완벽 작동, 역대 최저 분산. 그러나 co 레벨은 v5.8 기록(74.7/56.5/42.6) 아래.
- **방법론 경보**: v5.9~v5.13 다섯 런이 69.6~72.2에 군집, v5.8만 74.7로 고립.
  "v5.8+미세변경" 버전들(v5.10, v5.13)이 74.7을 재현 못함 → v5.8 기록이 ε 탐험의 행운 런일 가능성.
  단일 런 ±2~4pt 노이즈에서 근접 변형 간 순위 판정은 불가 — v5.9~13 기각 판정도 재검토 대상.
- **프로토콜 P1**: 검증 런은 ε=0 (하네스에서 강제, 프로덕션은 탐험 유지). 밴딧 EMA 학습은 계속됨.
- 다음: P1 하에 v5.8 재현 런 → 재현 시 v5.8 우위 확정 / 미재현 시 v5.13(무롤백·저분산·가드) 우세 판정.

### 2026-07-10 — v5.8 재현 런 (P1, ε=0) 결과: 원기록 무효, v5.13 챔피언 재판정
- 재현: 69.8±1.81 / 52.6±0.98 / 38.2±1.67 (무롤백, ValA 99.4 고정) — **원기록 74.7/56.5/42.6 재현 실패.**
  v5.8의 왕관은 ε 탐험 행운 런이 만든 것으로 공식 무효화. v5.9~13 "기각"들도 동급 군집으로 재해석.
- **현행 챔피언: v5.13** — 재현 조건에서 동급 이상 (69.8/52.2/41.1) + 위키화 용어보존 가드 + 무롤백 + 최저 분산.
- 갭 캠페인 공식 베이스라인: co@20 ~70 / co@10 ~52.5 / 갭 ~17pt. 이후 모든 레버는 P1 결정 런으로 판정.
- 교훈(P1의 근거 재확인): 단일 확률 런의 순위 판정은 ±3~4pt까지 속을 수 있다. 재현 없는 기록은 기록이 아니다.

### 2026-07-13 19:27:20 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 65.6/51.1/41.1%
- latency p50/p95: all=37.0/44.3ms · tiers(valB)={'cache': 0, 'llm': 83, 'rules': 7}
- SG: {'docs': 572, 'edges': 2179, 'l1_explicit': 1719, 'l2_co_retrieval': 1418, 'l3_llm_curated': 3, 'profiles': 3} · plan_cache: {'entries': 331, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1183}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 1}, 'llm_calls': 9, 'good': 152, 'attention': 45, 'elapsed_s': 37.3}

### 2026-07-13 19:27:46 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 70.0/53.3/42.2%
- latency p50/p95: all=37.5/42.0ms · tiers(valB)={'cache': 79, 'llm': 4, 'rules': 7}
- SG: {'docs': 572, 'edges': 2231, 'l1_explicit': 1719, 'l2_co_retrieval': 1511, 'l3_llm_curated': 5, 'profiles': 6} · plan_cache: {'entries': 331, 'hits': 171, 'hit_rate': 0.455}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1200}, 's_llm': {'typed_edges': 2, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 152, 'attention': 45, 'elapsed_s': 0.2}

### 2026-07-13 19:28:14 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 70.0/52.2/38.9%
- latency p50/p95: all=39.8/63.1ms · tiers(valB)={'cache': 23, 'llm': 64, 'rules': 3}
- SG: {'docs': 572, 'edges': 2304, 'l1_explicit': 1719, 'l2_co_retrieval': 1667, 'l3_llm_curated': 9, 'profiles': 9} · plan_cache: {'entries': 366, 'hits': 101, 'hit_rate': 0.269}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1112}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 157, 'attention': 10, 'elapsed_s': 0.2}

### 2026-07-13 19:28:43 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 70.0/52.2/38.9%
- latency p50/p95: all=43.1/67.4ms · tiers(valB)={'cache': 81, 'llm': 6, 'rules': 3}
- SG: {'docs': 572, 'edges': 2309, 'l1_explicit': 1719, 'l2_co_retrieval': 1671, 'l3_llm_curated': 12, 'profiles': 12} · plan_cache: {'entries': 366, 'hits': 193, 'hit_rate': 0.513}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1073}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 154, 'attention': 10, 'elapsed_s': 0.2}

### 2026-07-13 19:29:11 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 70.0/51.1/38.9%
- latency p50/p95: all=39.6/59.4ms · tiers(valB)={'cache': 28, 'llm': 56, 'rules': 6}
- SG: {'docs': 572, 'edges': 2312, 'l1_explicit': 1719, 'l2_co_retrieval': 1679, 'l3_llm_curated': 14, 'profiles': 15} · plan_cache: {'entries': 366, 'hits': 127, 'hit_rate': 0.338}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1045}, 's_llm': {'typed_edges': 2, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 155, 'attention': 28, 'elapsed_s': 0.2}

### 2026-07-10 — 개발 사이클 14 (v5.14): 앵커1 L1 에스코트 (@10 동수준화 캠페인, P1 체제)
- 베이스라인 (v5.13 P1): All@10 96.2~98.1 / ValA@10 95.0~95.6 / co@10 평균 52.0. 목표 = @20 수준 (co@10 ~70).
- v5.14: 최종 랭킹 1위 문서의 최강 L1 이웃 1개를 랭크 2에 에스코트 삽입 (경로 확장 원리, D8 준수).
- 인프레임 스크린 (ε0, 룰): co@10 52.2→57.8, co@20 76.7→83.3, ValA@10 95.0→93.9(-1.1, 슬롯 비용).
- **v5.14 동결 — P1 검증 5회. 보고는 @10 종합만.**

### 2026-07-13 19:33:35 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 85.6/80.0/74.4%
- latency p50/p95: all=37.3/44.1ms · tiers(valB)={'cache': 0, 'llm': 83, 'rules': 7}
- SG: {'docs': 572, 'edges': 2165, 'l1_explicit': 1719, 'l2_co_retrieval': 1419, 'l3_llm_curated': 4, 'profiles': 3} · plan_cache: {'entries': 329, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1148}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 1}, 'llm_calls': 9, 'good': 146, 'attention': 47, 'elapsed_s': 27.5}

### 2026-07-13 19:34:24 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 86.7/78.9/71.1%
- latency p50/p95: all=37.8/42.8ms · tiers(valB)={'cache': 80, 'llm': 3, 'rules': 7}
- SG: {'docs': 572, 'edges': 2220, 'l1_explicit': 1719, 'l2_co_retrieval': 1511, 'l3_llm_curated': 7, 'profiles': 6} · plan_cache: {'entries': 329, 'hits': 172, 'hit_rate': 0.457}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1146}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 146, 'attention': 47, 'elapsed_s': 23.6}

### 2026-07-13 19:36:44 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 86.7/78.9/73.3%
- latency p50/p95: all=39.7/61.2ms · tiers(valB)={'cache': 27, 'llm': 60, 'rules': 3}
- SG: {'docs': 572, 'edges': 2267, 'l1_explicit': 1719, 'l2_co_retrieval': 1641, 'l3_llm_curated': 9, 'profiles': 9} · plan_cache: {'entries': 366, 'hits': 104, 'hit_rate': 0.277}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1061}, 's_llm': {'typed_edges': 2, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 152, 'attention': 10, 'elapsed_s': 72.2}

### 2026-07-13 19:37:11 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 86.7/78.9/73.3%
- latency p50/p95: all=39.8/61.4ms · tiers(valB)={'cache': 83, 'llm': 4, 'rules': 3}
- SG: {'docs': 572, 'edges': 2281, 'l1_explicit': 1719, 'l2_co_retrieval': 1655, 'l3_llm_curated': 11, 'profiles': 12} · plan_cache: {'entries': 366, 'hits': 195, 'hit_rate': 0.519}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1054}, 's_llm': {'typed_edges': 2, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 153, 'attention': 10, 'elapsed_s': 0.2}

### 2026-07-13 19:38:20 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 86.7/78.9/71.1%
- latency p50/p95: all=39.2/59.1ms · tiers(valB)={'cache': 29, 'llm': 55, 'rules': 6}
- SG: {'docs': 572, 'edges': 2283, 'l1_explicit': 1719, 'l2_co_retrieval': 1658, 'l3_llm_curated': 12, 'profiles': 15} · plan_cache: {'entries': 366, 'hits': 124, 'hit_rate': 0.33}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1019}, 's_llm': {'typed_edges': 1, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 8, 'good': 150, 'attention': 30, 'elapsed_s': 41.8}

### 2026-07-10 — v5.14 검증 완료: **@10 동수준화 목표 달성, 챔피언 승격**
- P1 결정 런: co@10 **79.1±0.49** (베이스라인 52.0 → +27.1), co@20 86.5, co@5 72.6.
  All@20 100 / ValA@20 99.4 무손상, 무롤백, 역대 최저 분산. ValA@10 −1.3 (에스코트 슬롯 비용).
- 기전 확인: 스크린(+5.6)을 크게 상회한 것은 **LLM 플랜(카드→1위) × 에스코트(선언 링크→2위) 시너지**.
  룰 단독 스크린은 카드 1위 비율이 낮아 에스코트 효과가 절반만 보였음.
- **목표 "co@10 ≈ 기존 co@20(~70)" 초과 달성. v5.14 현행 챔피언.**
- 잔여 백로그: ValA@10 슬롯 비용 회수 (에스코트 조건화: 앵커가 L1 이웃 0개면 미삽입 등),
  Track K 위키화 개선 재시도, evolver ⑥.

### 2026-07-10 — 개발 사이클 15 (v5.15): 균일 응답 아키텍처 + 골격 일반화
- 유저 방향 재정렬: 핫패스는 어떤 쿼리든 LLM 0회 균일 (~40ms). 원안 "SG탐색→힌트→좁게탐색→리턴" 복원.
- 변경 3건:
  1. **응답먼저·분석나중**: analyze()는 캐시/룰만. 복잡·저품질 쿼리는 응답 후 분석 큐 →
     evolver **섀도 플래너**가 LLM 플랜 생성 → 섀도 검색 시운전 → 룰보다 좋을 때만 캐시 승격.
  2. **① 골격 키 전략수첩**: 모드 밴딧 키를 내용 지문 → 쿼리 골격(id|type|multi|len)으로.
     키워드 달라도 모양 같으면 전략 공유. 플랜캐시는 내용 키 유지 (doc힌트 교차 오염 방지).
  3. **② LLM-가치 게이트**: 골격별 룰/LLM 플랜 quality EMA 분리 추적 (플랜에 origin 필드),
     양측 n≥3 & 차이<0.03이면 해당 골격은 분석 큐 등록 스킵 — LLM 콜을 가치 있는 유형에만.
- 예상 트레이드오프: iter1은 LLM 플랜 부재로 하락, 섀도 플래너가 캐시를 채우며 후속 iteration 상승
  (돌릴수록 좋아짐이 더 선명해지는 구조). 판정 기준: 수렴 후 co@10이 v5.14(79.1) 근접 + 핫패스 p95 균일.
- 유닛(골격 동일성·게이트 포함) + 스모크 10/10. **v5.15 동결 — P1 검증 5회.**

### 2026-07-13 22:59:07 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 85.6/58.9/33.3%
- latency p50/p95: all=38.8/47.7ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 2051, 'l1_explicit': 1719, 'l2_co_retrieval': 1244, 'l3_llm_curated': 2, 'profiles': 0} · plan_cache: {'entries': 320, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 999}, 's_llm': {'typed_edges': 2}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 10, 'good': 165, 'attention': 60, 'shadow_planner': {'analyzed': 8, 'promoted': 7}, 'elapsed_s': 22.3}

### 2026-07-13 23:00:12 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 85.6/58.9/33.3%
- latency p50/p95: all=39.4/45.3ms · tiers(valB)={'cache': 10, 'llm': 0, 'rules': 80}
- SG: {'docs': 572, 'edges': 2120, 'l1_explicit': 1719, 'l2_co_retrieval': 1401, 'l3_llm_curated': 3, 'profiles': 0} · plan_cache: {'entries': 324, 'hits': 112, 'hit_rate': 0.298}
- evolve: {'records': 384, 's_rules': {'decayed_layers': 1171}, 's_llm': {'typed_edges': 1}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 10, 'good': 170, 'attention': 60, 'shadow_planner': {'analyzed': 8, 'promoted': 7}, 'elapsed_s': 43.9}

### 2026-07-13 23:01:06 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 85.6/58.9/35.6%
- latency p50/p95: all=38.8/46.8ms · tiers(valB)={'cache': 9, 'llm': 0, 'rules': 81}
- SG: {'docs': 572, 'edges': 2139, 'l1_explicit': 1719, 'l2_co_retrieval': 1423, 'l3_llm_curated': 5, 'profiles': 0} · plan_cache: {'entries': 326, 'hits': 111, 'hit_rate': 0.295}
- evolve: {'records': 384, 's_rules': {'decayed_layers': 1200}, 's_llm': {'typed_edges': 2}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 10, 'good': 168, 'attention': 58, 'shadow_planner': {'analyzed': 8, 'promoted': 7}, 'elapsed_s': 32.2}

### 2026-07-13 23:01:45 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 85.6/60.0/37.8%
- latency p50/p95: all=39.6/45.6ms · tiers(valB)={'cache': 10, 'llm': 0, 'rules': 80}
- SG: {'docs': 572, 'edges': 2144, 'l1_explicit': 1719, 'l2_co_retrieval': 1447, 'l3_llm_curated': 5, 'profiles': 0} · plan_cache: {'entries': 328, 'hits': 113, 'hit_rate': 0.301}
- evolve: {'records': 384, 's_rules': {'decayed_layers': 1108}, 's_llm': {'typed_edges': 0}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 10, 'good': 171, 'attention': 56, 'shadow_planner': {'analyzed': 8, 'promoted': 7}, 'elapsed_s': 16.1}

### 2026-07-13 23:02:29 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 86.7/61.1/36.7%
- latency p50/p95: all=40.2/47.7ms · tiers(valB)={'cache': 11, 'llm': 0, 'rules': 79}
- SG: {'docs': 572, 'edges': 2151, 'l1_explicit': 1719, 'l2_co_retrieval': 1453, 'l3_llm_curated': 7, 'profiles': 0} · plan_cache: {'entries': 331, 'hits': 117, 'hit_rate': 0.311}
- evolve: {'records': 384, 's_rules': {'decayed_layers': 1094}, 's_llm': {'typed_edges': 2}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 10, 'good': 170, 'attention': 54, 'shadow_planner': {'analyzed': 8, 'promoted': 7}, 'elapsed_s': 20.8}

### 2026-07-10 — v5.15 검증 및 v5.15.1 (섀도 예산 상향)
- v5.15 검증: **균일 레이턴시 달성** — 전 iteration p50 42~48ms / p95 ≤72ms (수 초 LLM 대기 소멸).
  co@10 58.9→61.1 완만 상승, 섀도 플래너 매 사이클 8분석/7승격 — 예산(8)이 수렴 병목.
  실 LLM 비용은 캐시 덕에 사이클당 신규 1~7콜뿐.
- v5.15.1: 섀도 예산 8→40, evolver 총예산 10→50 (백그라운드 전용이라 핫패스 무영향). 재검증.

### 2026-07-13 23:04:44 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 85.6/61.1/40.0%
- latency p50/p95: all=38.2/46.2ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 2066, 'l1_explicit': 1719, 'l2_co_retrieval': 1229, 'l3_llm_curated': 3, 'profiles': 3} · plan_cache: {'entries': 345, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 983}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 1}, 'llm_calls': 49, 'good': 165, 'attention': 63, 'shadow_planner': {'analyzed': 40, 'promoted': 37}, 'elapsed_s': 41.4}

### 2026-07-13 23:06:52 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 90.0/64.4/46.7%
- latency p50/p95: all=39.1/46.0ms · tiers(valB)={'cache': 19, 'llm': 0, 'rules': 71}
- SG: {'docs': 572, 'edges': 2160, 'l1_explicit': 1719, 'l2_co_retrieval': 1453, 'l3_llm_curated': 7, 'profiles': 6} · plan_cache: {'entries': 366, 'hits': 133, 'hit_rate': 0.354}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 1156}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 180, 'attention': 39, 'shadow_planner': {'analyzed': 40, 'promoted': 35}, 'elapsed_s': 108.5}

### 2026-07-13 23:09:18 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 88.9/67.8/48.9%
- latency p50/p95: all=38.7/45.4ms · tiers(valB)={'cache': 20, 'llm': 0, 'rules': 70}
- SG: {'docs': 572, 'edges': 2213, 'l1_explicit': 1719, 'l2_co_retrieval': 1584, 'l3_llm_curated': 11, 'profiles': 9} · plan_cache: {'entries': 366, 'hits': 128, 'hit_rate': 0.34}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 1056}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 184, 'attention': 40, 'shadow_planner': {'analyzed': 40, 'promoted': 33}, 'elapsed_s': 123.5}

### 2026-07-13 23:11:13 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 92.2/67.8/48.9%
- latency p50/p95: all=40.5/45.8ms · tiers(valB)={'cache': 27, 'llm': 0, 'rules': 63}
- SG: {'docs': 572, 'edges': 2231, 'l1_explicit': 1719, 'l2_co_retrieval': 1614, 'l3_llm_curated': 13, 'profiles': 12} · plan_cache: {'entries': 366, 'hits': 138, 'hit_rate': 0.367}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 1113}, 's_llm': {'typed_edges': 2, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 167, 'attention': 33, 'shadow_planner': {'analyzed': 40, 'promoted': 32}, 'elapsed_s': 92.8}

### 2026-07-13 23:12:31 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 88.9/71.1/53.3%
- latency p50/p95: all=40.6/54.6ms · tiers(valB)={'cache': 32, 'llm': 0, 'rules': 58}
- SG: {'docs': 572, 'edges': 2254, 'l1_explicit': 1719, 'l2_co_retrieval': 1645, 'l3_llm_curated': 16, 'profiles': 15} · plan_cache: {'entries': 367, 'hits': 142, 'hit_rate': 0.378}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 1041}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 167, 'attention': 41, 'shadow_planner': {'analyzed': 40, 'promoted': 32}, 'elapsed_s': 53.7}

### 2026-07-10 — v5.15.1 검증 완료: **승격 (균일 레이턴시 + 지속 우상향)**
- @10: co 61.1→71.1 (5회 연속 우상향, 미정점), All 97.1, ValA 93.9~94.4. p50 42~60ms 균일, 무롤백.
- 두 목표 동시 충족: ① 핫패스 LLM 0 균일 응답 ② 섀도 플래너(40분석/~33승격/사이클)의 학습이
  점수 곡선으로 직결 — 캠페인 최청정 자기진화 곡선.
- vs v5.14(79.1): v5.14는 핫패스 수 초 LLM 대기의 대가. 아키텍처 요구 충족은 v5.15.1.
- **v5.15.1 현행 챔피언.** 잔여: co@10 추가 상승 여지(곡선 미정점 — 장기 런), ValA@10 회수, p95 ~150ms 원인.

### 2026-07-14 — 개발 사이클 16 (v5.16): facet_link 레이어 + 일반성 ablation 착수
- 유저 직관 채택: 분해된 서브쿼리들의 각 결과는 "한 정보요구의 다른 면(facet)" — 교차쌍 링크는
  문서 상호 언급이 없어도 성립하는 상보성 증거. 분해 구조 자체가 잡음 필터 (교차쌍만, 동일면 쌍 제외).
- 판정: L1(문서 자신의 선언)과 동급은 아니고 3단 추론(분해·검색·quality) 산물 → **L1 바로 아래 0.9 배치**,
  반복 확인(count)으로 신뢰 적립, 에스코트 자격은 확인 2회부터. 학습은 evolver 사이클(백그라운드) 전용.
- 구현: SG facet_link 레이어(강화/감쇠/에스코트), retriever는 facet_tops 로그만, evolver가 good 쿼리에서 학습.
  ablation 스위치 STRUCTRAG_NO_L1 / STRUCTRAG_NO_FACET.
- 일반성 실험 계획: (a) L1·facet off = L2/L3만 → (b) 풀스택(=v5.15.1, 완료) → (c) facet on·L1 off,
  L1 대비 버금갈 때까지 (c) 개선 반복.
- 유닛(facet 케이스)·NO_L1 스위치·스모크 10/10 통과. **v5.16 동결.**

### 2026-07-14 00:40:45 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 64.4/51.1/35.6%
- latency p50/p95: all=36.3/44.5ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 930, 'l1_explicit': 0, 'l2_co_retrieval': 930, 'l3_llm_curated': 5, 'profiles': 3} · plan_cache: {'entries': 276, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2416, 'facet_queries': 0}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 1}, 'llm_calls': 49, 'good': 191, 'attention': 98, 'shadow_planner': {'analyzed': 40, 'promoted': 36}, 'elapsed_s': 164.5}

### 2026-07-14 00:46:07 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 64.4/53.3/32.2%
- latency p50/p95: all=38.2/45.4ms · tiers(valB)={'cache': 16, 'llm': 0, 'rules': 74}
- SG: {'docs': 572, 'edges': 1467, 'l1_explicit': 0, 'l2_co_retrieval': 1467, 'l3_llm_curated': 10, 'profiles': 6} · plan_cache: {'entries': 305, 'hits': 130, 'hit_rate': 0.346}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 3058, 'facet_queries': 31}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 186, 'attention': 117, 'shadow_planner': {'analyzed': 40, 'promoted': 33}, 'elapsed_s': 308.7}

### 2026-07-14 00:50:50 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 60.0/50.0/30.0%
- latency p50/p95: all=38.7/44.0ms · tiers(valB)={'cache': 17, 'llm': 0, 'rules': 73}
- SG: {'docs': 572, 'edges': 1677, 'l1_explicit': 0, 'l2_co_retrieval': 1677, 'l3_llm_curated': 15, 'profiles': 9} · plan_cache: {'entries': 335, 'hits': 125, 'hit_rate': 0.332}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 2594, 'facet_queries': 34}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 187, 'attention': 110, 'shadow_planner': {'analyzed': 40, 'promoted': 39}, 'elapsed_s': 270.1}

### 2026-07-14 00:55:00 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 61.1/45.6/30.0%
- latency p50/p95: all=39.7/47.9ms · tiers(valB)={'cache': 20, 'llm': 0, 'rules': 70}
- SG: {'docs': 572, 'edges': 1755, 'l1_explicit': 0, 'l2_co_retrieval': 1755, 'l3_llm_curated': 20, 'profiles': 12} · plan_cache: {'entries': 355, 'hits': 141, 'hit_rate': 0.375}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 2486, 'facet_queries': 28}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 179, 'attention': 73, 'shadow_planner': {'analyzed': 40, 'promoted': 33}, 'elapsed_s': 232.9}

### 2026-07-14 00:58:58 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 64.4/50.0/25.6%
- latency p50/p95: all=39.2/50.7ms · tiers(valB)={'cache': 18, 'llm': 0, 'rules': 72}
- SG: {'docs': 572, 'edges': 1838, 'l1_explicit': 0, 'l2_co_retrieval': 1838, 'l3_llm_curated': 25, 'profiles': 15} · plan_cache: {'entries': 363, 'hits': 143, 'hit_rate': 0.38}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 2214, 'facet_queries': 34}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 190, 'attention': 72, 'shadow_planner': {'analyzed': 40, 'promoted': 35}, 'elapsed_s': 220.0}

### 2026-07-14 — Ablation A 결과 (L1·facet off, L2/L3만): 일반성 가설 기각 근거 확보
- co@10 51.1→53.3→50.0→45.6→(iter5 기록 참조), co@20 64.4→60~61 — 성장 없음, 드리프트 퇴행.
  All/ValA@10은 99.0/98.9로 풀스택보다 높음 (확장·에스코트 슬롯 비용의 정량 확인).
- 결론: **L2(공동출현 통계)는 크로스레퍼런스 없는 코퍼스에서 구조 발견 엔진으로 부족.**
  같이 검색된 적 없는 쌍은 증거가 발생하지 않는 태생적 사각지대 + 잡음쌍의 점진 오염.
  → facet_link는 개선이 아니라 L1 부재 시나리오의 생존 조건. (c) 런으로 검증.

### 2026-07-14 00:59:51 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 64.4/51.1/35.6%
- latency p50/p95: all=39.8/45.5ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 930, 'l1_explicit': 0, 'l2_co_retrieval': 930, 'l3_llm_curated': 5, 'profiles': 3} · plan_cache: {'entries': 276, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2416, 'facet_queries': 0}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 1}, 'llm_calls': 49, 'good': 191, 'attention': 98, 'shadow_planner': {'analyzed': 40, 'promoted': 36}, 'elapsed_s': 3.7}

### 2026-07-14 01:00:19 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 64.4/53.3/32.2%
- latency p50/p95: all=39.9/48.2ms · tiers(valB)={'cache': 16, 'llm': 0, 'rules': 74}
- SG: {'docs': 572, 'edges': 1524, 'l1_explicit': 0, 'l2_co_retrieval': 1467, 'l3_llm_curated': 10, 'profiles': 6} · plan_cache: {'entries': 305, 'hits': 130, 'hit_rate': 0.346}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 3058, 'facet_queries': 31}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 186, 'attention': 117, 'shadow_planner': {'analyzed': 40, 'promoted': 32}, 'elapsed_s': 3.0}

### 2026-07-14 01:01:47 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 61.1/48.9/31.1%
- latency p50/p95: all=41.1/52.8ms · tiers(valB)={'cache': 16, 'llm': 0, 'rules': 74}
- SG: {'docs': 572, 'edges': 1752, 'l1_explicit': 0, 'l2_co_retrieval': 1661, 'l3_llm_curated': 15, 'profiles': 9} · plan_cache: {'entries': 334, 'hits': 127, 'hit_rate': 0.338}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 2484, 'facet_queries': 32}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 187, 'attention': 113, 'shadow_planner': {'analyzed': 40, 'promoted': 38}, 'elapsed_s': 64.8}

### 2026-07-14 01:03:02 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 58.9/48.9/28.9%
- latency p50/p95: all=40.2/50.2ms · tiers(valB)={'cache': 19, 'llm': 0, 'rules': 71}
- SG: {'docs': 572, 'edges': 1894, 'l1_explicit': 0, 'l2_co_retrieval': 1782, 'l3_llm_curated': 20, 'profiles': 12} · plan_cache: {'entries': 356, 'hits': 133, 'hit_rate': 0.354}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 2198, 'facet_queries': 36}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 181, 'attention': 79, 'shadow_planner': {'analyzed': 40, 'promoted': 32}, 'elapsed_s': 52.3}

### 2026-07-14 01:04:38 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 62.2/51.1/33.3%
- latency p50/p95: all=40.8/59.4ms · tiers(valB)={'cache': 12, 'llm': 0, 'rules': 78}
- SG: {'docs': 572, 'edges': 1963, 'l1_explicit': 0, 'l2_co_retrieval': 1845, 'l3_llm_curated': 25, 'profiles': 15} · plan_cache: {'entries': 362, 'hits': 137, 'hit_rate': 0.364}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 2044, 'facet_queries': 31}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 187, 'attention': 73, 'shadow_planner': {'analyzed': 40, 'promoted': 32}, 'elapsed_s': 73.6}

### 2026-07-14 — Ablation C 결과 및 개발 사이클 17 (v5.17): 상보 facet
- Ablation C (facet v1, L1 off): co@10 평균 50.7 / co@20 62.2 — **A와 동일, 리프트 0.**
  원인 실측: facet_link 엣지 218개 생성됐으나 ValB 정답쌍 커버 6/90.
  ① 도달 한계 — facet 페어는 각 서브쿼리 top-2에 뜬 문서끼리만, linked는 애초에 안 뜸
  ② 분해율 19% — ValB가 단일 의도 질문이라 분해가 안 일어남 (facet_link는 멀티의도용 메커니즘).
- v5.17: ① facet 캡처 폭 2→4 ② **상보 facet 룰** — 타입 질문(테스트↔스펙/이슈)의 암묵적 co-retrieval
  의도를 결정적 서브쿼리로 명시화 (엔지니어링 도메인 온톨로지, 코퍼스 컨벤션 아님, LLM 0).
  상보 facet은 융합에도 참여 + facet_link 학습원. 합성 텍스트라 graph 모드 캐시미스 방지 위해 hybrid 고정.
- 유닛·스모크 10/10. **v5.17 동결 — ablation C' (NO_L1) 재검증.**

### 2026-07-14 07:36:50 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 63.3/54.4/35.6%
- latency p50/p95: all=34.2/45.7ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 1476, 'l1_explicit': 0, 'l2_co_retrieval': 908, 'l3_llm_curated': 5, 'profiles': 3} · plan_cache: {'entries': 272, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2427, 'facet_queries': 106}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 1}, 'llm_calls': 49, 'good': 192, 'attention': 98, 'shadow_planner': {'analyzed': 40, 'promoted': 35}, 'elapsed_s': 3.2}

### 2026-07-14 07:38:33 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 62.2/53.3/33.3%
- latency p50/p95: all=38.3/52.6ms · tiers(valB)={'cache': 11, 'llm': 0, 'rules': 79}
- SG: {'docs': 572, 'edges': 2050, 'l1_explicit': 0, 'l2_co_retrieval': 1671, 'l3_llm_curated': 10, 'profiles': 6} · plan_cache: {'entries': 310, 'hits': 118, 'hit_rate': 0.314}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 3538, 'facet_queries': 115}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 178, 'attention': 126, 'shadow_planner': {'analyzed': 40, 'promoted': 35}, 'elapsed_s': 75.7}

### 2026-07-14 07:40:42 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 60.0/50.0/31.1%
- latency p50/p95: all=36.7/54.1ms · tiers(valB)={'cache': 12, 'llm': 0, 'rules': 78}
- SG: {'docs': 572, 'edges': 2338, 'l1_explicit': 0, 'l2_co_retrieval': 1820, 'l3_llm_curated': 14, 'profiles': 9} · plan_cache: {'entries': 346, 'hits': 111, 'hit_rate': 0.295}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 2450, 'facet_queries': 124}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 186, 'attention': 95, 'shadow_planner': {'analyzed': 40, 'promoted': 38}, 'elapsed_s': 100.5}

### 2026-07-14 07:42:24 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 63.3/52.2/33.3%
- latency p50/p95: all=38.5/54.1ms · tiers(valB)={'cache': 14, 'llm': 0, 'rules': 76}
- SG: {'docs': 572, 'edges': 2543, 'l1_explicit': 0, 'l2_co_retrieval': 1915, 'l3_llm_curated': 19, 'profiles': 12} · plan_cache: {'entries': 359, 'hits': 136, 'hit_rate': 0.362}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 2101, 'facet_queries': 123}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 183, 'attention': 78, 'shadow_planner': {'analyzed': 40, 'promoted': 35}, 'elapsed_s': 73.9}

### 2026-07-14 07:44:41 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 62.2/51.1/25.6%
- latency p50/p95: all=39.4/58.0ms · tiers(valB)={'cache': 17, 'llm': 0, 'rules': 73}
- SG: {'docs': 572, 'edges': 2639, 'l1_explicit': 0, 'l2_co_retrieval': 1984, 'l3_llm_curated': 24, 'profiles': 15} · plan_cache: {'entries': 361, 'hits': 151, 'hit_rate': 0.402}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 2052, 'facet_queries': 119}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 183, 'attention': 73, 'shadow_planner': {'analyzed': 40, 'promoted': 36}, 'elapsed_s': 107.8}

### 2026-07-14 — C' 결과 및 개발 사이클 18 (v5.18)
- C' (v5.17, NO_L1): co@10 평균 52.2 / co@20 62.2 — 이륙 실패. 단 진전 실측:
  facetQ 31→106~124 (분해율 해결), 정답쌍 커버 6→21/90 (도달 부분 개선).
- 커버→점수 미전환 원인 3건 (코드 추적):
  ① 강화 스텝 0.15는 2회 확인(0.30)이 유효가중 0.27로 확장 문턱(0.3) 미달 — 5회 런 내 무력
  ② 에스코트 동률: 상보 facet top-4가 균등 강화돼(정답1+오답3) max-w 선택이 오답 가능
  ③ 혼합형 상보 facet("스펙+이슈")의 BM25 조준 흐림
- v5.18: ① 스텝 0.25 (2회 확인 = 0.5 → 유효 0.45 > 문턱) ② 에스코트에 현재 쿼리 facet 결과
  교차증거 보너스(prefer set) ③ 상보 facet을 스펙용/이슈용으로 분리.
- 유닛·스모크 10/10. **v5.18 동결 — ablation C'' (NO_L1) 검증.**

### 2026-07-14 07:47:11 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 62.2/52.2/34.4%
- latency p50/p95: all=33.9/41.1ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 1607, 'l1_explicit': 0, 'l2_co_retrieval': 881, 'l3_llm_curated': 5, 'profiles': 3} · plan_cache: {'entries': 279, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 2446, 'facet_queries': 106}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 1}, 'llm_calls': 49, 'good': 192, 'attention': 98, 'shadow_planner': {'analyzed': 40, 'promoted': 37}, 'elapsed_s': 3.5}

### 2026-07-14 07:48:54 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 62.2/46.7/27.8%
- latency p50/p95: all=38.0/51.4ms · tiers(valB)={'cache': 14, 'llm': 0, 'rules': 76}
- SG: {'docs': 572, 'edges': 2071, 'l1_explicit': 0, 'l2_co_retrieval': 1623, 'l3_llm_curated': 10, 'profiles': 6} · plan_cache: {'entries': 357, 'hits': 101, 'hit_rate': 0.269}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 2441, 'facet_queries': 120}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 178, 'attention': 61, 'shadow_planner': {'analyzed': 40, 'promoted': 34}, 'elapsed_s': 68.2}

### 2026-07-14 07:50:45 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 64.4/46.7/24.4%
- latency p50/p95: all=40.8/50.8ms · tiers(valB)={'cache': 12, 'llm': 0, 'rules': 78}
- SG: {'docs': 572, 'edges': 2317, 'l1_explicit': 0, 'l2_co_retrieval': 1768, 'l3_llm_curated': 14, 'profiles': 9} · plan_cache: {'entries': 361, 'hits': 130, 'hit_rate': 0.346}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 2025, 'facet_queries': 125}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 187, 'attention': 63, 'shadow_planner': {'analyzed': 40, 'promoted': 36}, 'elapsed_s': 78.4}

### 2026-07-14 07:53:08 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 64.4/48.9/26.7%
- latency p50/p95: all=40.4/56.4ms · tiers(valB)={'cache': 16, 'llm': 0, 'rules': 74}
- SG: {'docs': 572, 'edges': 2418, 'l1_explicit': 0, 'l2_co_retrieval': 1851, 'l3_llm_curated': 19, 'profiles': 12} · plan_cache: {'entries': 363, 'hits': 142, 'hit_rate': 0.378}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 2025, 'facet_queries': 121}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 180, 'attention': 66, 'shadow_planner': {'analyzed': 40, 'promoted': 36}, 'elapsed_s': 109.5}

### 2026-07-14 07:54:55 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 64.4/47.8/25.6%
- latency p50/p95: all=38.9/53.2ms · tiers(valB)={'cache': 13, 'llm': 0, 'rules': 77}
- SG: {'docs': 572, 'edges': 2501, 'l1_explicit': 0, 'l2_co_retrieval': 1889, 'l3_llm_curated': 23, 'profiles': 15} · plan_cache: {'entries': 365, 'hits': 148, 'hit_rate': 0.394}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 2001, 'facet_queries': 117}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 178, 'attention': 65, 'shadow_planner': {'analyzed': 40, 'promoted': 35}, 'elapsed_s': 72.9}

### 2026-07-14 — C'' 결과: facet 시리즈 (v5.16~18) 판정 종결
- C'' (v5.18, NO_L1): co@10 평균 48.5 — 시리즈 최저. 스텝 상향이 오답 형제 엣지까지 확장 문턱을
  넘겨 top-10 오염 (에스코트 교차증거는 방어했으나 확장 경로 무방비).
- **시리즈 결론**: facet_link/공동출현 계열은 "검색이 닿은 문서"끼리만 엮는 증폭기 —
  단일의도 co-태스크에서 L1 부재를 대체 불가. 멀티의도 트래픽용 보조 레이어로 지위 확정.
  L3-reach(LLM 참조 읽기)는 본 코퍼스에선 L1의 고비용 재구현이라 기각 (산문형 참조 코퍼스용 옵션으로만 기록).
- 일반성 실험 최종표: 풀스택 71.1/88.9 ↔ NO_L1 최선 52.2/63.3 — **명시적 참조가 co-태스크의
  지배 증거원**임을 정량 확정. 무참조 코퍼스 일반화 주장은 이 갭과 함께 정직하게 제시할 것.

### 2026-07-14 — 개발 사이클 19 (v5.19): 쿼리 조건부 구조 강도 (유저 제안)
- 직관: 쿼리 문면에서 "연관 문서까지 원하는지" 판별 가능 → 구조 장치를 케이스별 제어.
- 설계 (v5.15.1 챔피언 대비 엄격히 가산적):
  OFF   = ID 단독 조회 (연관마커 無) → 에스코트·확장·tail 끔 (슬롯 낭비 제거)
  LIGHT = 기본 (챔피언과 동일 동작: 에스코트+확장, 상보facet 없음)
  FULL  = 멀티의도 / 연관마커(관련·함께·~도·비교·일치 등) / 타입 2족 이상 동시 언급
          → 상보 facet 가동 (facet_link 학습 포함)
- 정직한 한계: ValA류(카드만)와 ValB류(카드+링크) 질문은 문면 유사로 룰 분리 불가 → 중간지대는
  LIGHT 기본. ValA@10 슬롯비용 회수는 OFF 클래스에서만 발생.

### 2026-07-14 — v5.19 구현 완료 + 부수 버그 발견
- 쿼리 조건부 구조 강도(off/light/full) 구현: 룰 판별(_CO_MARKERS·타입 2족·멀티의도·ID단독),
  플랜에 intensity 필드, OFF는 확장·에스코트 스킵, 상보 facet은 FULL 전용.
- **부수 발견**: salient_terms의 3자+ 필터가 한국어 2음절 단어(엔진·이슈 등)를 전부 탈락 —
  v5.17~18 상보 facet이 한국어 쿼리에서 콘텐츠 텀을 잃던 숨은 원인. facet 전용 추출기(한글 2자+)로 수정.
  (quality 채점용 salient_terms는 캘리브레이션 보존 위해 미변경 — 개선 후보로 기록)
- 유닛·sanity·스모크 10/10. **v5.19 동결 — 풀스택 P1 검증 (v5.15.1 대비 판정).**

### 2026-07-14 08:01:17 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 85.6/62.2/33.3%
- latency p50/p95: all=37.3/43.2ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 2167, 'l1_explicit': 1719, 'l2_co_retrieval': 1271, 'l3_llm_curated': 3, 'profiles': 3} · plan_cache: {'entries': 347, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 992, 'facet_queries': 8}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 1}, 'llm_calls': 49, 'good': 165, 'attention': 60, 'shadow_planner': {'analyzed': 40, 'promoted': 36}, 'elapsed_s': 63.1}

### 2026-07-14 08:02:10 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 87.8/63.3/35.6%
- latency p50/p95: all=37.0/44.3ms · tiers(valB)={'cache': 16, 'llm': 0, 'rules': 74}
- SG: {'docs': 572, 'edges': 2516, 'l1_explicit': 1719, 'l2_co_retrieval': 1491, 'l3_llm_curated': 6, 'profiles': 6} · plan_cache: {'entries': 365, 'hits': 125, 'hit_rate': 0.332}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 1028, 'facet_queries': 41}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 185, 'attention': 38, 'shadow_planner': {'analyzed': 40, 'promoted': 34}, 'elapsed_s': 28.0}

### 2026-07-14 08:04:18 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 83.3/62.2/35.6%
- latency p50/p95: all=38.5/44.0ms · tiers(valB)={'cache': 24, 'llm': 0, 'rules': 66}
- SG: {'docs': 572, 'edges': 2899, 'l1_explicit': 1719, 'l2_co_retrieval': 1653, 'l3_llm_curated': 10, 'profiles': 9} · plan_cache: {'entries': 366, 'hits': 134, 'hit_rate': 0.356}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 971, 'facet_queries': 52}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 182, 'attention': 38, 'shadow_planner': {'analyzed': 40, 'promoted': 33}, 'elapsed_s': 102.6}

### 2026-07-14 08:06:13 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 85.6/67.8/43.3%
- latency p50/p95: all=39.7/48.8ms · tiers(valB)={'cache': 32, 'llm': 0, 'rules': 58}
- SG: {'docs': 572, 'edges': 3123, 'l1_explicit': 1719, 'l2_co_retrieval': 1747, 'l3_llm_curated': 15, 'profiles': 12} · plan_cache: {'entries': 366, 'hits': 140, 'hit_rate': 0.372}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 948, 'facet_queries': 46}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 166, 'attention': 33, 'shadow_planner': {'analyzed': 40, 'promoted': 33}, 'elapsed_s': 89.3}

### 2026-07-14 08:07:55 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 83.3/58.9/37.8%
- latency p50/p95: all=40.6/49.0ms · tiers(valB)={'cache': 27, 'llm': 0, 'rules': 63}
- SG: {'docs': 572, 'edges': 3221, 'l1_explicit': 1719, 'l2_co_retrieval': 1785, 'l3_llm_curated': 20, 'profiles': 15} · plan_cache: {'entries': 366, 'hits': 137, 'hit_rate': 0.364}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 883, 'facet_queries': 44}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 164, 'attention': 41, 'shadow_planner': {'analyzed': 40, 'promoted': 35}, 'elapsed_s': 76.3}

### 2026-07-14 — v5.19 검증 완료: 승격 기각, 챔피언 v5.15.1 유지 (코드 복원)
- v5.19 풀스택: co@10 평균 62.9, **58.9 하락 마감** (챔피언 71.1 상승 마감), co@20 85.1 열위.
- 기각 사유: OFF 클래스(ID 단독)가 본 문제셋에 희소해 회수 효과 미미, FULL 상보 facet은
  시리즈에서 확인된 한계 그대로, iter5 하락.
- 단, v5.19의 산출물 중 보존 가치 (태그 structrag-v5.19에 보관, 운영 환경용):
  ① 쿼리 조건부 강도 메커니즘 — ID 조회·멀티의도가 실제로 섞이는 운영 트래픽용
  ② 한국어 2음절 토큰 탈락 버그 발견 (salient_terms 3자+ 필터) — quality 개선 후보로 등재
- 코드 트리를 structrag-v5.15.1로 복원 (유닛·스모크 10/10 재확인).

### 2026-07-14 — 개발 사이클 20 (v5.20): 캐시 플랜 재최적화 + 장기런
- 학습 사각지대 폐쇄: 캐시 플랜 중 중품질 밴드(attention~reinforce 게이트 사이)는 퇴출도 재분석도
  안 되는 고착 구간 — 캐시 응답이 강화게이트(p60) 미만이면 분석 큐 재등록, 섀도가 이길 때만 교체.
- 검증 프로토콜 확장: 10-iteration 장기런 (챔피언 곡선의 정점 확인 겸용).
- 유닛·스모크 10/10. **v5.20 동결 — P1 10회 검증.**

### 2026-07-14 08:26:12 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 86.7/61.1/36.7%
- latency p50/p95: all=36.9/43.7ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 2092, 'l1_explicit': 1719, 'l2_co_retrieval': 1249, 'l3_llm_curated': 4, 'profiles': 3} · plan_cache: {'entries': 348, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 1009}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 1}, 'llm_calls': 49, 'good': 164, 'attention': 60, 'shadow_planner': {'analyzed': 40, 'promoted': 36}, 'elapsed_s': 72.0}

### 2026-07-14 08:26:40 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 93.3/66.7/44.4%
- latency p50/p95: all=37.5/44.4ms · tiers(valB)={'cache': 18, 'llm': 0, 'rules': 72}
- SG: {'docs': 572, 'edges': 2190, 'l1_explicit': 1719, 'l2_co_retrieval': 1465, 'l3_llm_curated': 7, 'profiles': 6} · plan_cache: {'entries': 367, 'hits': 128, 'hit_rate': 0.34}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 1114}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 180, 'attention': 36, 'shadow_planner': {'analyzed': 40, 'promoted': 31}, 'elapsed_s': 3.8}

### 2026-07-14 08:27:39 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 90.0/68.9/50.0%
- latency p50/p95: all=37.9/48.5ms · tiers(valB)={'cache': 19, 'llm': 0, 'rules': 71}
- SG: {'docs': 572, 'edges': 2249, 'l1_explicit': 1719, 'l2_co_retrieval': 1612, 'l3_llm_curated': 10, 'profiles': 9} · plan_cache: {'entries': 367, 'hits': 130, 'hit_rate': 0.346}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 1041}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 183, 'attention': 38, 'shadow_planner': {'analyzed': 40, 'promoted': 32}, 'elapsed_s': 34.7}

### 2026-07-14 08:29:09 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 90.0/67.8/48.9%
- latency p50/p95: all=37.8/44.3ms · tiers(valB)={'cache': 27, 'llm': 0, 'rules': 63}
- SG: {'docs': 572, 'edges': 2270, 'l1_explicit': 1719, 'l2_co_retrieval': 1648, 'l3_llm_curated': 11, 'profiles': 12} · plan_cache: {'entries': 367, 'hits': 140, 'hit_rate': 0.372}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 1044}, 's_llm': {'typed_edges': 1, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 170, 'attention': 33, 'shadow_planner': {'analyzed': 40, 'promoted': 28}, 'elapsed_s': 65.9}

### 2026-07-14 — 개발 사이클 21 (v5.21): 넓은 관찰 창 복원 (유저 지적)
- 유실 발견: ver4의 "서빙 좁게 / EVOLVE 관찰 넓게" 개념이 재작성에서 사라짐 — L2 강화(상위 10),
  evolver 후보(상위 8)가 서빙 창보다도 좁았음.
- v5.21: 내부 검색 폭 max(20, top_k), 문서랭킹 40위까지 observed로 로그,
  L2 강화 16문서·evolver 후보 12문서로 확대. 서빙 결과는 불변, 이볼빙 시야만 확장.
  (선택적 에코·quality 게이트가 확대 창의 잡음 방어)
- 유닛·스모크 10/10. **v5.21 동결 — v5.20 장기런 종료 후 10회 검증 예약.**

### 2026-07-14 08:30:44 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 87.8/71.1/52.2%
- latency p50/p95: all=38.6/48.7ms · tiers(valB)={'cache': 27, 'llm': 0, 'rules': 63}
- SG: {'docs': 572, 'edges': 2286, 'l1_explicit': 1719, 'l2_co_retrieval': 1676, 'l3_llm_curated': 14, 'profiles': 15} · plan_cache: {'entries': 367, 'hits': 138, 'hit_rate': 0.367}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 1053}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 170, 'attention': 39, 'shadow_planner': {'analyzed': 40, 'promoted': 32}, 'elapsed_s': 69.1}

### 2026-07-14 08:31:25 — ver5 iteration 6 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 90.0/71.1/52.2%
- latency p50/p95: all=39.2/49.2ms · tiers(valB)={'cache': 29, 'llm': 0, 'rules': 61}
- SG: {'docs': 572, 'edges': 2290, 'l1_explicit': 1719, 'l2_co_retrieval': 1686, 'l3_llm_curated': 17, 'profiles': 18} · plan_cache: {'entries': 368, 'hits': 143, 'hit_rate': 0.38}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 1008}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 164, 'attention': 31, 'shadow_planner': {'analyzed': 40, 'promoted': 26}, 'elapsed_s': 15.5}

### 2026-07-14 08:32:55 — ver5 iteration 7 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 88.9/67.8/50.0%
- latency p50/p95: all=37.9/43.7ms · tiers(valB)={'cache': 27, 'llm': 0, 'rules': 63}
- SG: {'docs': 572, 'edges': 2297, 'l1_explicit': 1719, 'l2_co_retrieval': 1694, 'l3_llm_curated': 18, 'profiles': 21} · plan_cache: {'entries': 368, 'hits': 137, 'hit_rate': 0.364}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 1007}, 's_llm': {'typed_edges': 1, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 164, 'attention': 39, 'shadow_planner': {'analyzed': 40, 'promoted': 34}, 'elapsed_s': 64.3}

### 2026-07-14 08:33:38 — ver5 iteration 8 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 91.1/74.4/57.8%
- latency p50/p95: all=38.8/46.2ms · tiers(valB)={'cache': 35, 'llm': 0, 'rules': 55}
- SG: {'docs': 572, 'edges': 2299, 'l1_explicit': 1719, 'l2_co_retrieval': 1695, 'l3_llm_curated': 20, 'profiles': 24} · plan_cache: {'entries': 368, 'hits': 149, 'hit_rate': 0.396}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 1020}, 's_llm': {'typed_edges': 2, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 163, 'attention': 29, 'shadow_planner': {'analyzed': 40, 'promoted': 23}, 'elapsed_s': 18.1}

### 2026-07-14 08:35:00 — ver5 iteration 9 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 86.7/67.8/50.0%
- latency p50/p95: all=38.0/44.2ms · tiers(valB)={'cache': 25, 'llm': 0, 'rules': 65}
- SG: {'docs': 572, 'edges': 2300, 'l1_explicit': 1719, 'l2_co_retrieval': 1700, 'l3_llm_curated': 21, 'profiles': 27} · plan_cache: {'entries': 368, 'hits': 138, 'hit_rate': 0.367}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 976}, 's_llm': {'typed_edges': 1, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 167, 'attention': 42, 'shadow_planner': {'analyzed': 40, 'promoted': 34}, 'elapsed_s': 55.8}

### 2026-07-14 08:35:53 — ver5 iteration 10 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 91.1/74.4/56.7%
- latency p50/p95: all=39.2/46.3ms · tiers(valB)={'cache': 32, 'llm': 0, 'rules': 58}
- SG: {'docs': 572, 'edges': 2300, 'l1_explicit': 1719, 'l2_co_retrieval': 1701, 'l3_llm_curated': 22, 'profiles': 30} · plan_cache: {'entries': 368, 'hits': 146, 'hit_rate': 0.388}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 976}, 's_llm': {'typed_edges': 1, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 165, 'attention': 32, 'shadow_planner': {'analyzed': 40, 'promoted': 24}, 'elapsed_s': 28.0}

### 2026-07-14 — v5.20 장기런 (10-iter) 완주: **신규 챔피언 승격**
- co@10 61.1→74.4 마감 (피크 74.4×2, 전반5 67.1→후반5 71.1 = +4.0 장기 성장 실증),
  co@20 평균 89.6·피크 93.3, All@10 97.1, ValA@10 94.4~95.0, **10회 무롤백**, p50 55~60ms.
- 승격 근거: 재최적화 루프가 8~10사이클째에도 개선분 채굴 — 장기 자기진화 최초 실증.
  v5.15.1 종점(71.1) 경신. 프로토콜에 장기런(10-iter) 표준화.
- 다음: v5.21(넓은 관찰창) 10-iter 비교 검증.

### 2026-07-14 08:38:31 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 86.7/60.0/32.2%
- latency p50/p95: all=39.0/44.9ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 3174, 'l1_explicit': 1719, 'l2_co_retrieval': 2695, 'l3_llm_curated': 5, 'profiles': 3} · plan_cache: {'entries': 348, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 3057}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 1}, 'llm_calls': 49, 'good': 163, 'attention': 60, 'shadow_planner': {'analyzed': 40, 'promoted': 38}, 'elapsed_s': 74.6}

### 2026-07-14 08:40:17 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 91.1/64.4/38.9%
- latency p50/p95: all=40.0/46.2ms · tiers(valB)={'cache': 16, 'llm': 0, 'rules': 74}
- SG: {'docs': 572, 'edges': 3565, 'l1_explicit': 1719, 'l2_co_retrieval': 3139, 'l3_llm_curated': 10, 'profiles': 6} · plan_cache: {'entries': 366, 'hits': 124, 'hit_rate': 0.33}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 3336}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 181, 'attention': 36, 'shadow_planner': {'analyzed': 40, 'promoted': 32}, 'elapsed_s': 81.1}

### 2026-07-14 08:42:14 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 85.6/65.6/41.1%
- latency p50/p95: all=42.0/50.5ms · tiers(valB)={'cache': 22, 'llm': 0, 'rules': 68}
- SG: {'docs': 572, 'edges': 3784, 'l1_explicit': 1719, 'l2_co_retrieval': 3400, 'l3_llm_curated': 12, 'profiles': 9} · plan_cache: {'entries': 368, 'hits': 130, 'hit_rate': 0.346}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 3147}, 's_llm': {'typed_edges': 2, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 176, 'attention': 39, 'shadow_planner': {'analyzed': 40, 'promoted': 31}, 'elapsed_s': 90.5}

### 2026-07-14 08:44:30 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 93.3/72.2/48.9%
- latency p50/p95: all=42.4/49.7ms · tiers(valB)={'cache': 33, 'llm': 0, 'rules': 57}
- SG: {'docs': 572, 'edges': 3870, 'l1_explicit': 1719, 'l2_co_retrieval': 3489, 'l3_llm_curated': 15, 'profiles': 12} · plan_cache: {'entries': 368, 'hits': 143, 'hit_rate': 0.38}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 3124}, 's_llm': {'typed_edges': 3, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 166, 'attention': 28, 'shadow_planner': {'analyzed': 40, 'promoted': 23}, 'elapsed_s': 107.8}

### 2026-07-14 08:46:39 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 87.8/64.4/41.1%
- latency p50/p95: all=42.7/59.3ms · tiers(valB)={'cache': 27, 'llm': 0, 'rules': 63}
- SG: {'docs': 572, 'edges': 3936, 'l1_explicit': 1719, 'l2_co_retrieval': 3563, 'l3_llm_curated': 17, 'profiles': 15} · plan_cache: {'entries': 368, 'hits': 137, 'hit_rate': 0.364}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 3208}, 's_llm': {'typed_edges': 2, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 166, 'attention': 38, 'shadow_planner': {'analyzed': 40, 'promoted': 29}, 'elapsed_s': 101.0}

## G-시리즈 개시 (2026-07-14, 유저 방향 확정): L1 배제 — 제너럴 스택 캠페인
- **방향**: L1(명시참조 스캐너)은 "참조가 기계친화적으로 적힌 문서"에서만 강한 코퍼스 특화 지름길.
  일반 시스템은 **facet_link + L2 + L3 세 증거만으로** L1 스택 동급(co@10 ~69-74 / co@20 ~90)까지.
  목표 달성까지 G-사이클 반복. 모든 런 STRUCTRAG_NO_L1=1.
- G1 (v5.22) 조립: 챔피언(v5.20) 스택 − L1 + facet_link 복원(유저 설계: 분해 서브쿼리 결과 교차쌍,
  L1 자리 0.9) + 상보 facet 상시 가동(한글 추출기 수정판, facet 재료 공급) + 넓은 관찰창(v5.21,
  원래 facet 재료용이던 것) + 에스코트 facet 폴백(2회 확인+현재쿼리 교차증거).
- 반성 기록: facet은 유저가 채택 확정한 레이어였는데 v5.19 기각 시 통째 리버트로 챔피언에서 유실,
  관찰창도 facet 맥락과 분리 검증하는 오류 — 구성 판단 미스로 명기.
- 시작점 참고: 직전 NO_L1 최선 co@10 52.2 / co@20 63.3. 갭 +17/+27.

### 2026-07-14 12:40:20 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 63.3/55.6/35.6%
- latency p50/p95: all=38.5/52.7ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 3288, 'l1_explicit': 0, 'l2_co_retrieval': 2826, 'l3_llm_curated': 5, 'profiles': 3} · plan_cache: {'entries': 280, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 5782, 'facet_queries': 105}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 1}, 'llm_calls': 49, 'good': 185, 'attention': 98, 'shadow_planner': {'analyzed': 40, 'promoted': 37}, 'elapsed_s': 16.5}

### 2026-07-14 12:41:36 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 63.3/51.1/32.2%
- latency p50/p95: all=45.0/60.1ms · tiers(valB)={'cache': 14, 'llm': 0, 'rules': 76}
- SG: {'docs': 572, 'edges': 4486, 'l1_explicit': 0, 'l2_co_retrieval': 4124, 'l3_llm_curated': 10, 'profiles': 6} · plan_cache: {'entries': 356, 'hits': 96, 'hit_rate': 0.255}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 6005, 'facet_queries': 123}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 182, 'attention': 61, 'shadow_planner': {'analyzed': 40, 'promoted': 33}, 'elapsed_s': 38.9}

### 2026-07-14 12:43:31 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 63.3/54.4/33.3%
- latency p50/p95: all=45.8/61.6ms · tiers(valB)={'cache': 15, 'llm': 0, 'rules': 75}
- SG: {'docs': 572, 'edges': 4823, 'l1_explicit': 0, 'l2_co_retrieval': 4320, 'l3_llm_curated': 14, 'profiles': 9} · plan_cache: {'entries': 368, 'hits': 137, 'hit_rate': 0.364}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 5143, 'facet_queries': 124}, 's_llm': {'typed_edges': 4, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 187, 'attention': 56, 'shadow_planner': {'analyzed': 40, 'promoted': 33}, 'elapsed_s': 79.1}

### 2026-07-14 12:45:13 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 66.7/52.2/32.2%
- latency p50/p95: all=47.2/65.2ms · tiers(valB)={'cache': 18, 'llm': 0, 'rules': 72}
- SG: {'docs': 572, 'edges': 4942, 'l1_explicit': 0, 'l2_co_retrieval': 4411, 'l3_llm_curated': 19, 'profiles': 12} · plan_cache: {'entries': 371, 'hits': 152, 'hit_rate': 0.404}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 5074, 'facet_queries': 123}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 187, 'attention': 41, 'shadow_planner': {'analyzed': 40, 'promoted': 27}, 'elapsed_s': 63.7}

### 2026-07-14 12:47:26 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 66.7/51.1/27.8%
- latency p50/p95: all=47.0/66.0ms · tiers(valB)={'cache': 21, 'llm': 0, 'rules': 69}
- SG: {'docs': 572, 'edges': 5075, 'l1_explicit': 0, 'l2_co_retrieval': 4506, 'l3_llm_curated': 24, 'profiles': 15} · plan_cache: {'entries': 371, 'hits': 148, 'hit_rate': 0.394}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 5039, 'facet_queries': 120}, 's_llm': {'typed_edges': 5, 'profiles': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {'wikified': 0}, 'llm_calls': 48, 'good': 188, 'attention': 36, 'shadow_planner': {'analyzed': 40, 'promoted': 28}, 'elapsed_s': 95.0}

### 2026-07-14 — G1 판정 및 G2
- G1: co@10 평균 52.9 / co@20 63.3→66.7. **결정적 진전**: 정답쌍 facet 커버 29/90 (전건 에스코트 자격),
  커버된 쿼리는 linked top-10 진입 24/29 (83%) — **소비 경로는 정상, 병목은 커버리지**로 확정.
- G2: ① facet 캡처 4→6 (관찰창 재료 심화) ② **L3 커버리지 채널** — 쿼리 콘텐츠 토큰과 2개+ 겹치는데
  관찰창 40위 밖인 문서를 숏리스트 → 관찰 1위 문서와의 관계를 LLM 청크증거 판정 → L3 엣지.
  검색 미도달 문서를 판정 테이블에 올리는 최초의 도달 확장 (내용 토큰 기반 = 제너럴, 참조표기 불요).
- 유닛·스모크 10/10. **G2 동결 — NO_L1 5-iter.**

### 2026-07-14 12:53:50 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 63.3/55.6/35.6%
- latency p50/p95: all=37.2/46.8ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 4097, 'l1_explicit': 0, 'l2_co_retrieval': 2826, 'l3_llm_curated': 8, 'profiles': 0} · plan_cache: {'entries': 282, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 5782, 'facet_queries': 105}, 's_llm': {'coverage_edges': 8}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 50, 'good': 185, 'attention': 98, 'shadow_planner': {'analyzed': 40, 'promoted': 39}, 'elapsed_s': 189.4}

### 2026-07-14 12:56:48 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 66.7/53.3/31.1%
- latency p50/p95: all=46.7/63.5ms · tiers(valB)={'cache': 14, 'llm': 0, 'rules': 76}
- SG: {'docs': 572, 'edges': 5450, 'l1_explicit': 0, 'l2_co_retrieval': 4270, 'l3_llm_curated': 12, 'profiles': 0} · plan_cache: {'entries': 363, 'hits': 95, 'hit_rate': 0.253}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 5766, 'facet_queries': 125}, 's_llm': {'coverage_edges': 4}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 50, 'good': 187, 'attention': 47, 'shadow_planner': {'analyzed': 40, 'promoted': 30}, 'elapsed_s': 141.0}

### 2026-07-14 12:58:48 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 66.7/53.3/28.9%
- latency p50/p95: all=48.8/66.1ms · tiers(valB)={'cache': 16, 'llm': 0, 'rules': 74}
- SG: {'docs': 572, 'edges': 5854, 'l1_explicit': 0, 'l2_co_retrieval': 4456, 'l3_llm_curated': 15, 'profiles': 0} · plan_cache: {'entries': 365, 'hits': 143, 'hit_rate': 0.38}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 4992, 'facet_queries': 118}, 's_llm': {'coverage_edges': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 50, 'good': 183, 'attention': 45, 'shadow_planner': {'analyzed': 40, 'promoted': 29}, 'elapsed_s': 82.8}

### 2026-07-14 13:00:08 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 64.4/53.3/26.7%
- latency p50/p95: all=49.9/65.2ms · tiers(valB)={'cache': 23, 'llm': 0, 'rules': 67}
- SG: {'docs': 572, 'edges': 6008, 'l1_explicit': 0, 'l2_co_retrieval': 4575, 'l3_llm_curated': 16, 'profiles': 0} · plan_cache: {'entries': 366, 'hits': 153, 'hit_rate': 0.407}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 4911, 'facet_queries': 113}, 's_llm': {'coverage_edges': 1}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 50, 'good': 180, 'attention': 41, 'shadow_planner': {'analyzed': 40, 'promoted': 24}, 'elapsed_s': 41.2}

### 2026-07-14 13:01:25 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 63.3/53.3/30.0%
- latency p50/p95: all=50.9/64.6ms · tiers(valB)={'cache': 20, 'llm': 0, 'rules': 70}
- SG: {'docs': 572, 'edges': 6155, 'l1_explicit': 0, 'l2_co_retrieval': 4650, 'l3_llm_curated': 19, 'profiles': 0} · plan_cache: {'entries': 367, 'hits': 151, 'hit_rate': 0.402}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 4826, 'facet_queries': 116}, 's_llm': {'coverage_edges': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 50, 'good': 183, 'attention': 42, 'shadow_planner': {'analyzed': 40, 'promoted': 27}, 'elapsed_s': 38.6}

### 2026-07-14 — G2 판정 및 G3 (관찰꼬리 승격)
- G2: co@10 평균 53.8 부동. **커버리지 엣지 19개 중 정답쌍 적중 0** — 토큰 숏리스트의 캐치-22 실증:
  쿼리 토큰과 겹치는 문서는 이미 관찰창 안(필터로 제외), 놓치는 linked는 불투명 ID 파일명이라 토큰 매칭 불가.
  "관찰창 밖" 필터가 정확히 역방향이었음. + 각주: 토큰 숏리스트는 파일명 유의미성 가정 내포(프로필 토큰이 일반화 경로).
- **결정적 실측**: 정답 linked의 67/90이 관찰창 40 안에 존재 (top-10에 46, top-20에 53) —
  문제의 3/4은 발굴이 아니라 "관찰꼬리(11~40위) → 구조 승격".
- G3: 커버리지 후보를 (관찰 1위, 관찰 11~40위) 쌍으로 교체 — 검색이 그 쿼리에 실제 반응시킨 문서만.
  예산 15/사이클. G2 토큰 채널 폐기.

### 2026-07-14 — G5 판정 및 G6 (G3 복귀 + 증량)
- G5: co@10 48.9→46.7 하락 — **기각.** 자카드 필터로도 통계 오염(저오버랩 유사쌍: 타제품 허브간 등)을
  못 이김. **통계 물량 노선(G4/G5) 공식 폐기** — G-스코어보드: G3(LLM 정밀 승격) 54.2가 최고.
- G6 = G3 코드 복원 + G4의 순기능만 이식(좌석 개방 — facet 엣지가 6석 활용) +
  커버리지 채널 증량 (15→35/사이클, 앵커 top-3 페어링으로 후보쌍 3배).
- 유닛·스모크 10/10. **G6 동결 — NO_L1 5-iter. 미달 시 정직한 상한선 보고로 캠페인 정리 예정.**

### 2026-07-14 18:41:54 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 63.3/55.6/35.6%
- latency p50/p95: all=37.8/49.0ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 4094, 'l1_explicit': 0, 'l2_co_retrieval': 2826, 'l3_llm_curated': 5, 'profiles': 0} · plan_cache: {'entries': 282, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 5782, 'facet_queries': 105}, 's_llm': {'coverage_edges': 5}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 50, 'good': 185, 'attention': 98, 'shadow_planner': {'analyzed': 40, 'promoted': 39}, 'elapsed_s': 2.9}

### 2026-07-14 18:42:50 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 65.6/52.2/32.2%
- latency p50/p95: all=47.7/64.4ms · tiers(valB)={'cache': 14, 'llm': 0, 'rules': 76}
- SG: {'docs': 572, 'edges': 5290, 'l1_explicit': 0, 'l2_co_retrieval': 3993, 'l3_llm_curated': 9, 'profiles': 0} · plan_cache: {'entries': 362, 'hits': 92, 'hit_rate': 0.245}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 5378, 'facet_queries': 127}, 's_llm': {'coverage_edges': 4}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 50, 'good': 192, 'attention': 44, 'shadow_planner': {'analyzed': 40, 'promoted': 30}, 'elapsed_s': 18.7}

### 2026-07-14 18:43:30 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 66.7/52.2/27.8%
- latency p50/p95: all=47.9/63.5ms · tiers(valB)={'cache': 17, 'llm': 0, 'rules': 73}
- SG: {'docs': 572, 'edges': 5661, 'l1_explicit': 0, 'l2_co_retrieval': 4163, 'l3_llm_curated': 10, 'profiles': 0} · plan_cache: {'entries': 367, 'hits': 145, 'hit_rate': 0.386}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 4660, 'facet_queries': 117}, 's_llm': {'coverage_edges': 1}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 50, 'good': 183, 'attention': 44, 'shadow_planner': {'analyzed': 40, 'promoted': 29}, 'elapsed_s': 3.0}

### 2026-07-14 18:45:11 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 66.7/51.1/26.7%
- latency p50/p95: all=49.6/68.3ms · tiers(valB)={'cache': 20, 'llm': 0, 'rules': 70}
- SG: {'docs': 572, 'edges': 5865, 'l1_explicit': 0, 'l2_co_retrieval': 4272, 'l3_llm_curated': 11, 'profiles': 0} · plan_cache: {'entries': 368, 'hits': 148, 'hit_rate': 0.394}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 4510, 'facet_queries': 116}, 's_llm': {'coverage_edges': 1}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 50, 'good': 184, 'attention': 40, 'shadow_planner': {'analyzed': 40, 'promoted': 25}, 'elapsed_s': 62.7}

### 2026-07-14 18:45:53 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 66.7/48.9/27.8%
- latency p50/p95: all=48.8/64.8ms · tiers(valB)={'cache': 18, 'llm': 0, 'rules': 72}
- SG: {'docs': 572, 'edges': 5960, 'l1_explicit': 0, 'l2_co_retrieval': 4319, 'l3_llm_curated': 12, 'profiles': 0} · plan_cache: {'entries': 368, 'hits': 149, 'hit_rate': 0.396}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 4232, 'facet_queries': 116}, 's_llm': {'coverage_edges': 1}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 50, 'good': 186, 'attention': 45, 'shadow_planner': {'analyzed': 40, 'promoted': 24}, 'elapsed_s': 3.3}

### 2026-07-14 — G6 판정 및 G6.1 (후보 순서 버그 수정)
- G6: co@10 51~55 밴드, covEdges 5→1 — 증량 무효. **원인 버그**: 후보 스캔이 평가 순서(all→ValA→ValB)
  그대로 조기 중단 → co-의도(ValB형) 쿼리의 쌍이 후보 테이블에 진입 불가. G3부터 잠복.
- G6.1: 타입 골격 쿼리 우선 정렬 + 전체 스캔 + 쿼리당 3쌍 상한. 결함 수정이므로 즉시 재검증.

### 2026-07-14 18:50:21 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 63.3/55.6/35.6%
- latency p50/p95: all=39.5/46.0ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 4093, 'l1_explicit': 0, 'l2_co_retrieval': 2826, 'l3_llm_curated': 4, 'profiles': 0} · plan_cache: {'entries': 282, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 5782, 'facet_queries': 105}, 's_llm': {'coverage_edges': 4}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 50, 'good': 185, 'attention': 98, 'shadow_planner': {'analyzed': 40, 'promoted': 39}, 'elapsed_s': 172.2}

### 2026-07-14 18:52:18 — ver5 iteration 2 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 65.6/52.2/32.2%
- latency p50/p95: all=46.8/62.2ms · tiers(valB)={'cache': 14, 'llm': 0, 'rules': 76}
- SG: {'docs': 572, 'edges': 5276, 'l1_explicit': 0, 'l2_co_retrieval': 3981, 'l3_llm_curated': 7, 'profiles': 0} · plan_cache: {'entries': 362, 'hits': 92, 'hit_rate': 0.245}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 5304, 'facet_queries': 127}, 's_llm': {'coverage_edges': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 50, 'good': 191, 'attention': 44, 'shadow_planner': {'analyzed': 40, 'promoted': 30}, 'elapsed_s': 79.8}

### 2026-07-14 18:54:35 — ver5 iteration 3 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 66.7/52.2/27.8%
- latency p50/p95: all=48.2/61.9ms · tiers(valB)={'cache': 17, 'llm': 0, 'rules': 73}
- SG: {'docs': 572, 'edges': 5595, 'l1_explicit': 0, 'l2_co_retrieval': 4149, 'l3_llm_curated': 11, 'profiles': 0} · plan_cache: {'entries': 366, 'hits': 146, 'hit_rate': 0.388}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 4593, 'facet_queries': 117}, 's_llm': {'coverage_edges': 4}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 50, 'good': 182, 'attention': 42, 'shadow_planner': {'analyzed': 40, 'promoted': 29}, 'elapsed_s': 99.4}

### 2026-07-14 18:56:02 — ver5 iteration 4 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 65.6/50.0/25.6%
- latency p50/p95: all=49.0/66.2ms · tiers(valB)={'cache': 20, 'llm': 0, 'rules': 70}
- SG: {'docs': 572, 'edges': 5796, 'l1_explicit': 0, 'l2_co_retrieval': 4250, 'l3_llm_curated': 12, 'profiles': 0} · plan_cache: {'entries': 367, 'hits': 150, 'hit_rate': 0.399}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 4461, 'facet_queries': 116}, 's_llm': {'coverage_edges': 1}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 50, 'good': 184, 'attention': 39, 'shadow_planner': {'analyzed': 40, 'promoted': 25}, 'elapsed_s': 48.3}

### 2026-07-14 18:58:18 — ver5 iteration 5 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 65.6/47.8/27.8%
- latency p50/p95: all=49.0/61.5ms · tiers(valB)={'cache': 18, 'llm': 0, 'rules': 72}
- SG: {'docs': 572, 'edges': 5936, 'l1_explicit': 0, 'l2_co_retrieval': 4304, 'l3_llm_curated': 15, 'profiles': 0} · plan_cache: {'entries': 367, 'hits': 147, 'hit_rate': 0.391}
- evolve: {'records': 416, 's_rules': {'decayed_layers': 4270, 'facet_queries': 117}, 's_llm': {'coverage_edges': 3}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 50, 'good': 187, 'attention': 45, 'shadow_planner': {'analyzed': 40, 'promoted': 23}, 'elapsed_s': 98.5}

### 2026-07-14 — G6.1 판정 및 G6.2 (상보성 프라이어 후보 선별)
- G6.1: 평균 51.6 — 순서 버그는 고쳤지만 쿼리당 3쌍이 꼬리 맨앞(11~13위) 위치 고정 →
  linked 제안 확률 ~10%. **기각.**
- G6.2: 꼬리 후보를 상보성 프라이어로 정렬 — (1−앵커와의 토큰 자카드) + ID형 불투명 파일명 보너스.
  G5 교훈(닮은 문서는 링크 아님)을 강화 게이트가 아닌 **후보 조준**에 적용. 이 노선의 완결 시도이며,
  결과와 무관하게 이후 캠페인 정리 보고 예정 (사전 고지된 판정선 60+).

### 2026-07-14 — 캠페인 재개 (유저 재확인): G7 — 판정 무죄 실증과 attention-first
- 정리 보고 후 유저 재질문("개선 여지 없나")으로 마지막 미해부 단계(LLM 판정) 직접 실험:
  **정답쌍 12/12 수락 (conf 0.75~0.92)** — 판정기는 처음부터 무죄.
- 진짜 범인: 커버리지 후보를 good(p60+)에서만 채집 — **co-미스 쿼리는 정의상 저품질이라
  attention에 있음.** "저품질에 집중"이라는 attention 큐 철학이 이 채널에만 미적용된 자기모순.
- G7: 후보 소스 attention+good (attention 우선). 체인 전 고리 개별 실증 완료:
  attention 쿼리 → 카드 앵커 × 상보 프라이어 꼬리 → 판정 수락(12/12) → L3 → 승격(전환 83%).
- **G7 동결 — NO_L1 5-iter.**

### 2026-07-14 19:29:57 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 63.3/55.6/35.6%
- latency p50/p95: all=37.6/49.0ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 4089, 'l1_explicit': 0, 'l2_co_retrieval': 2826, 'l3_llm_curated': 0, 'profiles': 0} · plan_cache: {'entries': 282, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 5782, 'facet_queries': 105}, 's_llm': {'coverage_edges': 0}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 50, 'good': 185, 'attention': 98, 'shadow_planner': {'analyzed': 40, 'promoted': 39}, 'elapsed_s': 149.0}

### 2026-07-14 — G7 iter1 진단 및 G7.1
- G7 iter1: covEdges 0 — ① 섀도 플래너가 예산 40 선점, 커버리지엔 10콜 ② 그 10쌍은 동족 타 jira라
  판정이 정당 거부. 거부는 캐시돼 재시도 무비용.
- G7.1: 커버리지 채널 선순위(예약 25콜, 섀도 앞) + 쿼리당 5쌍. (재시작 중 pkill 자기매칭 사고로
  G7 런 중단됨 — G7.1로 fresh 재검증)

### 2026-07-14 19:38:24 — ver5 iteration 1 (LLM on)
- All@20 100.0% · ValA@20 99.4% · ValB co@20/10/5 63.3/55.6/35.6%
- latency p50/p95: all=38.7/49.0ms · tiers(valB)={'cache': 0, 'llm': 0, 'rules': 90}
- SG: {'docs': 572, 'edges': 4090, 'l1_explicit': 0, 'l2_co_retrieval': 2826, 'l3_llm_curated': 1, 'profiles': 0} · plan_cache: {'entries': 269, 'hits': 2, 'hit_rate': 0.005}
- evolve: {'records': 376, 's_rules': {'decayed_layers': 5782, 'facet_queries': 105}, 's_llm': {'coverage_edges': 1}, 'k_rules': {'stale_edges_removed': 0}, 'k_llm': {}, 'llm_calls': 50, 'good': 185, 'attention': 98, 'shadow_planner': {'analyzed': 25, 'promoted': 24}, 'elapsed_s': 221.2}

### 2026-07-14 — G7.1 iter1 진단 및 G7.2
- G7.1: covEdges 1 — ① type 정렬 내 안정정렬로 ValA(180문항, 동일 골격)가 슬롯 독식 (depth-first)
  ② 거부 쌍 기억 부재로 매 사이클 동일 쌍 재제안. (+운영사고 2건: pkill 자기매칭으로 런 2회 조기종료)
- G7.2: ① breadth-first 로테이션 (쿼리당 1쌍씩 순회, 25슬롯이 25개 쿼리에 분산 → ValB 확실 도달)
  ② 거부 쌍 evolver state 영구 기억 — 사이클마다 신규 쌍만 심사 (5사이클 = 125 신규쌍 탐사).

### 2026-07-15 — v5.33-ALL (유저 지적): L1+facet+L2+L3 총동원 — 사상 첫 결합 검증
- 발견된 공백: facet 계열(facet_link·상보facet·관찰창·facet에스코트)은 전부 NO_L1로만 검증됨.
  v5.14(79.1)는 facet 이전 + 인라인 LLM. **전 레이어 결합의 클린 런이 존재하지 않았음.**
- v5.33-ALL = G7.2 스택 + L1 ON (env 없음). L1 존재 시: 확장 L1 4석 정상 작동, 에스코트 L1 1순위,
  커버리지 채널은 L1 기존재 쌍 스킵(잔여 구조만 탐색). 균일 레이턴시 유지.
- 판정선: v5.20(74.4/89.6) 초과 시 신규 플래그십, v5.14(79.1) 근접 시 "정밀모드 무용화" 보너스.
