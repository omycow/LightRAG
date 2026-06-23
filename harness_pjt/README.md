# Evolving LightRAG

LightRAG 위에 **쿼리 로그 기반 그래프 자동 진화 에이전트**를 구축한 프로젝트입니다.

Karpathy의 [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)(2026.04) 패턴에서 Raw / Wiki / Schema 3계층 구조와 지식 라이프사이클을 참고하여, LightRAG의 그래프 인프라 위에서 동일한 역할을 수행하는 구조를 설계했습니다.

![Core Cycle](images/core_cycle.png)

---

## 3계층 구조

LLM Wiki는 Raw Sources → Wiki → Schema 3계층으로 지식을 관리합니다. Evolving LightRAG에서는 이를 다음과 같이 대응시킵니다.

| 계층 | LLM Wiki에서 | Evolving LightRAG에서 | 구현 |
|---|---|---|---|
| **Raw** | 원본 문서 (불변) | 원본 청크 (불변, 검증용) | `text_chunks` KV 스토리지에 보존 |
| **Wiki** | LLM이 유지보수하는 마크다운 페이지 | 그래프 엔티티 + 릴레이션 | LLM 자동 추출 + EVOLVE 증분 추가 |
| **Schema** | 위키 구조 규칙 (CLAUDE.md 등) | 진화/점검 규칙 | `WikiGraphConfig` (임계값, 주기 설정) |

**Raw 계층**은 `rag.ainsert()` 시 원본 텍스트가 `text_chunks`에 저장되어 이후 검증에 활용됩니다. LLM Wiki에서는 합성 결과만 남아 오류가 전파되지만, 여기서는 원본이 보존됩니다.

**Wiki 계층**은 LLM이 문서에서 추출한 엔티티/릴레이션이 그래프에 저장되고, EVOLVE가 쿼리 패턴을 분석하여 새 릴레이션을 증분 추가합니다. LLM Wiki에서 페이지 10-15개를 통째로 재작성하는 것과 달리, 노드/엣지 단위로 추가되므로 기존 지식이 보존됩니다.

**Schema 계층**은 EVOLVE 트리거 조건(공동 검색 횟수, 품질 임계값), LINT 규칙(허브 차수, 유사도 임계값), 자동 진화 주기 등을 `WikiGraphConfig`로 관리합니다.

---

## 시스템 비교

| | LightRAG (vanilla) | LLM Wiki v2 | **Evolving LightRAG** |
|---|---|---|---|
| 지식 저장 | 그래프 + 벡터 | 마크다운 페이지 | 그래프 + 벡터 + BM25 |
| 검색 | 벡터 유사도 + 그래프 순회 | 풀컨텍스트 로딩 또는 하이브리드 | **벡터 + BM25 + 그래프 + RRF** |
| 지식 업데이트 | 새 문서 삽입 시에만 | LLM이 기존 페이지 읽고 재작성 | **쿼리 패턴 기반 증분 업데이트** |
| 업데이트 비용 | 낮음 (증분) | 높음 (10-15 페이지 재작성) | 낮음 (노드/엣지 단위) |
| 원본 보존 | 청크 보존 | 합성 결과만 남음 | 청크 보존 (검증 가능) |
| 스케일 | 수만 노드 | ~1000페이지 | 수만 노드 |
| 구조 점검 | 없음 | LLM 전체 스캔 | **그래프 알고리즘 (0 토큰)** |
| 다중 홉 추론 | 그래프 순회 가능 | 페이지 기반으로 제한적 | 그래프 순회 + 단축 경로 자동 생성 |
| 키워드 검색 | 없음 | v2에서 추가 | **BM25 점진적 인덱싱** |

---

## 하이브리드 검색

LightRAG(vanilla)는 벡터 코사인 유사도에만 의존합니다. 고유명사나 짧은 쿼리에서 정확한 매칭이 어렵습니다.

![Before: 벡터 전용](images/before_vector_only.png)

Evolving LightRAG는 각 검색 경로(엔티티/관계/청크)에 **BM25 키워드 검색**을 추가하고 RRF로 결합합니다.

![After: 하이브리드](images/after_hybrid_search.png)

![Hybrid Search Flow](images/hybrid_search_flow.png)

### RRF (Reciprocal Rank Fusion)

```
RRF(d) = Σ 1/(k + rank_r(d)),  k=60
```

![RRF Fusion](images/rrf_fusion.png)

### BM25 인덱싱 대상

BM25는 청크뿐 아니라 **그래프의 엔티티(노드)와 릴레이션(엣지)의 모든 텍스트 필드**를 인덱싱합니다.

![BM25 Indexed Fields](images/bm25_indexed_fields.png)

| 대상 | 인덱싱 필드 |
|---|---|
| **Chunks** | `content` (원본 텍스트) |
| **Entities (Nodes)** | `entity_name` + `entity_type` + `content` + `description` |
| **Relations (Edges)** | `src_id` + `tgt_id` + `keywords` + `content` + `description` |

엔티티의 타입 정보("artifact", "organization" 등)나 릴레이션의 키워드("developed by", "uses" 등)도 키워드 검색에 포함되어, 벡터 유사도만으로는 매칭하기 어려운 **구조적 메타데이터 기반 검색**이 가능합니다.

### 점진적 BM25 인덱싱

전체 재빌드 대신 각 VDB upsert 시점에 `BM25Index.add()`를 호출하여 즉시 반영합니다. 디스크 영속화(`save()/load()`)도 지원하여 재시작 시 재빌드가 불필요합니다.

---

## 에이전트 구조

LangGraph StateGraph 기반 에이전트가 LightRAG의 공개 API만 사용하여 동작합니다. LightRAG 내부 코드를 수정하지 않습니다.

![State Machine](images/state_machine.png)

---

## INGEST — 문서 삽입과 업데이트

Raw 계층에 해당합니다. 새 문서가 들어오면 LightRAG의 풀 파이프라인을 태워 그래프에 증분 추가합니다.

![Ingest Pipeline](images/ingest_pipeline.png)

```python
# 새 문서 삽입
await agent.ingest(["문서 텍스트..."], file_paths=["doc.md"])

# 디렉토리 감시: 변경된 파일만 자동 감지 + 증분 재처리
from wikigraph.sources import detect_changed_files
changed = detect_changed_files("./docs/", "./meta/hashes.json")
if changed:
    await agent.ingest([text for _, text in changed], file_paths=[name for name, _ in changed])
```

내부 동작:
1. `rag.ainsert()` → 청킹 → LLM 엔티티/관계 추출 → 벡터 임베딩 → BM25 인덱싱 → 그래프 저장
2. 기존 엔티티가 새 문서에 재등장하면 description이 **병합**됨 (덮어쓰기가 아님)
3. 삽입 후 대표 구절로 `aquery_data()` 검증 → 추출이 정상인지 확인

`detect_changed_files()`는 content hash를 비교하여 **변경된 파일만** 재처리합니다. 전체 재삽입이 아닌 증분 업데이트입니다.

---

## EVOLVE — 쿼리 패턴 기반 그래프 진화

Wiki 계층에 해당합니다. 쿼리 로그를 분석하여 그래프에 새 릴레이션(또는 엔티티)을 **증분 추가**합니다. `ainsert_custom_kg()`를 통해 주입하므로 벡터 DB와 BM25 인덱스도 자동으로 갱신됩니다.

![EVOLVE Strategies](images/evolve_strategies.png)

### 전략 1: Co-retrieval Strengthening (공동 검색 관계 강화)

**문제**: 엔티티 A와 B가 여러 쿼리에서 반복적으로 함께 검색되지만, 그래프에 직접 연결이 없습니다. 사용자는 이 둘을 관련 있다고 판단하고 있지만 원본 문서에 명시적 관계가 없어서 그래프가 이를 반영하지 못합니다.

**동작**:
1. 쿼리 로그에서 엔티티 쌍별 공동 검색 횟수를 집계합니다
2. 3회 이상 함께 검색된 쌍 중 `graph.has_edge(A, B)` = False인 것을 필터링합니다
3. 양쪽 엔티티의 description을 LLM에 전달하여 관계를 추론합니다
4. 새 릴레이션을 `weight=0.5`, `source_id="wikigraph_evolve"`로 주입합니다

**효과**: 원본 문서에 명시되지 않은 **암묵적 관계**를 사용 패턴에서 발견합니다. 예를 들어 "React"와 "Bun"이 프론트엔드 쿼리에서 항상 함께 나오면, "Bun is the build tool for the React frontend" 같은 관계가 자동 생성됩니다.

**마킹**: `weight=0.5` (원본 추출 = 1.0과 구분), `source_id="wikigraph_evolve"` → 원본 vs 추론 지식 추적 가능. LINT에서 미사용 추론 엣지를 자동 정리할 때 이 마킹을 기준으로 합니다.

### 전략 2: Gap Filling (추출 누락 보강)

**문제**: 특정 쿼리에 대해 그래프에서 관련 엔티티/관계가 전혀 검색되지 않습니다 (quality < 0.3). 그런데 원본 청크에서는 관련 텍스트가 검색됩니다. 이는 LLM 추출 과정에서 엔티티/관계가 누락된 것입니다.

**동작**:
1. 쿼리 로그에서 quality < 0.3인 실패 쿼리를 수집합니다
2. 해당 쿼리에서 **청크는 검색됐지만 엔티티는 없는** 경우를 필터링합니다
3. 검색된 원본 청크 텍스트를 LLM에 전달하여 엔티티/관계를 **재추출**합니다
4. 청크도 엔티티도 없는 경우 → 리포트만 출력 ("관련 문서 추가 필요")

**기존 방식과 차이**: LLM에게 "지어내라"고 하지 않고, **원본 청크에 근거가 있는 경우에만** 그래프를 보강합니다. Raw 계층(원본 데이터)이 항상 근거가 되므로 hallucination을 방지합니다.

**효과**: LLM 추출이 놓친 엔티티/관계를 **원본 데이터 기반으로** 복구합니다. 이 전략만이 **새 엔티티 노드**를 추가할 수 있습니다 (나머지 전략은 기존 노드 간 엣지만 추가).

### 전략 3: Shortcut Path (다중 홉 단축 경로)

**문제**: A→B→C 경로가 쿼리에서 반복적으로 사용되지만, A에서 C로 가려면 항상 B를 거쳐야 합니다. 그래프 순회 비용이 발생하고, B가 누락되면 A-C 관계를 찾을 수 없습니다.

**동작**:
1. 쿼리 로그에서 관계 (A,B)와 (B,C)가 3회 이상 함께 검색된 패턴을 탐지합니다
2. A→C 직접 엣지가 없는 경우, A-B와 B-C의 설명을 합성하여 A→C 단축 엣지를 생성합니다
3. `weight=0.5`로 주입합니다

**효과**: 자주 사용되는 다중 홉 경로를 **1홉으로 단축**합니다. "LightRAG → Bun → React" 경로가 자주 쓰이면 "LightRAG → React" 직접 관계가 생겨 다음 쿼리에서 즉시 접근됩니다.

### EVOLVE 트리거 조건

| 조건 | 설명 |
|---|---|
| quality < 0.5 (reactive) | EVALUATE에서 결과 품질이 낮으면 즉시 트리거 |
| N번째 쿼리 (proactive) | `auto_evolve_interval` (기본 5) 마다 자동 트리거 |
| 수동 호출 | `agent.evolve()` 로 명시적 트리거 |

---

## LINT — 그래프 구조 점검

Schema 계층에 해당합니다. 그래프 알고리즘으로 구조적 이상을 탐지합니다. LLM Wiki에서 전체 위키를 LLM으로 스캔하는 것과 달리, 대부분의 점검이 **0 토큰**으로 수행됩니다.

| 점검 항목 | 방법 | 토큰 비용 | 자동 수정 |
|---|---|---|---|
| 고아 노드 | `node_degree() == 0` | 0 | 리포트만 (정상 존재 가능) |
| 허브 과부하 | `node_degree() > threshold` | 0 | 리포트만 |
| 중복 엔티티 | 엔티티명 임베딩 유사도 > 0.95 | embedding | 리포트만 |
| 방치 엔티티 | 쿼리 로그에서 미접근 | 0 | 리포트만 |
| 모순 탐지 | 다중 description 비교 | optional LLM | 리포트만 |
| **stale evolved edge** | EVOLVE 생성 + 미사용 + 저가중치 | 0 | **자동 제거** |

자동 제거 대상은 **EVOLVE가 추론으로 만든 엣지 중 실제로 활용되지 않는 것**만 해당합니다. 원본 추출 지식과 자연 발생 엔티티(고아 노드 포함)는 자동 삭제하지 않습니다.

---

## 실행 결과

```
INGEST: 2 documents inserted, 2/2 verified
  → Graph: 20 nodes, 13 edges

QUERY × 5:
  "What is LightRAG?"       → 11 entities, 10 relations
  "How does BM25 work?"     → 12 entities,  9 relations
  "What is hybrid search?"  → 15 entities, 17 relations
  "What query modes exist?" →  6 entities,  4 relations
  "What is RRF?"            →  7 entities,  5 relations (5th query → auto EVOLVE)

EVOLVE (auto, 5th query):
  + co-retrieval: Hybrid Query Mode → Local Query Mode (4 co-occurrences)
  + co-retrieval: Global Query Mode → Knowledge Graph (3 co-occurrences)
  + shortcut: Hybrid Query Mode → Knowledge Graph (via LightRAG)
  → 10 relationships injected

EVOLVE (manual):
  + shortcut: RRF → TF-IDF (via BM25)
  + shortcut: BM25 → Vector (via RRF)
  → 4 relationships injected

LINT: 20 entities scanned, no issues found

Final graph: 20 nodes, 27 edges (13 original + 14 auto-generated)
```

![Comparison](images/comparison_chart.png)

---

## 파일 구조

```
harness_pjt/
├── wikigraph/
│   ├── agent.py           # LangGraph StateGraph + WikiGraphAgent API
│   ├── config.py          # Schema 계층: EVOLVE/LINT 임계값
│   ├── state.py           # LangGraph 상태 스키마 (TypedDict)
│   ├── metadata.py        # 쿼리 로그 + 엔티티 메타데이터 영속화
│   ├── sources.py         # Raw 계층: 파일 수집, 변경 감지
│   └── operations/
│       ├── ingest.py      # Raw → Wiki: rag.ainsert() + 검증
│       ├── query.py       # Wiki 검색: rag.aquery_data() + 로깅 + 평가
│       ├── evolve.py      # Wiki 진화: 3가지 전략으로 그래프 증분 개선
│       └── lint.py        # Schema 점검: 5가지 구조 점검 + 자동 정리
├── demo_wikigraph.ipynb
├── demo_hybrid_search.ipynb
└── README.md
```

---

## 사용법

```python
from wikigraph.agent import WikiGraphAgent
from wikigraph.config import WikiGraphConfig

rag = LightRAG(
    working_dir="./my_kb",
    llm_model_func=llm_func,
    embedding_func=embed_func,
    addon_params={"enable_hybrid_search": True},
)
await rag.initialize_storages()

agent = WikiGraphAgent(rag, WikiGraphConfig(auto_evolve_interval=5))

# Raw: 문서 삽입 (증분)
await agent.ingest(["문서 내용..."])

# Wiki: 검색 + 자동 진화
result = await agent.query("질문")

# Wiki: 수동 진화 트리거
await agent.evolve()

# Schema: 구조 점검
await agent.lint()
```

---

## 기술 스택

| 컴포넌트 | 기술 |
|---|---|
| Agent 오케스트레이션 | LangGraph StateGraph |
| 지식 엔진 | LightRAG (Graph + VDB + BM25) |
| 검색 | Vector + BM25 + Graph Traversal + RRF |
| 임베딩 | sentence-transformers/all-MiniLM-L6-v2 |
| LLM | Qwen 35B (원격) |

---

## References

- [Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) (2026.04)
- [LLM Wiki v2](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2)
- [LightRAG](https://arxiv.org/abs/2410.05779) (HKUDS, 2024)
- [What LLM Wiki Is Missing](https://dev.to/penfieldlabs/what-karpathys-llm-wiki-is-missing-and-how-to-fix-it-1988)
- [RAG vs Agent Memory vs LLM Wiki](https://dev.to/vishalmysore/rag-vs-agent-memory-vs-llm-wiki-a-practical-comparison-1oo6)
