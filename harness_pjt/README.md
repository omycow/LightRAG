# Evolving LightRAG

LightRAG의 그래프 기반 RAG에 **쿼리 로그 기반 자동 지식 진화**를 추가한 에이전트 시스템입니다. Karpathy의 [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 패턴에서 지식 라이프사이클(Ingest → Query → Evolve → Lint) 개념을 차용하고, LightRAG의 증분 그래프 업데이트와 하이브리드 검색을 활용하여 **스케일러블하면서도 지식이 점진적으로 개선되는 RAG**를 구현합니다.

![Core Cycle](images/core_cycle.png)

---

## 동기

RAG 시스템과 LLM Wiki 패턴은 각각 다른 강점을 가지고 있습니다.

| | Traditional RAG | LLM Wiki ([Karpathy](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), 2026.04) | **Evolving LightRAG** |
|---|---|---|---|
| 지식 저장 | 벡터 청크 | 마크다운 페이지 | 그래프 노드/엣지 |
| 지식 업데이트 | 문서 재삽입 시에만 | 페이지 재작성 (LLM이 기존 페이지를 읽고 수정) | **쿼리 패턴 기반 증분 업데이트** |
| 검색 | 벡터 유사도 | 풀컨텍스트 로딩 또는 하이브리드 | 벡터 + BM25 + 그래프 순회 + RRF |
| 스케일 | 수백만 문서 | ~1000페이지 ([참고](https://dev.to/penfieldlabs/what-karpathys-llm-wiki-is-missing-and-how-to-fix-it-1988)) | 수만 노드 |
| 업데이트 비용 | 청크 재삽입 | 10-15페이지 재작성 | 노드/엣지 단위 증분 |
| 원본 보존 | 원본 참조 가능 | 합성 결과만 남음 ([오류 전파 문제](https://dev.to/vishalmysore/rag-vs-agent-memory-vs-llm-wiki-a-practical-comparison-1oo6)) | 원본 청크 보존 |
| 구조 점검 | 없음 | LLM 전체 스캔 | 그래프 알고리즘 (0 토큰) |
| 다중 홉 추론 | 제한적 | 제한적 | 그래프 경로 순회 |

### LLM Wiki 계층 매핑

| LLM Wiki | Evolving LightRAG |
|---|---|
| Raw Sources (원본, 불변) | 원본 청크 (`text_chunks` KV에 보존) |
| Wiki Pages (LLM이 합성/유지보수) | 그래프 엔티티 + 릴레이션 (LLM 추출 + EVOLVE) |
| Schema (구조 규칙) | WikiGraphConfig (EVOLVE/LINT 임계값) |

---

## BM25 하이브리드 검색

LightRAG의 벡터 전용 검색에 **BM25 키워드 검색**을 추가하여 [RRF(Reciprocal Rank Fusion)](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)로 결합합니다.

![Before: 벡터 전용](images/before_vector_only.png)

![After: 하이브리드](images/after_hybrid_search.png)

![Hybrid Search Flow](images/hybrid_search_flow.png)

### RRF 수식

```
RRF(d) = Σ 1/(k + rank_r(d)),  k=60
```

양쪽 검색에서 모두 높은 순위를 받은 문서가 최종 순위에서 우선됩니다.

![RRF Fusion](images/rrf_fusion.png)

### 점진적 BM25 인덱싱

기존 방식(전체 재빌드) 대신, **각 VDB upsert 시점에 즉시 BM25 인덱스를 갱신**합니다. 엔티티/릴레이션/청크 각각의 저장 시점에 `BM25Index.add()` 호출로 증분 업데이트합니다.

### 구현 파일

| 파일 | 역할 |
|---|---|
| `lightrag/bm25_index.py` | BM25Index 클래스, RRF 함수, 증분 `add()`, 디스크 `save()/load()` |
| `lightrag/lightrag.py` | BM25 빌드/로드, upsert 콜백, `global_config` 주입 |
| `lightrag/operate.py` | 3개 검색 함수(엔티티/관계/청크)에 하이브리드 모드 적용 |
| `lightrag/pipeline.py` | chunks VDB upsert 후 BM25 즉시 업데이트 |

---

## 에이전트 아키텍처

LangGraph StateGraph 기반의 에이전트가 LightRAG의 공개 API(`ainsert`, `aquery_data`, `ainsert_custom_kg`)를 조합하여 4가지 Operation을 수행합니다. LightRAG 내부 코드 수정 없이 외부 래퍼로 동작합니다.

![State Machine](images/state_machine.png)

---

## Operation 상세

### INGEST — 증분 문서 삽입

```python
await agent.ingest(["문서 텍스트..."], file_paths=["doc.md"])
```

![Ingest Pipeline](images/ingest_pipeline.png)

`rag.ainsert()`를 통해 풀 파이프라인(청킹 → LLM 엔티티/관계 추출 → 벡터 임베딩 → BM25 인덱싱 → 그래프 저장)이 실행됩니다. 기존 그래프를 재구축하지 않고 새 노드/엣지만 추가합니다.

**변경 감지**: `sources.detect_changed_files(directory, hash_store_path)` 함수로 content hash 기반 변경분만 재처리할 수 있습니다.

### QUERY → EVALUATE → EVOLVE

```python
result = await agent.query("질문")
```

1. **QUERY**: `rag.aquery_data()` 호출 → 벡터 + BM25 + 그래프 순회로 엔티티/관계/청크 검색. 결과를 쿼리 로그에 기록 (검색된 엔티티 목록, 타임스탬프, 엔티티별 access_count 갱신).

2. **EVALUATE**: 휴리스틱 기반 품질 점수 산출 (LLM 호출 없음, 0 토큰):
   - 엔티티 0개 + 청크 0개 → quality=0.0
   - 엔티티 있으나 관계 없음 → quality=0.3
   - 엔티티 + 관계 + 청크 모두 있음 → quality=0.5~1.0

3. **EVOLVE** (quality < 0.5 또는 `auto_evolve_interval`번째 쿼리일 때 트리거):

![EVOLVE Strategies](images/evolve_strategies.png)

| 전략 | 트리거 조건 | 동작 | LightRAG API |
|---|---|---|---|
| **Co-retrieval strengthening** | 엔티티 쌍이 3회+ 공동 검색됨 | LLM으로 관계 추론 → 새 엣지 | `ainsert_custom_kg()` |
| **Gap filling** | 쿼리 quality < 0.3 | LLM에게 필요한 엔티티/관계 제안 요청 | `ainsert_custom_kg()` |
| **Shortcut path** | A→B→C 경로 3회+ 사용 | A→C 직접 관계 생성 | `ainsert_custom_kg()` |

모든 추론 지식에 `source_id="wikigraph_evolve"`, `weight=0.5`를 부여하여 원본 추출 지식과 구분합니다.

### LINT — 그래프 구조 점검

```python
result = await agent.lint()
```

| 점검 항목 | 알고리즘 | 토큰 비용 |
|---|---|---|
| 고아 노드 | `node_degree() == 0` | 0 |
| 허브 과부하 | `node_degree() > threshold` | 0 |
| 중복 엔티티 | 엔티티명 임베딩 유사도 > 0.95 | embedding only |
| 방치 엔티티 | 쿼리 로그에서 미접근 | 0 |
| 모순 탐지 | 다중 description 비교 | optional LLM |
| **자동 정리** | EVOLVE 생성 엣지 중 미사용 + 저가중치 → 제거 | 0 |

---

## 실행 결과

```
INGEST: 2 documents inserted, 2/2 verified

QUERY × 5:
  "What is LightRAG?"       → 11 entities, 10 relations ✓
  "How does BM25 work?"     → 12 entities,  9 relations ✓
  "What is hybrid search?"  → 15 entities, 17 relations ✓
  "What query modes exist?" →  6 entities,  4 relations ✓
  "What is RRF?"            →  7 entities,  5 relations ✓ (5th → EVOLVE)

EVOLVE (auto-triggered):
  + co-retrieval: Hybrid Query Mode → Local Query Mode (4 co-occurrences)
  + co-retrieval: Global Query Mode → Knowledge Graph (3 co-occurrences)
  + shortcut: Hybrid Query Mode → Knowledge Graph (via LightRAG)
  → 10 relationships injected

EVOLVE (manual):
  + shortcut: RRF → TF-IDF (via BM25)
  + shortcut: BM25 → Vector (via RRF)
  → 4 relationships injected

LINT: 20 entities scanned, no issues found

Graph: 20 nodes, 27 edges (13 original + 14 auto-generated)
```

![Comparison](images/comparison_chart.png)

---

## 파일 구조

```
harness_pjt/
├── wikigraph/
│   ├── agent.py           # LangGraph StateGraph + WikiGraphAgent API
│   ├── config.py          # EVOLVE/LINT 임계값, auto_evolve_interval
│   ├── state.py           # LangGraph 상태 스키마 (TypedDict)
│   ├── metadata.py        # 쿼리 로그 + 엔티티 메타데이터 JSON 영속화
│   ├── sources.py         # 파일 수집, content hash 기반 변경 감지
│   └── operations/
│       ├── ingest.py      # rag.ainsert() 래핑 + 추출 검증
│       ├── query.py       # rag.aquery_data() + 로깅 + 휴리스틱 평가
│       ├── evolve.py      # 3가지 진화 전략 (co-retrieval / gap-fill / shortcut)
│       └── lint.py        # 5가지 구조 점검 + 자동 정리
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

await agent.ingest(["문서 내용..."])          # 증분 삽입
result = await agent.query("질문")           # 검색 + 자동 진화
await agent.evolve()                        # 수동 진화
await agent.lint()                          # 구조 점검
```

---

## 기술 스택

| 컴포넌트 | 기술 |
|---|---|
| Agent 오케스트레이션 | LangGraph StateGraph |
| 지식 엔진 | LightRAG (Graph + VDB + BM25) |
| 검색 | Vector + BM25 + Graph Traversal + RRF |
| 임베딩 | sentence-transformers/all-MiniLM-L6-v2 (로컬) |
| LLM | Qwen 35B (원격 서버) |

---

## References

- [Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) (2026.04)
- [LLM Wiki v2](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2)
- [LightRAG](https://arxiv.org/abs/2410.05779) (HKUDS, 2024)
- [What LLM Wiki Is Missing](https://dev.to/penfieldlabs/what-karpathys-llm-wiki-is-missing-and-how-to-fix-it-1988)
- [RAG vs Agent Memory vs LLM Wiki](https://dev.to/vishalmysore/rag-vs-agent-memory-vs-llm-wiki-a-practical-comparison-1oo6)
