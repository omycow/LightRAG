# StructRAG 전략별 문헌 근거 (research notes)

그라운드룰 4: 모든 전략은 근거가 있어야 한다. 각 컴포넌트가 어떤 연구에 기반하는지 기록한다.
(웹 검증일: 2026-07-09)

## 1. 구조그래프 기반 스코프/확장 (structure_graph.py, retriever.py EXPAND)

- **Asai et al., ICLR 2020, "Learning to Retrieve Reasoning Paths over Wikipedia Graph for Question Answering"** — 문단을 노드, 하이퍼링크를 엣지로 한 그래프 위에서 추론 경로를 검색해 HotpotQA에서 당시 SOTA 대비 +14pt. 문서 간 명시적 링크 그래프가 멀티홉(co-retrieval) 검색의 핵심 구조라는 직접 근거.
  https://arxiv.org/abs/1911.10470
- 우리 적용: L1(명시적 참조)·L2(공동검색 통계)·L3(LLM 큐레이션) 3층 엣지의 doc-level 그래프에서 시드 → 가중 1-hop 확장. 상위 히트 문서의 이웃 문서 청크를 보충하는 EXPAND 단계가 co-retrieval을 직접 공략.

## 2. 전략 라우팅 (analyzer.py)

- **Jeong et al., NAACL 2024, "Adaptive-RAG: Learning to Adapt Retrieval-Augmented LLMs through Question Complexity"** — 쿼리 복잡도 분류기로 no-retrieval/single-step/multi-step을 라우팅하면 항상-비싼 베이스라인과 동급 성능을 훨씬 낮은 비용으로 달성. "쿼리 성격에 따라 전략을 고르는 라우터"의 유효성 근거.
  https://aclanthology.org/2024.naacl-long.389/
- **BEIR (Thakur et al., 2021) 계열 관찰** — dense 리트리버는 zero-shot/도메인 밖에서 BM25를 이기지 못하는 경우가 빈번. BM25는 희귀 텀·정확 식별자(ID, 파일명, 스펙 번호)에 강하고 dense는 패러프레이즈에 강함 → 상호보완적 리콜.
  → 룰 검증 레이어: ID 토큰/파일명 포함 쿼리는 bm25/hybrid 가중, 자연어 개념 쿼리는 vector/mix 가중.
- **Li et al., WWW 2010, "A Contextual-Bandit Approach to Personalized News Article Recommendation" (LinUCB)** — 탐험/활용 트레이드오프의 표준 해법. StrategyMemory의 ε-greedy(클러스터별 모드 quality EMA argmax + ε 탐험)의 근거.
- **RRF: Cormack et al., SIGIR 2009, "Reciprocal Rank Fusion outperforms Condorcet and individual rank learning methods"** — 서로 다른 랭커 융합의 사실상 표준. 서브쿼리 결과 융합 및 스코프 soft-boost 구현 기반.

## 3. 룰기반 퀄리티 점수 (quality.py)

Query Performance Prediction(QPP)의 post-retrieval 예측자 — LLM 없이 검색 결과 품질을 추정하는 검증된 IR 기법:

- **NQC — Shtok et al., "Predicting Query Performance by Query-Drift Estimation" (TOIS 2012); Normalized Query Commitment Revisited (SIGIR 2019)** — 상위 k 결과 스코어의 표준편차(코퍼스 스코어로 정규화). 상위 결과 스코어가 고르게 높으면 좋은 검색, 분산이 크면 노이즈.
- **WIG — Zhou & Croft, SIGIR 2007, "Query Performance Prediction in Web Search Environments"** — 상위 결과 스코어와 코퍼스 평균 스코어의 차이.
- 융합 합의도(BM25 상위 ∩ vector 상위 겹침): 서로 독립적인 두 리트리버가 합의하면 신뢰도가 높다는 rank-agreement 계열 QPP 아이디어.
- 우리 조합: score 분포(top1/mean@k/NQC형 분산) + 융합 합의도 + 쿼리 텀 커버리지 + 스코프 정합 + 문서 집중도. 고정 가중치(문서화)로 0~1 산출.
- **CRAG — Yan et al., 2024, "Corrective Retrieval Augmented Generation"** — 경량 retrieval evaluator가 결과 품질을 판정해 후속 교정 행동을 트리거하는 패턴. 우리는 evaluator를 LLM이 아닌 QPP 룰로 대체하고, 저품질 쿼리를 attention queue로 보내 백그라운드 교정(rewrite 마이닝, 구조 보강)을 트리거.

## 4. 쿼리 리라이트/분해 (analyzer.py Tier 1, evolver ⑥)

- **Ma et al., 2023, "Query Rewriting for Retrieval-Augmented Large Language Models" (Rewrite-Retrieve-Read)** — 리트리버에 맞춘 쿼리 리라이트가 downstream 성능 개선.
- **Wang et al., 2023, "Query2Doc"** — 유사 문서형 확장 생성이 BM25/dense 모두 개선. 단, 엔티티/수치 정밀 쿼리에는 역효과 가능(→ ID 쿼리는 리라이트 최소화, 룰 검증 레이어가 방어).
- 분해: multi-intent 쿼리를 서브쿼리로 나눠 각각 최적 전략으로 검색 후 융합 — Adaptive-RAG의 multi-step 대응을 병렬 분해로 단순화한 형태.

## 5. 진화/자기정화 (evolver.py)

- L2 강화/감쇠: 사용 기반 강화 + 미사용 감쇠는 그래프 메모리의 표준 위생 기법 (evidence-based reinforcement/decay). 저품질 쿼리 증거 배제(quality gate)는 CRAG의 "잘못된 검색 결과로부터 배우지 않기" 원칙의 적용.
- KG 위생(Track K): 중복 엔티티 병합·설명 통합은 KG 캐노니컬라이제이션(entity resolution/canonicalization) 문헌의 표준 절차. 설명 위키화는 임베딩 대상 텍스트 품질을 올려 entity VDB 검색을 직접 개선.
- 문서 프로필(wiki식 요약 카드): RAPTOR (Sarthi et al., 2024) 등 요약 노드가 검색 라우팅을 개선한다는 계열의 적용 — 단 우리는 검색 인덱스가 아닌 SG 노드 속성으로 저장해 스코프 라우팅에만 사용 (KG/VDB 오염 방지).

## Sources (웹 검증)

- [Adaptive-RAG — ACL Anthology](https://aclanthology.org/2024.naacl-long.389/)
- [Learning to Retrieve Reasoning Paths — arXiv 1911.10470](https://arxiv.org/abs/1911.10470)
- [Normalized Query Commitment Revisited — SIGIR 2019](https://dl.acm.org/doi/10.1145/3331184.3331334)
- [QPP for Neural IR — arXiv 2302.09947](https://arxiv.org/pdf/2302.09947)
- [Hybrid Search: BM25 + Dense 상호보완 리콜 정리](https://mbrenndoerfer.com/writing/hybrid-search-bm25-dense-retrieval-fusion)
- [BM25 wins on rare terms/exact identifiers](https://tianpan.co/blog/2026-04-12-hybrid-search-production-bm25-dense-embeddings)
