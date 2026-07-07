# Evolving LightRAG

LightRAG 위에 **쿼리 로그 기반 그래프 자동 진화 에이전트**를 구축한 프로젝트입니다.

Karpathy의 [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)(2026.04) 패턴에서 Raw / Wiki / Schema 3계층 구조와 지식 라이프사이클을 참고하여, LightRAG의 그래프 인프라 위에서 동일한 역할을 수행하는 구조를 설계했습니다.

세부 전략, 지표, 이론적 배경은 이 문서에서 다루지 않습니다 — **[ARCHITECTURE.md](ARCHITECTURE.md)** 에 정리되어 있습니다. 이 문서는 에이전트가 어떻게 동작하는지, 그리고 진화가 어떤 큰 축으로 이루어지는지만 다룹니다.

---

## 왜 두 개의 경로로 나뉘는가

LightRAG(vanilla)의 그래프는 문서 삽입 시점에 고정됩니다. 쿼리를 아무리 많이 던져도 그래프는 그대로입니다. 반면 매 쿼리마다 그래프를 개선하려고 사용자를 기다리게 하면 응답이 느려집니다.

Evolving LightRAG는 이 둘을 분리합니다: **사용자는 즉시 답을 받고, 그래프는 그 뒤에서 조용히 좋아집니다.**

```
사용자 질문
     │
     ▼
┌─────────────┐
│  1. ANALYZE │  쿼리를 검색에 최적화된 형태로 재작성하고,
│  (쿼리 개선) │  하나의 질문이 여러 정보 요구를 담고 있으면 서브쿼리로 분해
└─────┬───────┘
      │
      ├─────────────────────────────┐  여기서 두 경로로 갈라짐
      ▼                             ▼
┌───────────────┐        ┌───────────────────────────────┐
│ RESPOND (전경) │        │  EVOLVE 파이프라인 (백그라운드) │
│ 최적화된 쿼리로 │        │  ┌──────────┐                  │
│ 바로 답변 생성  │        │  │ RETRIEVE │ 서브쿼리별 리트리브
│      ↓        │        │  └────┬─────┘                  │
│  사용자에게    │        │       ▼                        │
│  즉시 리턴     │        │  ┌──────────┐                  │
└───────────────┘        │  │ EVALUATE │ 품질 점수 (0 LLM)  │
                          │  └────┬─────┘                  │
                          │       ▼                        │
                          │  ┌──────────────┐               │
                          │  │ EVOLVE(light)│ 매 쿼리마다    │
                          │  └────┬─────────┘               │
                          │       ▼                        │
                          │  N번째 쿼리마다 ──────────┐      │
                          │       │                   ▼      │
                          │       │        ┌────────────────────┐
                          │       │        │ EVOLVE(batch) +    │
                          │       │        │ STRUCTURAL          │
                          │       │        │ (종합 분석)          │
                          │       │        └────────────────────┘
                          │       ▼                        │
                          │   그래프에 반영 (다음 쿼리부터)   │
                          └───────────────────────────────┘
```

- **RESPOND (전경, foreground)**: ANALYZE가 만든 최적화 쿼리를 그대로 LightRAG에 보내 답을 받고, 그 자리에서 사용자에게 리턴합니다. 그래프 개선을 기다리지 않습니다.
- **EVOLVE 파이프라인 (백그라운드)**: RESPOND가 사용자에게 답을 리턴하는 것과 동시에 큐에 올라가, 별도 워커가 순서대로 처리합니다. 여기서 만들어진 그래프 변경은 **이번 쿼리가 아니라 다음 쿼리부터** 검색 결과에 반영됩니다.
- 백그라운드 파이프라인은 두 단계로 나뉩니다:
  - **매 쿼리마다** (tier 1, 가벼움): 이번 쿼리에서 검색된 것만 보고 싼 값에 그래프를 조금씩 개선
  - **N번째 쿼리마다** (tier 2, `batch_evolve_interval`, 기본 50): 누적된 쿼리 로그 전체를 다시 훑어 더 폭넓게 개선하고, 그래프 전체를 스캔해야 하는 무거운 점검(근거 상실 관계 제거, 모순 통합)과 **구조 관계 마이닝**을 함께 수행

구현은 [`wikigraph/agent.py`](wikigraph/agent.py)의 `WikiGraphAgent.query()`를 보면 됩니다. LangGraph `StateGraph`는 이 중 진짜로 여러 단계가 이어지는 EVOLVE 파이프라인(`RETRIEVE → EVALUATE → EVOLVE(light) → EVOLVE(batch) → STRUCTURAL`)에만 쓰입니다. ANALYZE와 RESPOND는 단일 호출이라 직접 함수로 호출합니다.

---

## 진화의 네 기둥

그래프를 개선하는 방법은 결국 네 가지 큰 축으로 나뉩니다. 각 항목의 세부 전략과 임계값은 [ARCHITECTURE.md](ARCHITECTURE.md)에 있습니다.

### 1. 쿼리 개선 (Query Improvement)

검색으로 보내기 전에 질문 자체를 좋게 만듭니다. 모호한 표현을 정리하고, 하나의 질문이 여러 정보 요구를 담고 있으면 독립적으로 검색 가능한 서브쿼리로 쪼갭니다. `ANALYZE` 단계가 담당하며, 그 결과(`optimized_query`, `sub_queries`)가 RESPOND 경로와 EVOLVE 경로 양쪽에 쓰입니다.

### 2. 지식그래프 개선 (Knowledge Graph Improvement)

쿼리 로그와 원본 청크를 근거로 그래프에 엔티티/릴레이션을 추가·보강·정리합니다. 자주 함께 검색되는데 연결이 없는 엔티티를 잇고, 추출이 누락된 곳을 원본 청크 근거로 채우고, 반복되는 다중 홉 경로를 단축하고, 문서가 사라지면 근거를 잃은 관계를 지우고, 여러 출처의 모순된 설명을 하나로 통합합니다. tier 1(매 쿼리)과 tier 2(배치)로 나뉘어 실행됩니다.

### 3. 구조 관계 개선 (Structural Relation Improvement)

**LightRAG 그래프의 근본적 한계**입니다: 엔티티/릴레이션 추출은 텍스트 안의 *의미적* 관계만 잡아내고, 문서와 문서, 청크와 청크 사이의 *구조적* 관계는 전혀 드러내지 못합니다 — 같은 문서의 인접한 두 청크, 자주 같이 답변에 쓰이는 두 문서 사이에 LLM이 우연히 관계를 추출하지 않는 한 아무 연결도 없습니다.

STRUCTURAL은 LLM 추출이 아니라 LightRAG가 이미 가진 메타데이터(`full_doc_id`, `chunk_order_index`)와 누적된 쿼리 로그로 이 관계를 채웁니다: 문서를 그래프 노드로 명시하고, 문서가 포함하는 엔티티를 연결하고, 엔티티를 공유하는 문서끼리 연결하고, 같은 문서의 인접 청크에서 나온 엔티티끼리 연결합니다. tier 2(배치)와 같은 주기로 실행되어, 종합 분석을 할 때마다 그래프가 구조적으로도 더 촘촘해집니다.

### 4. 린팅 (Linting)

그래프 알고리즘으로 구조적 이상을 탐지합니다 (대부분 0 토큰). 고아 노드, 허브 과부하, 중복 엔티티, 방치된 엔티티를 리포트하고, EVOLVE가 추론으로 만든 엣지 중 가중치가 낮고 한 번도 검색에 쓰이지 않은 것만 자동으로 정리합니다. 구조 관계 엣지(문서-엔티티, 문서-문서, 청크 인접)는 추론이 아니라 실제 코퍼스 사실이므로 자동 정리 대상에서 제외됩니다.

---

## 시스템 비교

| | LightRAG (vanilla) | Evolving LightRAG |
|---|---|---|
| 지식 업데이트 | 새 문서 삽입 시에만 | 쿼리마다 백그라운드로, N쿼리마다 종합적으로 |
| 문서/청크 간 구조 | 없음 | STRUCTURAL이 문서-문서, 청크-청크 관계를 명시적으로 생성 |
| 사용자 응답 지연 | 검색 1회 | 검색 1회 (그래프 개선은 응답과 분리되어 다음 쿼리부터 반영) |
| 구조 점검 | 없음 | 그래프 알고리즘 (거의 0 토큰) |

---

## 사용법

```python
from wikigraph.agent import WikiGraphAgent
from wikigraph.config import WikiGraphConfig

rag = LightRAG(working_dir="./my_kb", llm_model_func=llm_func, embedding_func=embed_func)
await rag.initialize_storages()

agent = WikiGraphAgent(rag, WikiGraphConfig(batch_evolve_interval=50), llm_func=llm_func)

# 문서 삽입 (지식그래프 개선의 원천)
await agent.ingest(["문서 내용..."])

# 질문: 즉시 답을 받고, 그래프 개선은 백그라운드 큐에 올라감
result = await agent.query("질문")
print(result["answer"])

# 백그라운드 작업이 끝나길 명시적으로 기다리고 싶을 때 (테스트/종료 시)
await agent.flush()

# 수동으로 EVOLVE 파이프라인 전체를 즉시 실행
await agent.evolve()

# 구조 점검
await agent.lint()
```

---

## 파일 구조

```
harness_pjt/
├── wikigraph/
│   ├── agent.py              # WikiGraphAgent: ANALYZE → fork(RESPOND / 백그라운드 EVOLVE 큐)
│   ├── config.py              # 네 기둥별 임계값
│   ├── state.py               # LangGraph 상태 스키마 (TypedDict)
│   ├── metadata.py            # 쿼리 로그 + 엔티티 메타데이터 영속화
│   ├── sources.py              # 파일 수집, 변경 감지
│   └── operations/
│       ├── analyze.py         # 쿼리 개선: 재작성 + 분해
│       ├── ingest.py          # 문서 삽입 + 검증
│       ├── query.py           # RESPOND(전경) + RETRIEVE/EVALUATE(백그라운드)
│       ├── evolve.py           # 지식그래프 개선: tier1(매 쿼리) + tier2(배치)
│       ├── structural.py       # 구조 관계 개선: 문서-문서, 청크-청크
│       └── lint.py             # 린팅: 구조 점검 + 제한적 자동 정리
└── tests/
    └── test_wikigraph.py
```

---

## 한계와 열린 질문

현재 구현은 아키텍처 제안과 프로토타입 단계입니다. 실제로 vanilla LightRAG 대비 검색 품질이 개선되는지, STRUCTURAL이 만든 문서/청크 구조 관계가 멀티홉 질의에 실질적으로 도움이 되는지는 정량적으로 검증되지 않았습니다. 자세한 위험 분석과 TODO는 [ARCHITECTURE.md](ARCHITECTURE.md#한계)를 참고하세요.

---

## References

- [Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) (2026.04)
- [LightRAG](https://arxiv.org/abs/2410.05779) (HKUDS, 2024)
- [On the Evolution of Knowledge Graphs: A Survey](https://arxiv.org/abs/2310.04835)

전략별 근거 논문은 [ARCHITECTURE.md의 References](ARCHITECTURE.md#references)에 정리되어 있습니다.
