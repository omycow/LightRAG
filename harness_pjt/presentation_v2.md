# Living LightRAG: WikiGraph Agent
## LightRAG + LLM Wiki 철학의 결합

---

# Part 1: LightRAG Hybrid Search

---

## 1. 문제: 벡터 전용 검색의 한계

LightRAG의 검색 파이프라인은 **전적으로 벡터 유사도 검색에만 의존**합니다:

| 검색 경로 | 방식 | 한계 |
|---|---|---|
| 청크 검색 | 코사인 유사도 | 고유명사 놓침 |
| 엔티티 검색 | 코사인 유사도 | 짧은 쿼리에 약함 |
| 관계 검색 | 코사인 유사도 | 정확한 키워드 매칭 불가 |

---

## 2. 해결: BM25 하이브리드 검색

각 검색 경로에 **BM25 키워드 검색**을 추가하고 **RRF**로 병합:

```
Query
  ├── Vector Search (의미 유사도) ──┐
  │                                 ├── RRF 병합 → 최종 순위
  └── BM25 Search (키워드 매칭)  ──┘
```

### RRF (Reciprocal Rank Fusion)

```
RRF(d) = SUM 1/(k + rank_r(d)),  k=60
```

양쪽에서 모두 검색된 문서가 더 높은 순위를 받음.

---

## 3. 구현 아키텍처

| 파일 | 변경 |
|---|---|
| `lightrag/bm25_index.py` | 신규 — BM25Index + RRF + 증분 업데이트(`add()`) + 디스크 저장(`save()/load()`) |
| `lightrag/lightrag.py` | BM25 빌드/로드, 점진적 업데이트 콜백, global_config 주입 |
| `lightrag/operate.py` | 3개 검색 함수에 하이브리드 모드 추가 |
| `lightrag/pipeline.py` | chunks VDB upsert 후 BM25 즉시 업데이트 |

### 핵심: 점진적 BM25 인덱싱

기존: `_insert_done()` 시점에 전체 재빌드
개선: **각 VDB upsert 시점마다 즉시 업데이트**

```
청크 저장 → [BM25 Chunks 즉시 업데이트]
엔티티 저장 → [BM25 Entities 즉시 업데이트]
릴레이션 저장 → [BM25 Relations 즉시 업데이트]
```

---

## 4. 기대 효과

| 쿼리 유형 | Vector Only | Hybrid |
|---|---|---|
| 고유명사 ("GPT-4o") | 임베딩 근사 매칭 | BM25 정확 매칭 보완 |
| 짧은 쿼리 (1-2 단어) | 품질 저하 | BM25 IDF 효과적 |
| 긴 서술형 쿼리 | 효과적 | 벡터 위주 + BM25 보조 |

---

# Part 2: LLM Wiki — 철학과 한계

---

## 5. Karpathy의 LLM Wiki (2026.04)

> "Stop re-deriving, start compiling." — 매번 다시 찾지 말고, 한번 정리해서 축적하라.

### 3계층 아키텍처

| 계층 | 역할 |
|---|---|
| **Raw Sources** | 원본 문서 (불변) |
| **Wiki** | LLM이 유지보수하는 마크다운 페이지 |
| **Schema** | 구조 규칙 (CLAUDE.md) |

### 3가지 동작

| 동작 | 설명 |
|---|---|
| **INGEST** | 새 문서 → LLM이 기존 wiki 10-15개 페이지 업데이트 |
| **QUERY** | wiki에서 검색 → 답변 → 좋은 답변은 wiki에 편입 |
| **LINT** | 주기적 건강검진 — 모순, 고아, 갭 탐지 |

---

## 6. LLM Wiki의 한계 (v2 포함)

| # | 한계 | 원인 |
|---|---|---|
| 1 | **오류 전파** | LLM 합성 오류가 모든 답변에 영구적으로 남음 |
| 2 | **세션 리셋** | 매 세션마다 wiki를 처음부터 다시 로딩 |
| 3 | **스케일** | ~1000페이지에서 한계 (v2에서 검색 추가했지만 근본적 한계) |
| 4 | **멀티홉 불가** | "A→B→C" 추적이 페이지 기반으로는 어려움 |
| 5 | **관계 의미 부재** | `[[위키링크]]`는 "연결됨"만 표현, 타입/가중치 없음 |
| 6 | **품질 저하 무감지** | 합성 오류가 쌓여도 탐지 메커니즘 부재 |
| 7 | **업데이트 비용** | 새 문서 1개 → 15페이지 재작성 필요 |

---

## 7. LLM Wiki의 장점 (LightRAG에 없는 것)

| 장점 | LightRAG 현재 상태 |
|---|---|
| 지식이 **진화**함 | 정적 — insert 후 변화 없음 |
| **Confidence** 점수 | 없음 |
| **LINT** — 건강검진 | 없음 |
| 쿼리 결과 → 지식 **순환** | 없음 |
| 다양한 소스 **쉬운 추가** | ainsert()만 지원 |

---

# Part 3: WikiGraph Agent

---

## 8. 핵심 아이디어

> **LLM Wiki의 "지식이 자라는 사이클"을, LightRAG의 그래프 엔진 위에서 돌린다.**

| | LLM Wiki | LightRAG | **WikiGraph Agent** |
|---|---|---|---|
| 저장소 | 마크다운 파일 | 그래프 + VDB | 그래프 + VDB + BM25 |
| 업데이트 | 페이지 통째 재작성 | 노드/엣지 증분 | **노드/엣지 증분 + 자동 진화** |
| 검색 | 풀컨텍스트 로딩 | 그래프 순회 + 벡터 | 그래프 + 벡터 + BM25 |
| 스케일 | ~1000페이지 | 수만 노드 | 수만 노드 |
| 오류 처리 | 전파됨 | 원본 보존 | 원본 보존 + **검증** |
| 지식 진화 | ✅ | ❌ | ✅ |
| 건강검진 | LLM 전체 스캔 | ❌ | **그래프 알고리즘** |

---

## 9. 아키텍처

```
┌─────────────────────────────────────────────┐
│           WikiGraph Agent (LangGraph)        │
│                                             │
│  ┌─────────┐  ┌─────────┐  ┌─────────────┐ │
│  │ INGEST  │  │  QUERY  │  │    LINT     │ │
│  │ URL/파일 │  │ 그래프   │  │ 고아/중복/  │ │
│  │ 자동수집 │  │ 즉시검색 │  │ 모순 탐지  │ │
│  └────┬────┘  └────┬────┘  └─────────────┘ │
│       │            │                        │
│       ▼            ▼                        │
│  rag.ainsert()  rag.aquery_data()           │
│       │            │                        │
│       │       ┌────▼────┐                   │
│       │       │EVALUATE │                   │
│       │       │품질 판단 │                   │
│       │       └────┬────┘                   │
│       │            │ (품질 낮음)             │
│       │       ┌────▼────┐                   │
│       │       │ EVOLVE  │                   │
│       │       │ 관계 강화│                   │
│       │       │ 갭 채우기│                   │
│       │       │ 단축경로 │                   │
│       │       └────┬────┘                   │
│       │            │                        │
│       ▼            ▼                        │
│  ┌──────────────────────────────────────┐   │
│  │         LightRAG Engine             │   │
│  │  Knowledge Graph + VDB + BM25       │   │
│  │  ainsert_custom_kg() 으로 증분 주입  │   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

---

## 10. INGEST — 쉬운 문서 추가

LLM Wiki의 장점 도입: 다양한 소스를 쉽게 추가

| 소스 | 방식 |
|---|---|
| URL | 웹페이지 크롤링 → 마크다운 → `rag.ainsert()` |
| 파일/디렉토리 | 새 파일 감지 → 자동 ingest |
| 텍스트 | "이것도 기억해" → 즉시 그래프에 추가 |
| 업데이트 | content hash 비교 → 변경분만 재처리 |

### LLM Wiki 대비 장점

```
LLM Wiki:  새 문서 → LLM이 기존 wiki 전체 읽고 → 15페이지 재작성
WikiGraph: 새 문서 → rag.ainsert() → 새 노드/엣지만 추가 (기존 그래프 보존)
```

---

## 11. EVOLVE — 지식 진화 (핵심 차별점)

### 전략 1: 공동 검색 관계 강화

```
쿼리 로그 분석:
  Query 1: [A, B, C] 검색됨
  Query 2: [A, B, D] 검색됨
  Query 3: [A, B, E] 검색됨
  → A와 B가 3회 공동 검색 → 직접 관계 없으면 LLM이 추론하여 생성
```

### 전략 2: 지식 갭 채우기

```
Query: "X와 Y의 관계는?"
  → 엔티티 0개 반환 (quality=0.0)
  → EVOLVE 트리거
  → LLM에게: "X와 Y에 대해 추론할 수 있는 관계를 제안해줘"
  → ainsert_custom_kg()으로 주입
  → 재쿼리 시 결과 개선
```

### 전략 3: 멀티홉 단축 경로

```
자주 사용되는 경로: A → B → C
  → A → C 직접 관계 자동 생성
  → 다음 쿼리에서 즉시 접근 가능
```

---

## 12. LINT — 그래프 건강검진

LLM Wiki: LLM이 전체 wiki를 스캔 (수만 토큰 비용)
WikiGraph: **그래프 알고리즘으로 0토큰** 탐지

| 검사 | 알고리즘 | LLM 비용 |
|---|---|---|
| 고아 노드 | `node_degree() == 0` | 0 |
| 허브 과부하 | `node_degree() > 50` | 0 |
| 중복 엔티티 | 벡터 유사도 > 0.95 | 0 |
| 방치 엔티티 | 쿼리 로그 미접근 | 0 |
| 모순 탐지 | 다중 description 비교 | 선택적 |

---

## 13. LangGraph 상태 머신

```
ROUTER ─┬── INGEST ──────────────────→ (완료)
        │
        ├── QUERY → EVALUATE ─┬─ OK → (완료)
        │                     │
        │                     └─ 낮음 → EVOLVE → (완료)
        │
        └── LINT ────────────────────→ (완료)
```

상태 영속화: 쿼리 로그 + 엔티티 메타데이터 → JSON 파일 (working_dir 내)

---

## 14. 기술 스택

| 컴포넌트 | 기술 |
|---|---|
| 오케스트레이션 | LangGraph StateGraph |
| 지식 엔진 | LightRAG (Graph + VDB + BM25) |
| 임베딩 | sentence-transformers (로컬 CPU) |
| LLM | Qwen 35B (원격 서버) |
| 검색 | Vector + BM25 + Graph + RRF |

---

# Part 4: 비교 분석

---

## 15. RAG vs LLM Wiki vs WikiGraph Agent

| 차원 | Traditional RAG | LLM Wiki v2 | WikiGraph Agent |
|---|---|---|---|
| **지식 표현** | 벡터 청크 | 마크다운 페이지 | 그래프 노드/엣지 |
| **지식 진화** | ❌ 정적 | ✅ 페이지 재작성 | ✅ 증분 업데이트 |
| **스케일** | ✅ 수백만 문서 | ❌ ~1000페이지 | ✅ 수만 노드 |
| **멀티홉** | ❌ | ❌ | ✅ 그래프 순회 |
| **오류 검증** | ✅ 원본 재참조 | ❌ 오류 전파 | ✅ 원본 보존 |
| **업데이트 비용** | 낮음 | 높음 (15페이지 재작성) | **최소 (증분)** |
| **검색 효율** | 벡터만 | 하이브리드 | 벡터+BM25+그래프 |
| **건강검진** | ❌ | LLM 전체 스캔 | **그래프 알고리즘** |
| **토큰 비용** | 쿼리마다 검색 | 전체 로딩 | **필요한 노드만** |

---

## 16. 데모 시나리오

```
Step 1: INGEST — 문서 3개 삽입, 그래프 생성
Step 2: QUERY — 5개 질문, 쿼리 로그 축적
Step 3: EVOLVE — 로그 분석 → 새 관계 3개 자동 생성
Step 4: QUERY — 같은 질문 재실행 → 답변 품질 개선 확인
Step 5: LINT — 고아 노드 2개 탐지, 중복 엔티티 1쌍 발견
```

---

## 17. 향후 방향

1. **자동 소스 수집** — RSS, API 연동으로 지식 자동 성장
2. **Confidence Decay** — 시간에 따른 지식 신뢰도 감쇠
3. **커뮤니티 기반 요약** — 그래프 클러스터 자동 요약 노드 생성
4. **멀티 에이전트** — 도메인별 전문 에이전트 협업
5. **실시간 모순 탐지** — ingest 시점에 기존 그래프와 충돌 검사

---

## 18. 토큰 비용 비교 (예상)

| 시나리오 (문서 100개 기준) | LLM Wiki v2 | WikiGraph Agent |
|---|---|---|
| **INGEST 1건** | ~50K tokens (wiki 전체 읽기 + 15페이지 재작성) | ~5K tokens (엔티티 추출만) |
| **QUERY 1건** | ~10K tokens (관련 페이지 전문 로딩) | ~2K tokens (그래프 노드 정보만) |
| **LINT** | ~100K tokens (전체 wiki 스캔) | **0 tokens** (그래프 알고리즘) |
| **100 QUERY 총비용** | ~1M tokens | ~200K tokens |

---

## 19. 구현 현황

| 컴포넌트 | 상태 | 상세 |
|---|---|---|
| BM25 하이브리드 검색 | ✅ 구현 완료 | `bm25_index.py`, 점진적 업데이트, 디스크 저장 |
| WikiGraph INGEST | ✅ 구현 완료 | `rag.ainsert()` 래핑 + 검증 |
| WikiGraph QUERY + EVALUATE | ✅ 구현 완료 | `rag.aquery_data()` + 휴리스틱 품질 판단 |
| WikiGraph EVOLVE | ✅ 구현 완료 | 3가지 전략 (공동검색/갭채우기/단축경로) |
| WikiGraph LINT | ✅ 구현 완료 | 5가지 건강검진 (그래프 알고리즘) |
| LangGraph 상태 머신 | ✅ 구현 완료 | StateGraph + 조건부 분기 |
| 메타데이터 영속화 | ✅ 구현 완료 | JSON 파일 기반 쿼리 로그/엔티티 메타 |
| URL 크롤링 소스 수집 | 🔄 기본 구조 | `sources.py` (파일 감시, 변경 감지) |
| Confidence Decay | 📋 향후 | 시간 기반 신뢰도 감쇠 |

---

## 20. 결론

> **WikiGraph Agent = LLM Wiki의 철학 + LightRAG의 엔진**

- LLM Wiki에서 가져온 것: 지식이 자라는 사이클 (INGEST → QUERY → EVOLVE → LINT)
- LightRAG가 제공하는 것: 스케일러블한 그래프 검색 + 증분 업데이트 + 원본 보존
- 새로 만드는 것: 쿼리 패턴 기반 자동 지식 진화 (EVOLVE)

```
LLM Wiki:   지식은 자라야 한다   + 스케일 한계
LightRAG:   효율적인 그래프 검색  + 정적 지식
                    ↓
WikiGraph Agent:  스케일러블하게 자라는 지식 그래프
```

---

## References

- [Karpathy's LLM Wiki Gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) (2026.04)
- [LLM Wiki v2 Extension](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2)
- [LightRAG Paper](https://arxiv.org/abs/2410.05779) (HKUDS, 2024)
- [What Karpathy's LLM Wiki Is Missing](https://dev.to/penfieldlabs/what-karpathys-llm-wiki-is-missing-and-how-to-fix-it-1988)
- [RAG vs Agent Memory vs LLM Wiki](https://dev.to/vishalmysore/rag-vs-agent-memory-vs-llm-wiki-a-practical-comparison-1oo6)
- [Did LLM Wiki Kill RAG?](https://www.epsilla.com/blogs/llm-wiki-kills-rag-karpathy-enterprise-semantic-graph)
