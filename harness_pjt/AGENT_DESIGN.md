# Evolving LightRAG — 에이전트 상세 설계서

> 이 문서는 쿼리가 들어오고 결과를 반환할 때까지 에이전트가 수행하는 모든 전략과 그래프 업데이트를 기술합니다.
> 코드 기준: `wikigraph/` 패키지 전체

---

## 구현 현황 요약

README에 언급된 모든 전략이 구현되어 있습니다.

| 전략 | 구현 위치 | 상태 |
|---|---|---|
| QSM 스코프 제한 | `adaptive_retriever.py` | ✅ 완전 구현 |
| DRG 보조 보완 | `adaptive_retriever.py` + `drg.py` | ✅ 완전 구현 |
| 온라인 DCSG 주입 | `adaptive_retriever.py` | ✅ 완전 구현 (비동기) |
| 문서 앵커 엔티티 생성 | `operations/evolve.py` `da_strategy()` | ✅ 완전 구현 |
| 문서 간 구조 연결 | `operations/evolve.py` `dcsg_strategy()` | ✅ 완전 구현 |
| 문서 횡단 엔티티 연결 | `operations/evolve.py` `cdrb_strategy()` | ✅ 완전 구현 |
| 공동 검색 관계 강화 | `operations/evolve.py` `evolve_node()` 전략 1 | ✅ 구현 (LangGraph 경로) |
| 추출 누락 보강 | `operations/evolve.py` `evolve_node()` 전략 2 | ✅ 구현 (llm_func 필요) |
| 단축 경로 생성 | `operations/evolve.py` `evolve_node()` 전략 3 | ✅ 구현 (LangGraph 경로) |
| 근거 상실 관계 제거 | `operations/evolve.py` `evolve_node()` 전략 4 | ✅ 완전 구현 |
| 모순 통합 | `operations/evolve.py` `evolve_node()` 전략 5 | ✅ 구현 (llm_func 필요) |
| 산출물 재삽입 | `agent.py` `ingest_artifact()` | ✅ 완전 구현 |
| 원본 문서 동기화 | `agent.py` `watch()` | ✅ 완전 구현 |
| LINT 전체 (6종) | `operations/lint.py` | ✅ 완전 구현 |

**주요 주의사항**: EVOLVE 경로가 두 가지입니다.
- **신규 Agentic 경로** (`agentic_query()`): DA + DCSG + CDRB + QSM + DRG
- **구형 LangGraph 경로** (`query()`): 전략 1~5 (co-retrieval, gap fill, shortcut, source verification, contradiction)

두 경로는 독립적으로 동작하며, 벤치마크에서는 agentic 경로가 주로 사용됩니다.

---

## 전체 흐름

```
사용자 쿼리 입력
    │
    ├── [경로 A] agent.agentic_query()  ←  현재 기본 경로 (벤치마크)
    │       │
    │       ├── 1. QSM 스코프 힌트 조회         (검색 전)
    │       ├── 2. LightRAG mix-mode 검색
    │       ├── 3. DRG 보조 문서 보완           (검색 후)
    │       ├── 4. QSM + DRG 업데이트           (학습)
    │       ├── 5. 온라인 DCSG 주입             (비동기, 조건부)
    │       └── 결과 반환
    │               │
    │               └── [N 쿼리마다] batch_evolve()  ←  백그라운드
    │                       ├── DA: 문서 앵커 엔티티 생성
    │                       ├── DCSG: 문서 간 구조 연결 주입
    │                       └── CDRB: 문서 횡단 엔티티 연결 주입
    │
    └── [경로 B] agent.query()          ←  구형 LangGraph 경로
            │
            ├── 1. query_node: mix-mode 검색 + 쿼리 로그 기록
            ├── 2. evaluate_node: 결과 품질 산출 (quality 0.0~1.0)
            └── 3. evolve_node: 품질 저하 또는 N번째 쿼리 시 EVOLVE
                    ├── 전략 1: 공동 검색 관계 강화
                    ├── 전략 2: 추출 누락 보강 (LLM)
                    ├── 전략 3: 단축 경로 생성
                    ├── 전략 4: 근거 상실 관계 제거
                    └── 전략 5: 모순 통합 (LLM)
```

---

## 경로 A: agentic_query() 상세

`agent.py:agentic_query()` → `adaptive_retriever.py:agentic_retrieve()`

### 단계 1 — QSM 스코프 힌트 조회 (검색 전)

```
쿼리 → qsm.get_scope_docs(query, min_count=2)
     → 관련 문서 목록 반환 (fingerprint/partial overlap/topic index 순)
```

QSM에 이 쿼리 패턴이 2회 이상 관찰됐으면 관련 문서 목록을 가져옵니다.
관련 문서가 있으면 쿼리 앞에 `DOC:파일명` 토큰을 최대 4개 붙입니다.

```
enhanced_query = "DOC:37_NVMe_Spec.html DOC:38_PCIe_Hub.html " + 원본쿼리
```

DA가 생성한 앵커 엔티티 덕분에, 이 `DOC:` 접두어가 entity VDB 검색 시 해당 문서의 앵커를 상위에 올립니다. 앵커 엔티티의 `source_id`는 해당 문서의 모든 청크 ID를 포함하므로, 앵커가 상위에 올라오면 그 문서의 청크들이 자동으로 검색 결과에 포함됩니다.

**그래프 업데이트**: 없음 (읽기 전용)

**코드**: `qsm.py:QueryStructuralMemory.get_scope_docs()`
- 1순위: fingerprint 정확 매칭 (count ≥ min_count)
- 2순위: fingerprint 부분 겹침 (공통 단어 3개 이상)
- 3순위: doc_topic_index 키워드 매칭 (2개 이상)

---

### 단계 2 — LightRAG mix-mode 검색

```
query_mix_mode(rag, enhanced_query, top_k=20)
```

LightRAG의 `mix` 모드를 실행합니다. 내부적으로:

1. **Entity VDB 검색**: 쿼리와 유사한 엔티티 상위 K개 선택
   - DA 앵커(`DOC:파일명`)가 있으면 스코프 힌트로 상위에 랭크될 가능성 높음
2. **Relation VDB 검색**: 쿼리와 유사한 릴레이션 상위 K개 선택
   - DCSG/CDRB/온라인 DCSG로 추가된 엣지도 여기서 검색됨
3. **KG 순회**: 찾은 엔티티/릴레이션에서 연결된 청크 수집
   - DCSG 엣지: `DOC:A → DOC:B` → B의 모든 청크 도달 가능
   - CDRB 엣지: `엔티티A → 엔티티B` → B의 원본 청크 도달 가능
4. **BM25 검색**: 키워드 매칭으로 추가 청크 수집
5. **RRF 통합**: 위 결과들을 순위 역수 합산으로 병합, 최종 top_k 선택

**그래프 업데이트**: 없음 (읽기 전용)

---

### 단계 3 — DRG 보조 문서 보완 (검색 후)

```
검색된 파일 목록 → drg.get_related_docs(retrieved_files, top_n=5, min_weight=0.3)
                → 관련 문서 목록 (가중치 높은 순)
                → 각 문서에서 drg_supplement_k개 청크 추가
```

이미 mix-mode가 반환한 결과에 없는 문서 중 DRG에서 연결 강도 0.3 이상인 문서를 찾아, 해당 문서의 상위 3개 청크를 결과에 추가합니다.

추가된 청크에는 메타데이터가 붙습니다:
```python
{
    **원본_청크_데이터,
    "_drg_supplement": True,
    "_drg_weight": 0.6,       # DRG 연결 강도
    "_drg_source": "38_PCIe_Hub.html",  # 어느 문서에서 왔는지
}
```

DRG에 아직 간선이 없거나(초기 상태), 연결 강도가 임계값 미만이면 보완 없이 진행합니다.

**그래프 업데이트**: 없음 (DRG 읽기 전용)

**코드**: `drg.py:DocRelationGraph.get_related_docs()`
- 가중치 집계 방식: `score(관련문서) = max(검색된_문서 중 연결된 간선의 가중치)`

---

### 단계 4 — QSM 업데이트 (학습)

```
qsm.update(query, retrieved_docs_list)
```

이번 쿼리에서 검색된 모든 문서(mix-mode + DRG 보완 포함)를 QSM에 기록합니다.

**내부 동작**:
1. 쿼리 fingerprint 계산 (정규화된 키워드 정렬)
2. `query_routing[fingerprint]` 에 문서 목록 병합, count 증가
3. 문서 쌍별 공동 검색 카운터(`cooc_pending`) 증가
   - 비코퍼스 파일 자동 필터 (`golden_code_plan.md`, `wikigraph_*` 등 제외)
4. 임계값 이상 쌓인 쌍은 DCSG 후보로 대기

**저장소**: `wikigraph_meta/qsm.json` (쿼리마다 메모리 업데이트, 주기적으로 디스크 저장)

**그래프 업데이트**: 없음 (QSM JSON만 갱신)

---

### 단계 5 — DRG 업데이트 (학습)

```
drg.update(retrieved_files_from_base_retrieval)
```

mix-mode 기본 검색에서 나온 문서들(보완 문서 제외)의 모든 쌍에 대해 DRG 간선 가중치를 증가시킵니다.

**가중치 업데이트 규칙**:
```
weight_new = min(1.0, weight_old + 0.1)
```
1회 공동 검색 → +0.1, 10회 → ≈ 1.0

**저장소**: `wikigraph_meta/drg.json`

**그래프 업데이트**: 없음 (DRG JSON만 갱신, KG 변경 없음)

---

### 단계 6 — 온라인 DCSG 주입 (비동기, 조건부)

```python
pending = drg_get_pending_online(drg, min_count=10)
if pending:
    asyncio.ensure_future(_inject_online_dcsg(rag, drg, qsm, pending[:20]))
```

DRG 간선 중 count가 `online_dcsg_threshold`(기본 10)을 넘고 아직 KG에 주입되지 않은 쌍을 찾아, **결과 반환을 막지 않고 백그라운드에서** KG에 엣지를 주입합니다.

**주입 내용**:
- KG에 `DOC:A ↔ DOC:B` 엣지 추가 (`weight=0.65`, `file_path="wikigraph_drg_online"`)
- Relation VDB에도 임베딩 추가 (다음 mix-mode 검색부터 반영)
- DRG에 `dcsg_injected=True` 표시 (중복 주입 방지)
- QSM `cooc_pending` → `cooc_confirmed` 이동

**그래프 업데이트**:
- KG: `DOC:A → DOC:B` 엣지 추가
- Relation VDB: 해당 엣지 임베딩 추가

**코드**: `adaptive_retriever.py:_inject_online_dcsg()`

---

### 단계 7 — 결과 반환

```python
return mix_results + supplement
```

`chunks` 리스트를 반환합니다. 각 청크는 딕셔너리이며 `_drg_supplement` 플래그로 보완 여부를 구분할 수 있습니다.

`agentic_query()`는 추가로 반환합니다:
```python
{
    "chunks": [...],
    "drg_supplement": 3,     # DRG가 보완한 청크 수
    "evolve_fired": False,   # 이번 쿼리로 batch_evolve가 실행됐는지
    "evolve_count": 2,       # 지금까지 batch_evolve 실행 횟수
    "queries_seen": 47,      # 지금까지 처리한 쿼리 수
}
```

---

### 단계 8 — batch_evolve (N 쿼리마다, 블로킹)

`agentic_query_buffer` 길이가 `evolve_every`(기본 50)의 배수가 될 때마다 `batch_evolve()`를 호출합니다. 이 단계는 결과 반환 전에 완료됩니다 (블로킹).

#### 8-1. 문서 앵커 엔티티 생성 (DA)

```
rag.text_chunks 전체 스캔
  → 파일명별 청크 ID 목록 구성
  → 각 파일에 DOC:파일명 엔티티 생성/갱신
  → entity VDB에 임베딩 추가
```

**건너뜀 조건**: 기존 DOC 엔티티가 있고 `[DOCUMENT_ANCHOR]` 마커를 포함하며 청크 목록이 최신이면 스킵.

**엔티티 설명 형식**:
```
"Document file: 37_NVMe_Spec.html. File name terms: NVMe Spec. Contains 4 chunk(s). [DOCUMENT_ANCHOR]"
```
파일명 파생 키워드만 사용하고 본문 발췌는 포함하지 않음 → 검색 노이즈 방지.

**그래프 업데이트**:
- KG: `DOC:파일명` 노드 upsert (`entity_type="DOCUMENT"`)
- Entity VDB: 각 DOC 엔티티 임베딩 추가

**코드**: `evolve.py:da_strategy()`

---

#### 8-2. 문서 간 구조 연결 주입 (DCSG)

```
questions 배치 → 각 쿼리에 mix-mode + BM25 검색 실행 (evolve_k=50)
             → 쿼리별 공동 검색된 문서 쌍 카운팅
             → count ≥ co_occur_min(2) 인 쌍 선택 → 최대 max_edges/2개
             → 기존에 없는 DOC:A ↔ DOC:B 엣지 생성
```

**엣지 속성**:
```python
{
    "weight": 0.7,
    "description": "Documents co-retrieved in 5 queries. Shared query topics: NVMe, PCIe",
    "keywords": "document-structure,dcsg-evolve,NVMe,PCIe",
    "source_id": "<DA엔티티_청크ID들을 SEP로 연결>",
    "file_path": "wikigraph_dcsg",
}
```

**source_id 설계**: 두 DOC 엔티티의 source_id(= 각 문서의 모든 청크 ID)를 합쳐서 씁니다. 이렇게 하면 DCSG 엣지를 통한 KG 순회 시 두 문서의 청크가 모두 검색 후보가 됩니다.

**그래프 업데이트**:
- KG: `DOC:A ↔ DOC:B` 엣지 upsert (배치)
- Relation VDB: 해당 엣지 임베딩 추가

**코드**: `evolve.py:dcsg_strategy()`

---

#### 8-3. 문서 횡단 엔티티 연결 주입 (CDRB)

```
questions 배치 → 각 쿼리에 mix-mode + BM25 검색 실행 (evolve_k=50)
             → 청크 → 엔티티 역색인으로 "쿼리당 문서별 엔티티 집합" 구성
             → 서로 다른 문서 소속 엔티티 쌍 카운팅
             → specificity 가중치 산출: score = count / (doc_cnt_A × doc_cnt_B)
             → 상위 max_edges/2개 선택 → KG 엣지 생성
```

**specificity 가중치**: 여러 문서에 걸쳐 등장하는 허브 엔티티 쌍보다, 특정 문서에만 등장하는 엔티티 쌍을 우선 선택합니다.

**엣지 속성**:
```python
{
    "weight": 0.6,
    "description": "EntityA and EntityB co-retrieved in 3 queries (37_NVMe_Spec.html ↔ 38_PCIe_Hub.html)",
    "keywords": "cross-document,cdrb-evolve",
    "source_id": "<두 엔티티의 source_id 합집합>",
    "file_path": "wikigraph_cdrb",
}
```

**그래프 업데이트**:
- KG: `엔티티A ↔ 엔티티B` 엣지 upsert (배치)
- Relation VDB: 해당 엣지 임베딩 추가

**코드**: `evolve.py:cdrb_strategy()`

---

#### 8-4. DRG 청크 인덱스 갱신

```
drg.index_doc_chunks(rag)   # text_chunks 스캔 → doc_index 재구성
drg.save()                  # 디스크에 저장
```

DA로 새 DOC 엔티티가 추가됐을 수 있으므로, batch_evolve 후 DRG의 `doc_index`를 새로 빌드합니다.

**그래프 업데이트**: 없음 (DRG JSON만 갱신)

---

## 경로 B: query() LangGraph 경로 상세

`agent.py:query()` → `operations/query.py:query_node()` → `evaluate_node()` → 조건부 `evolve_node()`

이 경로는 구형 LangGraph StateGraph를 통합니다. 아직 삭제되지 않았으며 `agent.query()`로 호출 가능합니다.

### query_node

```python
result = await rag.aquery_data(query, param=QueryParam(mode="mix"))
```

QSM/DRG 없이 순수 LightRAG mix-mode를 실행합니다.

검색 결과에서:
- 검색된 엔티티 이름 → `entity_metadata`의 `access_count` 증가
- 검색된 엔티티/릴레이션/청크 → `query_log`에 기록

**그래프 업데이트**: 없음

---

### evaluate_node

LLM 호출 없이 결과 품질을 0.0~1.0 점수로 산출합니다.

| 결과 상태 | quality |
|---|---|
| 엔티티 0 + 청크 0 | 0.0 |
| 엔티티 있으나 릴레이션 0 | 0.3 |
| 청크만 있고 엔티티 0 | 0.4 |
| 엔티티 + 릴레이션 + 청크 | 0.5 + 0.1×엔티티수 + 0.05×릴레이션수 (최대 1.0) |

`quality < 0.5` 또는 N번째 쿼리(`auto_evolve_interval`, 기본 5)이면 `evolve_node` 실행.

---

### evolve_node — 전략 1: 공동 검색 관계 강화

```
query_log에서 3회+ 함께 검색된 엔티티 쌍 탐지
  → KG에 직접 연결 없으면
  → (llm_func 있으면) LLM에게 관계 추론 요청
  → ainsert_custom_kg()로 엣지 주입
```

**엣지 속성**: `weight=0.5`, `source_id="wikigraph_evolve"`, `keywords="co-retrieved"`

**그래프 업데이트**: KG에 엔티티 간 새 엣지 추가

---

### evolve_node — 전략 2: 추출 누락 보강

```
query_log에서 "청크는 있으나 엔티티가 없는" 쿼리 탐지
  → 해당 청크 텍스트 최대 3개 → LLM에 전달
  → ENTITY: 이름|타입|설명 / RELATIONSHIP: src|tgt|설명 파싱
  → ainsert_custom_kg()로 주입
```

**필요 조건**: `llm_func` 필수. 없으면 로그만 남기고 건너뜀.

**그래프 업데이트**: KG에 새 엔티티 노드 및/또는 엣지 추가

---

### evolve_node — 전략 3: 단축 경로 생성

```
query_log에서 A→B→C 패턴이 3회+ 등장하고 A↔C 직접 연결 없는 쌍 탐지
  → edge_AB + edge_BC의 description 합성
  → ainsert_custom_kg()로 A→C 엣지 주입
```

**엣지 속성**: `weight=0.5`, `keywords="shortcut,via_B"`, `source_id="wikigraph_evolve"`

**그래프 업데이트**: KG에 단축 엣지 추가

---

### evolve_node — 전략 4: 근거 상실 관계 제거

```
KG의 모든 엣지 순회
  → source_id에서 청크 ID 파싱
  → text_chunks에 해당 청크가 없으면 (문서 삭제/변경)
  → graph.remove_edges() 호출
  → wikigraph_evolve 출처 엣지는 제외 (원래 source chunk 없음)
```

**그래프 업데이트**: KG에서 고아 엣지 제거

---

### evolve_node — 전략 5: 모순 통합

```
KG 노드 스캔 (최대 50개)
  → description에 <SEP> 구분자로 여러 설명이 병합된 노드 탐지
  → LLM에게 통합 설명 요청
  → graph.upsert_node()로 노드 설명 갱신
```

**필요 조건**: `llm_func` 필수. 한 번의 evolve에서 최대 3개 노드 처리.

**그래프 업데이트**: KG 노드 description 수정

---

## LINT 상세

`agent.lint()` → `operations/lint.py:lint_node()`

수동 호출 또는 주기적으로 실행. 대부분의 점검이 LLM 없이 그래프 알고리즘으로 수행됩니다.

### 점검 1: 고아 노드 탐지

```
모든 노드 순회 → node_degree() == 0 인 노드 리포트
```
자동 삭제 안 함. 새로 삽입된 문서의 정상 엔티티일 수 있음.

### 점검 2: 허브 과부하 탐지

```
모든 노드 순회 → node_degree() > 50(기본값) 인 노드 리포트
```
예: "LightRAG" 노드에 릴레이션 100개 → 어떤 쿼리에도 나와 변별력 저하. 수동 판단.

### 점검 3: 중복 엔티티 탐지

```
공통 토큰이 있는 노드 쌍만 후보 추출 (O(N²) 방지)
  → 후보 임베딩 계산 → 코사인 유사도 > 0.95 인 쌍 리포트
```
예: "BUN" / "Bun Runtime", "React" / "React.js". 수동 판단.

### 점검 4: 방치 엔티티 탐지

```
entity_metadata에서 access_count < 2 이면서
20번(기본) 이상의 쿼리 동안 한 번도 검색되지 않은 엔티티 리포트
```
리포트만. 자동 삭제 안 함.

### 점검 5: 모순 탐지

```
description에 <SEP> 구분자가 있는 노드 탐지 → 모순 가능성 리포트
```
선택적으로 LLM에 판단 요청 가능.

### 점검 6: 추론 엣지 자동 정리 (유일한 자동 수정)

```
조건: source_id에 "wikigraph_evolve" 포함
    AND weight ≤ 0.5
    AND 양쪽 엔티티 access_count 합 == 0
→ graph.remove_edges() 자동 실행
```

추론으로 생성됐지만 한 번도 검색에 활용되지 않은 엣지를 정리합니다.
원본 문서 기반 엣지(`weight ≥ 1.0`)는 절대 자동 삭제되지 않습니다.

**그래프 업데이트**: 조건을 만족하는 추론 엣지 자동 제거

---

## 기타 진입점

### agent.ingest_artifact()

```python
await agent.ingest_artifact(answer_text, label="architecture_summary")
```

LLM이 생성한 답변, 요약 등을 LightRAG 파이프라인에 재삽입합니다.
`rag.ainsert()` → 청킹 → LLM 엔티티/릴레이션 추출 → KG에 추가.

**그래프 업데이트**: KG에 새 노드/엣지 추가 (일반 문서 삽입과 동일)

### agent.watch()

```python
result = await agent.watch("./docs/")
```

디렉토리의 파일을 content hash로 비교하여 변경분만 재삽입.
`detect_changed_files()` → 변경 파일만 `agent.ingest()` → 기존 엔티티 description 병합.

**그래프 업데이트**: 변경된 파일의 청크 → 새 엔티티/릴레이션 추가 또는 기존 노드에 description 병합

---

## 데이터 저장소

| 저장소 | 경로 | 내용 | 업데이트 시점 |
|---|---|---|---|
| KG (그래프) | `rag_data/graph_chunk_entity_relation.graphml` | 엔티티 노드 + 릴레이션 엣지 | INGEST, EVOLVE, 온라인 DCSG |
| Entity VDB | `rag_data/vdb_entities.json` | 엔티티 임베딩 | INGEST, DA |
| Relation VDB | `rag_data/vdb_relations.json` | 릴레이션 임베딩 | INGEST, DCSG, CDRB, 온라인 DCSG |
| Chunk VDB | `rag_data/vdb_chunks.json` | 청크 임베딩 | INGEST |
| BM25 인덱스 | `rag_data/bm25_index.pkl` | 엔티티/릴레이션/청크 전문 인덱스 | INGEST 시 점진적 추가 |
| text_chunks | `rag_data/kv_store_text_chunks.json` | 원본 청크 텍스트 | INGEST |
| QSM | `wikigraph_meta/qsm.json` | 쿼리 fingerprint → 문서 목록, 공동 검색 카운터 | 매 agentic_retrieve 후 |
| DRG | `wikigraph_meta/drg.json` | 문서 간 공동 검색 가중치, 청크 인덱스 | 매 agentic_retrieve 후 |
| 쿼리 로그 | `wikigraph_meta/query_log.json` | 쿼리별 검색 결과 기록 | 매 query() 후 |
| 엔티티 메타 | `wikigraph_meta/entity_metadata.json` | 엔티티별 access_count, last_accessed | 매 query() 후 |

---

## 전략별 트리거 조건 요약

| 전략 | 트리거 | 경로 |
|---|---|---|
| QSM 스코프 | 매 agentic_query | Agentic (실시간) |
| DRG 보완 | 매 agentic_query (DRG 간선이 있을 때) | Agentic (실시간) |
| QSM+DRG 학습 | 매 agentic_query | Agentic (실시간) |
| 온라인 DCSG | DRG 간선 count ≥ 10 (비동기) | Agentic (실시간, 비동기) |
| DA | batch_evolve 실행 시 (매 50쿼리) | Agentic (배치) |
| DCSG | batch_evolve 실행 시 (매 50쿼리) | Agentic (배치) |
| CDRB | batch_evolve 실행 시 (매 50쿼리) | Agentic (배치) |
| 공동 검색 관계 강화 | evolve_node 실행 시 | LangGraph (quality < 0.5 or N번째 쿼리) |
| 추출 누락 보강 | evolve_node 실행 시 (llm_func 필요) | LangGraph |
| 단축 경로 생성 | evolve_node 실행 시 | LangGraph |
| 근거 상실 관계 제거 | evolve_node 실행 시 | LangGraph |
| 모순 통합 | evolve_node 실행 시 (llm_func 필요) | LangGraph |
| 산출물 재삽입 | 수동 호출 | 명시적 |
| 원본 문서 동기화 | 수동 호출 또는 파일 감시 | 명시적 |
| LINT | 수동 호출 | 명시적 |

---

## 골든 LightRAG 보호

벤치마크 평가는 항상 골든 LightRAG에서 시작합니다.

```
harness_pjt/rag_data/           ← 골든 (불변, 절대 수정 금지)
evaluation/scenario2-ver4/rag_data/ ← 작업용 복사본 (여기서 EVOLVE)
```

`agentic_eval.py --fresh` 실행 시: 골든을 복사 → QSM/DRG 초기화 → 새 학습 시작.
연속 실행 시: 이전 QSM/DRG 유지 → 쌓인 학습 상태에서 시작.
