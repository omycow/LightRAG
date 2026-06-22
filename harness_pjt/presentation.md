# LightRAG Hybrid Search 개선
## Vector + BM25 키워드 검색 통합

---

## 1. 문제: 벡터 전용 검색의 한계

LightRAG의 검색 파이프라인은 **전적으로 벡터 유사도 검색에만 의존**합니다:

| 검색 경로 | 함수 | 방식 |
|---|---|---|
| 청크 검색 | `chunks_vdb.query()` | 코사인 유사도 |
| 엔티티 검색 | `entities_vdb.query()` | 코사인 유사도 |
| 관계 검색 | `relationships_vdb.query()` | 코사인 유사도 |

### 한계점

- **고유명사**: "GPT-4o", "HKUDS" - 임베딩 근사 매칭으로 정확한 결과를 놓칠 수 있음
- **기술 용어**: 의미가 유사한 다른 문서와 혼동 가능
- **짧은 쿼리 (1-2 단어)**: 임베딩 품질이 급격히 저하
- **정확한 키워드 매칭**: 쿼리와 동일한 단어를 포함한 문서를 놓칠 수 있음

---

## 2. Before: 벡터 전용 파이프라인

![Before: 벡터 전용 파이프라인](images/before_vector_only.png)

3개 검색 경로(엔티티, 관계, 청크) **모두** 벡터 유사도만 사용.
키워드 매칭 경로가 전혀 없음.

---

## 3. After: 하이브리드 검색 파이프라인

![After: 하이브리드 검색 파이프라인](images/after_hybrid_search.png)

각 검색 경로에 **이중 검색**이 추가됨:
- **파란색**: 벡터 유사도 검색 (기존)
- **주황색**: BM25 키워드 검색 (신규)

결과는 **RRF (Reciprocal Rank Fusion)**로 병합됨.

---

## 4. RRF (Reciprocal Rank Fusion)

![RRF Fusion](images/rrf_fusion.png)

### 수식

```
RRF(d) = SUM  1 / (k + rank_r(d))      k=60
```

### 왜 RRF인가?

- 벡터와 BM25의 점수 스케일이 **완전히 다름**
  - 벡터: 코사인 유사도 0~1
  - BM25: TF-IDF 가중 점수 0~100+
- RRF는 **순위만 사용** → 점수 정규화 불필요
- 업계 표준: OpenSearch, Elasticsearch에서 사용

### 예시

| 문서 | Vector 순위 | BM25 순위 | RRF 점수 |
|---|---|---|---|
| Doc_A | #1 | #3 | 1/61 + 1/63 = **0.0323** |
| Doc_C | #3 | #1 | 1/63 + 1/61 = **0.0323** |
| Doc_B | #2 | - | 1/62 = 0.0161 |
| Doc_F | - | #2 | 1/62 = 0.0161 |

**양쪽에서 모두 검색된 문서**가 더 높은 순위를 받음.

---

## 5. 구현 아키텍처

### 신규 모듈: `lightrag/bm25_index.py`

```python
class BM25Index:
    """인메모리 BM25 키워드 검색 인덱스"""
    
    def build(self, documents: dict[str, str]) -> None:
        """문서 {id: text} 매핑으로 인덱스 빌드"""
        
    def query(self, query_text: str, top_k: int = 20) -> list[dict]:
        """BM25 검색 -> [{id, score}, ...]"""

def reciprocal_rank_fusion(
    vector_results, bm25_results, k=60
) -> list[dict]:
    """RRF로 벡터 + BM25 결과 병합"""
```

### 수정 파일 목록

| 파일 | 변경 내용 |
|---|---|
| `lightrag/bm25_index.py` | 신규 - BM25Index 클래스 + RRF 함수 |
| `lightrag/lightrag.py` | BM25 인덱스 빌드, lazy rebuild, global_config 주입 |
| `lightrag/operate.py` | `_get_node_data`, `_get_edge_data`, `_get_vector_context`에 하이브리드 검색 추가 |
| `lightrag/addon_params.py` | `enable_hybrid_search` 기본 파라미터 추가 |
| `pyproject.toml` | `rank-bm25` 의존성 추가 |

---

## 6. BM25 인덱스 라이프사이클

### 빌드 단계 (`initialize_storages()` 시점)

```
text_chunks (KV)        --> BM25 청크 인덱스
entities_vdb (VDB)      --> BM25 엔티티 인덱스  
relationships_vdb (VDB) --> BM25 관계 인덱스
```

### Lazy Invalidation (지연 무효화)

```
문서 삽입 --> _insert_done() --> _bm25_stale = True
다음 쿼리 --> aquery_llm()  --> stale이면: 인덱스 재빌드
```

### 활성화 방법

```python
rag = LightRAG(
    addon_params={"enable_hybrid_search": True},  # 기본값: OFF
    ...
)
```

---

## 7. 검색 함수 변경 사항

### 엔티티 검색 (`_get_node_data`)

```python
# 기존: 벡터 검색
results = await entities_vdb.query(query, top_k=...)

# 신규: BM25 하이브리드
if entities_bm25 and entities_bm25.is_built:
    bm25_results = entities_bm25.query(query, top_k=...)
    results = reciprocal_rank_fusion(results, bm25_results)
```

### 관계 검색 (`_get_edge_data`) - 동일 패턴

### 청크 검색 (`_get_vector_context`) - 동일 패턴

모든 수정은 **`enable_hybrid_search` 플래그로 제어**됨.
기본 동작은 **변경 없음** (하위 호환성 보장).

---

## 8. 기대 효과

| 쿼리 유형 | Vector Only | Hybrid (Vector + BM25) |
|---|---|---|
| 고유명사 ("GPT-4o") | 임베딩 근사 매칭 | BM25 정확 매칭 보완 |
| 기술 용어 | 유사 의미 문서 혼동 | 정확 토큰 포함 문서 우선 |
| 짧은 쿼리 (1-2 단어) | 임베딩 품질 저하 | BM25 IDF 가중치 효과적 |
| 긴 서술형 쿼리 | 효과적 | 벡터 위주 + BM25 보조 |

**예상 Recall@K 향상: 15-20%** (특히 키워드 매칭이 중요한 쿼리)

---

## 9. 설정 방법

```python
# 하이브리드 검색 활성화
rag = LightRAG(
    working_dir="./my_rag",
    llm_model_func=your_llm_func,
    embedding_func=EmbeddingFunc(...),
    addon_params={
        "enable_hybrid_search": True,
    }
)

# 쿼리 - 모든 모드에서 동작
result = await rag.aquery("GPT-4o란?", param=QueryParam(mode="hybrid"))
```

### 의존성

```toml
# pyproject.toml
"rank-bm25>=0.2.2,<1.0.0"
```

---

## 10. 데모

`demo_hybrid_search.ipynb` 참조:
- LightRAG 인스턴스 생성 (Vector Only / Hybrid)
- 동일 지식그래프 삽입
- BM25 인덱스 빌드 및 직접 검증
- Vector Only vs Hybrid 쿼리 결과 비교
- RRF 점수 계산 시각화
