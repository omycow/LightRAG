# StructRAG 이식 가이드 — 다른 코퍼스/벤치마크에서 돌리는 법

> 대상: v5.34 (플래그십, 태그 `structrag-v5.34`). v5.20(저지연)·v5.14(정밀)도 절차 동일.
> 원칙: 그라운드룰 6(골든 읽기 전용)·7(무개입 검증)은 코퍼스가 바뀌어도 그대로 적용된다.

## 0. 시스템이 요구하는 것 (딱 3가지)

| 준비물 | 형태 | 비고 |
|---|---|---|
| ① 골든 LightRAG 스토어 | LightRAG working_dir (인제스트 완료 상태) | `kv_store_text_chunks.json` 필수 — L1 빌더의 유일한 입력 |
| ② 질문셋 + 정답 소스 | JSON (아래 스키마) | 벤치마크 **정답 텍스트는 불필요**, 기대 소스 문서명만 |
| ③ LLM 함수 | `async def llm(prompt, system_prompt=None, **kw) -> str` | 백그라운드 전용. 없으면 `--no-llm` (룰만으로도 동작) |

## 1. 골든 스토어 만들기

새 코퍼스를 LightRAG로 인제스트해서 완성된 working_dir를 만들고, 그 디렉토리를
**골든(읽기 전용)**으로 선언한다. StructRAG는 KG/VDB 내부를 재구축하지 않는다 —
있는 그대로 위에 얹힌다.

```python
rag = LightRAG(working_dir="./golden_dir", embedding_func=..., llm_model_func=...)
await rag.ainsert(docs)   # 코퍼스 전체
```

이후 실험은 항상 **카피본**에서: 하네스의 `--fresh`가 골든→작업 디렉토리 복사를
담당한다 (`eval_v5.py`의 `GOLDEN_DIR`/`WORK_DIR` 경로만 새 위치로 바꾸면 됨).

## 2. 질문셋 스키마

`harness_pjt/benchmark/questions.py`의 로더를 새 벤치마크용으로 교체한다.
필요한 필드는 이것뿐이다:

```jsonc
// 단일 소스 리콜 (All / Val-A 형)
{ "question": "...", "expected_sources": ["doc_a.html"] }

// 공동검색 (Val-B 형) — 두 문서가 top-k에 동시에 들어야 성공
{ "question": "...", "expected_card": "card_x.html", "expected_linked": "spec_y.md" }
```

- 매칭은 `source_hit()`의 **basename 비교** — 경로 차이는 자동 흡수
- 정답 **내용**은 어디에도 입력되지 않는다 (그라운드룰 1). evolving은 쿼리·청크·
  리트리브 결과·룰 퀄리티만 본다

## 3. 실행 배선 (하네스 어댑테이션 최소 4줄)

`evaluation/scenario2-ver5/eval_v5.py`를 복사해 새 시나리오 디렉토리를 만들고:

```python
GOLDEN_DIR = <새 골든 경로>          # ①
q_all / q_va / q_vb = <새 로더>      # ② (co 메트릭이 없으면 q_vb 비워도 동작)
DATA_ROOT = <질문 JSON 루트>         # ③ (env DATA_ROOT로도 주입 가능)
CLI_MODEL / claude_cli_llm = <LLM>   # ④ (claude CLI 없으면 자체 async 함수로 교체)
```

핵심 오브젝트 배선은 하네스 안에 이미 있다 (그대로 재사용):

```python
retriever = StructRetriever(rag, work_dir, llm_func=llm)  # 핫패스: LLM 0회 보장
retriever.sg.build_l1(rag.text_chunks._data)              # L1 스캔 (결정적, 1회)
evolver   = Evolver(rag, retriever, llm_func=llm, llm_budget=50)
ret = await retriever.retrieve(question, top_k=20)        # 평가 루프
await evolver.run_cycle()                                 # iteration 경계마다
```

실행:

```bash
cd <LightRAG 루트>
nohup python3 <경로>/eval_v5.py --fresh --iterations 5 > run.log 2>&1 &
# --fresh: 골든 카피 + 학습상태 초기화 / --no-llm: 룰 전용 / 재실행: --fresh 없이 이어서
```

## 4. 범용 vs 코퍼스 튜닝 지점 (정직 고지)

이식 시 그대로 가져가도 되는 것과, 새 도메인에서 점검해야 하는 것:

| 구성요소 | 이식성 | 조치 |
|---|---|---|
| L2 co_retrieval + 선택적 에코, QPP 퍼센타일 게이트, PlanCache/골격 밴딧, 섀도 플래너, RRF+채널보장, 회귀가드 | **완전 범용** | 없음 (자가보정) |
| L1 정규식 (파일명 리터럴 + `[A-Z][A-Z0-9]{1,7}(-[A-Z0-9]{2,8}){1,3}`) | 준범용 | 코퍼스에 크로스레퍼런스 관례가 있으면 자동 활용. 없으면 L1 엣지 0개로 자연 무력화 — 오동작은 없음. ID 관례가 다르면(`#1234`, `[[wiki]]` 등) 패턴 1줄 추가 |
| 상보 facet 사전 `_COMPLEMENT_FACETS` (analyzer.py) | **도메인 특화** | 현재 test↔spec/issue 페어 (HW 검증 도메인). 새 도메인의 상보 축으로 교체 필요 (예: 논문 코퍼스면 method↔result, 법률이면 조문↔판례). 비우면 v5.34→v5.20 동작으로 자연 강등 |
| 골격 타입 키워드 (`_SKEL_TYPES*`) | 준범용 | 한국어/영어 혼용 질의 가정. 언어가 다르면 타입 키워드 목록 교체 |
| 한국어 조사 처리 (`_KO_PARTICLE`) | 언어 특화 | 비한국어 코퍼스면 no-op — 해 없음 |

**기대 프로파일**: 크로스레퍼런스 있는 코퍼스 = v5.20~v5.34급 (co@10 70+).
없는 코퍼스 = L2/L3만으로 동작하며 무참조 상한(당사 실측 co@10 ~52-55) 부근 —
이때는 바닐라 하이브리드 대비 +15~20pt가 현실적 기대치다.

## 5. 검증 프로토콜 (그라운드룰 7 — 반드시 지킬 것)

1. 코드 **동결** 후 커밋+태그 (버전 주장은 태그 기준)
2. `--fresh --iterations N` (N≥5) **무개입** 실행 — iteration 사이 코드/설정 수정 금지
   (수정하면 그건 시스템의 진화가 아니라 사람의 진화)
3. ε=0 결정 런 (P1) — 재현 불가한 기록은 무효 (v5.8 교훈)
4. LLM 캐시(`llm_cache.json`)는 프롬프트 해시 응답 캐시 — 재현성 확보용이며 정답
   누출 아님. 새 코퍼스 첫 런은 캐시가 비므로 LLM 쿼터 여유 확인
5. 판정 지표: 자체 벤치마크의 recall@k + (co 페어가 있으면) co@k, 전 iteration
   All/ValA류 **비회귀** + 롤백 0 + 핫패스 tiers에 `llm: 0` 확인
6. 결과·사유는 DESIGN_HISTORY 방식으로 append (하네스가 자동 기록)

## 6. 버전 선택 가이드

| 버전 | 태그 | 성격 | 고르는 경우 |
|---|---|---|---|
| v5.34 | `structrag-v5.34` | 플래그십 (co@10 74.4/피크 76.7, p50 ~92ms, LLM 0) | 기본값. 도메인 상보축을 정의할 수 있을 때 |
| v5.20 | `structrag-v5.20` | 저지연 (74.4@10iter, p50 ~58ms) | 레이턴시 민감 / 상보축 정의가 어려운 도메인 |
| v5.14 | `structrag-v5.14` | 정밀 (79.1) — 핫패스 인라인 LLM 대기 | 오프라인 배치 평가 등 응답속도 무관 시 |

환경 스위치: `STRUCTRAG_NO_L1=1`(L1 차단 — 무참조 코퍼스 시뮬레이션),
`STRUCTRAG_NO_FACET=1`(facet 차단), `CLAUDE_CLI_MODEL`, `DATA_ROOT`.
