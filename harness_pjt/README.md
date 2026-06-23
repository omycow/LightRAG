# Evolving LightRAG

LightRAG 위에 **쿼리 로그 기반 그래프 자동 진화 에이전트**를 구축한 프로젝트입니다.

Karpathy의 [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)(2026.04) 패턴에서 Raw / Wiki / Schema 3계층 구조를 참고하여, LightRAG의 그래프 인프라 위에서 동일한 역할을 수행합니다.

![Core Cycle](images/core_cycle.png)

---

## 3계층 구조

| 계층 | LLM Wiki | Evolving LightRAG | 구현 |
|---|---|---|---|
| **Raw** | 원본 문서 (불변) | 원본 청크 (불변, 검증용) | `text_chunks` KV, `detect_changed_files()` |
| **Wiki** | LLM 유지보수 마크다운 | 그래프 엔티티 + 릴레이션 | LLM 추출 + EVOLVE 증분 + `ingest_artifact()` |
| **Schema** | 구조 규칙 | 진화/점검 규칙 | `WikiGraphConfig` |

---

## 시스템 비교

| | LightRAG (vanilla) | LLM Wiki v2 | **Evolving LightRAG** |
|---|---|---|---|
| 지식 저장 | 그래프 + 벡터 | 마크다운 페이지 | 그래프 + 벡터 + BM25 |
| 검색 | 벡터 + 그래프 순회 | 풀컨텍스트 또는 하이브리드 | **벡터 + BM25 + 그래프 + RRF** |
| 지식 업데이트 | 문서 삽입 시에만 | 페이지 재작성 | **쿼리 패턴 + 디렉토리 감시** |
| 업데이트 비용 | 낮음 | 높음 (10-15페이지) | 낮음 (노드/엣지 단위) |
| 원본 보존 | 청크 보존 | 합성만 남음 | 청크 보존 (검증 가능) |
| 구조 점검 | 없음 | LLM 전체 스캔 | **그래프 알고리즘 (0 토큰)** |
| 산출물 재삽입 | 없음 | 답변 → 위키 페이지 | **`ingest_artifact()` → 그래프** |

---

## 하이브리드 검색

LightRAG(vanilla)의 벡터 전용 검색에 **BM25 키워드 검색**을 추가하고 [RRF](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)로 결합합니다.

![Before/After](images/after_hybrid_search.png)

BM25는 청크뿐 아니라 **그래프 노드/엣지의 모든 텍스트 필드**를 인덱싱합니다.

![BM25 Indexed Fields](images/bm25_indexed_fields.png)

| 대상 | 인덱싱 필드 |
|---|---|
| Chunks | `content` |
| Entities | `entity_name` + `entity_type` + `content` + `description` |
| Relations | `src_id` + `tgt_id` + `keywords` + `content` + `description` |

각 VDB upsert 시점에 `BM25Index.add()`로 즉시 반영됩니다 (전체 재빌드 불필요).

---

## 에이전트 구조

![State Machine](images/state_machine.png)

LangGraph StateGraph 기반. LightRAG 공개 API만 사용하며 내부 코드를 수정하지 않습니다.

---

## 주요 지표

| 지표 | 출처 | 역할 |
|---|---|---|
| **weight** | LightRAG 기본 | 관계 신뢰도. 원본=1.0(누적), 추론=0.5. 검색 순위 + LINT 정리 기준 |
| **quality** | Evolving 추가 | 쿼리 결과 품질 0~1 (휴리스틱). EVOLVE 트리거 |
| **access_count** | Evolving 추가 | 엔티티 검색 빈도. LINT 방치 탐지 + 추론 엣지 정리 |
| **source_id** | LightRAG 기본 | 출처 청크 추적. 원본 vs 추론 구분, 근거 검증 |

---

## INGEST — 문서 삽입

```python
await agent.ingest(["문서 텍스트..."], file_paths=["doc.md"])
```

`rag.ainsert()` → 청킹 → LLM 추출 → 임베딩 → BM25 → 그래프 저장. 기존 엔티티 재등장 시 description 병합.

### 디렉토리 감시 자동 삽입

```python
# 디렉토리의 변경된 파일만 자동 감지 + 증분 삽입
result = await agent.watch("./docs/")
```

`watch(directory)`는 content hash로 변경 파일을 감지하여 자동으로 `ingest()`를 수행합니다. 에이전트가 생성한 문서(요약, 분석 결과 등)를 감시 디렉토리에 저장하면 다음 `watch()` 호출 시 자동으로 그래프에 삽입됩니다.

### 산출물 재삽입

```python
# 에이전트의 지식 산출물을 직접 그래프에 삽입
answer = await rag.aquery("LightRAG 아키텍처 요약")
await agent.ingest_artifact(answer, label="architecture_summary")
```

쿼리 답변, 분석 리포트 등 에이전트가 생성한 지식 산출물을 `ingest_artifact()`로 그래프에 재삽입합니다. LLM Wiki에서 좋은 답변이 위키 페이지로 편입되는 것과 동일한 역할을 합니다. 삽입된 산출물은 일반 문서와 동일하게 청킹 → 엔티티 추출 → 그래프 저장 파이프라인을 거칩니다.

---

## EVOLVE — 그래프 진화

쿼리 로그를 분석하여 그래프를 **추가/제거/수정**합니다.

![EVOLVE Strategies](images/evolve_strategies.png)

| # | 전략 | 트리거 | 그래프 변경 |
|---|---|---|---|
| 1 | **Co-retrieval** | 엔티티 쌍 3회+ 공동 검색 | 엣지 **추가** (LLM 관계 추론) |
| 2 | **Gap Filling** | 청크 검색됨 + 엔티티 없음 | 노드+엣지 **추가** (원본 청크 기반 재추출) |
| 3 | **Shortcut** | A→B→C 경로 3회+ 사용 | 엣지 **추가** (A→C 단축) |
| 4 | **Source Verification** | source chunk가 text_chunks에서 소멸 | 엣지 **제거** |
| 5 | **Contradiction** | 엔티티에 `<SEP>` 구분 description 2개+ | 노드 **수정** (LLM 통합) |

- 전략 1-3이 추가한 관계: `weight=0.5`, `source_id="wikigraph_evolve"`로 마킹
- 전략 2(Gap Filling)만 새 엔티티 노드를 추가 가능, 나머지는 기존 노드 간 엣지만 조작
- 전략 2는 **원본 청크에 근거가 있는 경우에만** 동작 (hallucination 방지)

**트리거**: quality < 0.5 (reactive) | N번째 쿼리마다 (proactive) | `agent.evolve()` (수동)

### 이론적 배경

- **Co-retrieval → Link Prediction**: [NoGE](https://arxiv.org/abs/2104.07396) — co-occurrence 기반 KGC
- **Gap Filling → Extraction Repair**: [Self-Improving RAG](https://ojs.iscram.org/index.php/Proceedings/article/view/154) — 피드백 루프 보강
- **Shortcut → Transitive Closure**: [SMORE](https://arxiv.org/abs/2110.14890) — multi-hop reasoning

---

## LINT — 그래프 점검

그래프 알고리즘으로 구조적 이상을 탐지합니다 (대부분 0 토큰).

| 점검 | 방법 | 자동 수정 |
|---|---|---|
| 고아 노드 | `node_degree() == 0` | 리포트만 |
| 허브 과부하 | `node_degree() > 50` | 리포트만 |
| 중복 엔티티 | 임베딩 유사도 > 0.95 | 리포트만 |
| 방치 엔티티 | access_count < 2 | 리포트만 |
| 모순 탐지 | 다중 description | 리포트만 |
| **추론 엣지 정리** | `wikigraph_evolve` + weight ≤ 0.5 + 미사용 | **자동 제거** |

---

## 실행 결과

```
INGEST: 2 documents → 20 nodes, 13 edges

QUERY × 5 → 5th query triggers auto EVOLVE:
  + co-retrieval: Hybrid Query Mode → Local Query Mode (4 co-occurrences)
  + shortcut: Hybrid Query Mode → Knowledge Graph (via LightRAG)
  → 10 relationships injected

EVOLVE (manual): + 4 shortcuts

LINT: 20 entities scanned, no issues

Final: 20 nodes, 27 edges (13 original + 14 auto-generated)
```

![Comparison](images/comparison_chart.png)

---

## 사용법

```python
agent = WikiGraphAgent(rag, WikiGraphConfig(auto_evolve_interval=5))

await agent.ingest(["문서 내용..."])          # 문서 삽입
result = await agent.query("질문")           # 검색 + 자동 진화
await agent.watch("./docs/")                # 디렉토리 감시 + 자동 삽입
await agent.ingest_artifact(text, "label")  # 산출물 재삽입
await agent.evolve()                        # 수동 진화
await agent.lint()                          # 구조 점검
```

---

## 한계와 열린 질문

### 현재 구현의 한계

- **노이즈 누적**: EVOLVE가 추가한 관계가 쌓이면 그래프 노이즈 증가 가능 ([RAG Survey](https://arxiv.org/abs/2506.00054)). weight 마킹 + LINT 정리로 대응하나 장기 효과 미검증.
- **수확 체감**: 반복 3회 이후 추가 이득 급감 ([Iterative GraphRAG](https://arxiv.org/abs/2509.25530)). 최적 주기/임계값은 도메인별 튜닝 필요.
- **LLM 추출 정확도**: 60-85% 수준 ([RAG Survey](https://arxiv.org/abs/2506.00054)). gap filling 재추출도 같은 실수 반복 가능.

### 열린 질문

- **vanilla LightRAG나 LLM Wiki 대비 이점이 실제로 있는가?** EVOLVE로 엣지 수가 느는 것은 확인했지만, 검색 정확도 향상을 정량적으로 비교하지 않았습니다. 같은 쿼리셋에 대해 vanilla vs Evolving의 결과 비교가 필요합니다.
- **복잡성 대비 효용이 있는가?** 에이전트 인프라의 추가 복잡성이 단순히 문서를 더 넣는 것보다 나은지 검증이 필요합니다.
- **LLM Wiki로 충분한 규모에서 이 시스템이 필요한가?** ~1000페이지 이하의 유스케이스에서는 LLM Wiki가 더 단순하고 효과적일 수 있습니다.
- **EVOLVE 전략 간 피드백 루프**: co-retrieval → shortcut → co-retrieval 순환이 장기 운영 시 예측 불가 그래프 변형을 일으킬 수 있습니다.

### TODO

- [ ] vanilla LightRAG vs Evolving LightRAG 검색 품질 정량 비교
- [ ] timestamp 기반 confidence decay
- [ ] Human-in-the-loop: EVOLVE 결과 승인/거부
- [ ] 대규모 코퍼스에서의 장기 검증

---

## 파일 구조

```
harness_pjt/
├── wikigraph/
│   ├── agent.py          # LangGraph StateGraph + watch() + ingest_artifact()
│   ├── config.py         # EVOLVE/LINT 임계값
│   ├── state.py          # 상태 스키마
│   ├── metadata.py       # 쿼리 로그 + 엔티티 메타 영속화
│   ├── sources.py        # 파일 수집, content hash 변경 감지
│   └── operations/
│       ├── ingest.py     # rag.ainsert() + 검증
│       ├── query.py      # rag.aquery_data() + 로깅 + 평가
│       ├── evolve.py     # 5가지 진화 전략
│       └── lint.py       # 6가지 구조 점검
└── README.md
```

---

## References

**Architecture**
- [Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) (2026.04)
- [LLM Wiki v2](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2)
- [LightRAG](https://arxiv.org/abs/2410.05779) (HKUDS, 2024)

**Knowledge Graph Evolution**
- [On the Evolution of Knowledge Graphs](https://arxiv.org/abs/2310.04835)
- [Iterative Retrieval in GraphRAG](https://arxiv.org/abs/2509.25530)
- [RAG Survey: Noise and Robustness](https://arxiv.org/abs/2506.00054)

**EVOLVE Foundations**
- [NoGE: Co-occurrence GNN for KG Link Prediction](https://arxiv.org/abs/2104.07396)
- [SMORE: KG Completion and Multi-hop Reasoning](https://arxiv.org/abs/2110.14890)
- [Practical GraphRAG: Hybrid Retrieval](https://arxiv.org/abs/2507.03226)
- [Self-Improving RAG for KG Construction](https://ojs.iscram.org/index.php/Proceedings/article/view/154)
