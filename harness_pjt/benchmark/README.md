# SHS-POC-RAGFlow Benchmark — 운용 가이드

shs-poc-ragflow 코퍼스로 **Evolving LightRAG** (LightRAG + BM25 하이브리드 + WikiGraph EVOLVE)를 벤치마크합니다.

**평가 지표**: `source_hit@20` — 정답 소스 파일이 상위 20개 검색 결과에 포함되는 비율

---

## 모드 3가지

| 모드 | LLM | RAG 저장 위치 | 속도 | 비용 |
|---|---|---|---|---|
| **로컬 (Qwen)** | Qwen3.6-35B @ 내부 서버 | `rag_data/` | 보통 | 서버 운용비만 |
| **Claude CLI** | claude-haiku-4-5 (claude.ai) | `rag_data_claude/` | 느림 (1건/30s~) | claude.ai 구독 |
| **Groq** | llama-3.1-8b-instant (Groq API) | `rag_data_groq/` | 빠름 | 무료 (30 RPM, 14.4K RPD) |

세 모드는 각자 별도 RAG 디렉토리를 사용하므로 동시에 실행해도 간섭 없음.

---

## 직접 실행 (수동)

네트워크 오류 발생 시 프로세스가 멈춥니다. 장시간 무인 운용에는 아래 auto_runner를 사용하세요.

```bash
cd /home/bwkim_u/harness_pjt/LightRAG

# 로컬 Qwen
bash harness_pjt/benchmark/run_benchmark_final.sh

# Claude CLI
bash harness_pjt/benchmark/run_benchmark_claude_cli.sh

# Groq
export GROQ_API_KEY=gsk_...
bash harness_pjt/benchmark/run_benchmark_groq.sh
```

---

## auto_runner로 실행 (권장 — 자동 재개)

네트워크 오류나 rate limit 발생 시 자동으로 대기 후 재시작합니다.
LightRAG가 완료된 청크를 캐시에서 스킵하므로 이어서 재개됩니다.

```bash
cd /home/bwkim_u/harness_pjt/LightRAG

# 로컬 Qwen
nohup python3 harness_pjt/benchmark/auto_runner.py \
    run_benchmark_final.sh \
    harness_pjt/benchmark/results/benchmark_final.log \
    >> harness_pjt/benchmark/results/auto_runner_local.log 2>&1 &

# Claude CLI
nohup python3 harness_pjt/benchmark/auto_runner.py \
    run_benchmark_claude_cli.sh \
    harness_pjt/benchmark/results/benchmark_claude_cli.log \
    >> harness_pjt/benchmark/results/auto_runner_claude.log 2>&1 &

# Groq
export GROQ_API_KEY=gsk_...
nohup python3 harness_pjt/benchmark/auto_runner.py \
    run_benchmark_groq.sh \
    harness_pjt/benchmark/results/benchmark_groq.log \
    >> harness_pjt/benchmark/results/auto_runner_groq.log 2>&1 &
```

---

## auto_runner 동작 정책

### 로컬 Qwen

| 상황 | 감지 패턴 | 동작 |
|---|---|---|
| Qwen 서버 다운 | `Connection error` / `APIConnectionError` 5회 연속 | **30분 대기 후 재시작** |
| 정상 완료 | 로그에 `Done:` 출력 | 종료 |

- 서버가 내려가면 자동으로 30분마다 연결 시도
- `QWEN_WAIT_MIN=30` 환경변수로 대기 시간(분) 조절 가능

### Claude CLI

| 상황 | 감지 패턴 | 동작 |
|---|---|---|
| 일일 사용량 초과 | `claude.*usage limit` 메시지 | **리셋 시간까지 대기 후 재시작** |
| 시간 파싱 실패 | — | **기본 4시간 대기** |
| 정상 완료 | `Done:` | 종료 |

- claude CLI 출력의 "resets in X hours Y minutes" 문구를 파싱하여 정확한 시간에 재시작
- `CLAUDE_WAIT_H=4` 환경변수로 기본 대기 시간(시) 조절 가능

### Groq

| 상황 | 감지 패턴 | 동작 |
|---|---|---|
| TPM 한도 초과 (분당) | `rate_limit_exceeded` | **5분 대기 후 재시작** |
| RPD 한도 초과 (일별) | `requests per day.*limit` | **다음날 자정 UTC(한국 오전 9시)까지 대기** |
| 정상 완료 | `Done:` | 종료 |

- 모델: `llama-3.1-8b-instant` (6K TPM, 14.4K RPD, 500K TPD)
- compound/llama-3.3-70b/llama-4-scout는 groq/compound RPD 250을 공유 → 사용 금지
- `GROQ_WAIT_MIN=5` 환경변수로 TPM 대기 시간(분) 조절 가능

---

## 중지 방법

```bash
# 전체 중지 (auto_runner + 하위 벤치마크 모두)
pkill -f "auto_runner.py"
pkill -f "run_benchmark.py"

# 특정 모드만 중지
pkill -f "auto_runner.py.*run_benchmark_final"       # 로컬만
pkill -f "auto_runner.py.*run_benchmark_claude"      # Claude CLI만
pkill -f "auto_runner.py.*run_benchmark_groq"        # Groq만

# 상태 확인
ps aux | grep -E "auto_runner|run_benchmark" | grep -v grep | awk '{print $2, $11, $12}'
```

---

## 진행 상황 모니터링

```bash
# auto_runner 재시작 이력
tail -f harness_pjt/benchmark/results/auto_runner_local.log
tail -f harness_pjt/benchmark/results/auto_runner_claude.log
tail -f harness_pjt/benchmark/results/auto_runner_groq.log

# 벤치마크 실제 처리 로그
tail -f harness_pjt/benchmark/results/benchmark_final.log
tail -f harness_pjt/benchmark/results/benchmark_claude_cli.log
tail -f harness_pjt/benchmark/results/benchmark_groq.log

# 완료된 파일 수
grep -c "Completed processing" harness_pjt/benchmark/results/benchmark_final.log
grep -c "Completed processing" harness_pjt/benchmark/results/benchmark_claude_cli.log
grep -c "Completed processing" harness_pjt/benchmark/results/benchmark_groq.log

# 전체 프로세스 상태
ps aux | grep -E "auto_runner|run_benchmark" | grep -v grep | awk '{print $2, $11, $12}'
```

---

## RAG 완성 후 벤치마크 흐름

### RAG 데이터 저장 위치

| 모드 | Ingest 완료 후 저장 위치 |
|---|---|
| 로컬 Qwen | `harness_pjt/rag_data/` |
| Claude CLI | `harness_pjt/rag_data_claude/` |
| Groq | `harness_pjt/rag_data_groq/` |

RAG 데이터: 그래프(`graph_chunk_entity_relation.graphml`), 벡터 DB, BM25 인덱스, LLM 캐시

### Scenario 1 → Scenario 2 흐름

```
[Ingest 완료] → 원본 RAG 완성
      │
      ▼
[Scenario 1: 초기 평가] — 읽기 전용, 그래프 변경 없음
  벡터 vs BM25 vs Hybrid source_hit@20 비교
      │
      ▼
[자동 백업 생성] ← run_benchmark.py가 Scenario 2 진입 전 자동 실행
  rag_data/         →  rag_data_pre_evolve/
  rag_data_claude/  →  rag_data_claude_pre_evolve/
  rag_data_groq/    →  rag_data_groq_pre_evolve/
      │
      ▼
[Scenario 2: EVOLVE 후 평가] — 그래프 수정 발생
  쿼리 로그 기반 그래프 진화 (3 사이클)
  → Before / After 비교 리포트 생성
```

Scenario 2는 그래프를 변경합니다. 백업에서 복원하는 방법:

```bash
# 예: Qwen RAG 원본 복원
rm -rf harness_pjt/rag_data
cp -r harness_pjt/rag_data_pre_evolve harness_pjt/rag_data
```

### 결과 파일

```
harness_pjt/benchmark/results/
├── benchmark_results_*.md        # Scenario 1+2 마크다운 리포트
├── benchmark_final.log           # 로컬 Qwen 전체 로그
├── benchmark_claude_cli.log      # Claude CLI 전체 로그
├── benchmark_groq.log            # Groq 전체 로그
├── auto_runner_local.log         # auto_runner 재시작 이력 (로컬)
├── auto_runner_claude.log        # auto_runner 재시작 이력 (Claude)
└── auto_runner_groq.log          # auto_runner 재시작 이력 (Groq)
```

---

## 환경변수 요약

| 변수 | 기본값 | 설명 |
|---|---|---|
| `RAG_LLM` | `qwen` | `qwen` / `claude-cli` / `groq` |
| `CLAUDE_CLI_MODEL` | `claude-haiku-4-5-20251001` | Claude CLI 모델 |
| `GROQ_API_KEY` | — | Groq API 키 (필수) |
| `GROQ_MODEL` | `llama-3.1-8b-instant` | Groq 모델명 (compound 계열 사용 금지) |
| `MAX_ASYNC` | 모드별 | 동시 LLM 호출 수 (Qwen:4, Claude:1, Groq:1) |
| `CHUNK_SIZE` | `3000` (Claude/Qwen), `1200` (Groq) | 청크 크기 (토큰) |
| `QWEN_WAIT_MIN` | `30` | Qwen 서버 다운시 재시도 대기(분) |
| `CLAUDE_WAIT_H` | `4` | Claude 한도 초과시 기본 대기(시) |
| `GROQ_WAIT_MIN` | `5` | Groq TPM 한도시 재시도 대기(분) |

---

## 자주 발생하는 오류

| 오류 | 원인 | 해결 |
|---|---|---|
| `claude CLI timed out after 180s` | Claude 응답 지연 | auto_runner 자동 처리. 수동: `MAX_ASYNC=1` |
| `GROQ_API_KEY not set` | Groq API 키 미설정 | `export GROQ_API_KEY=gsk_...` |
| `Connection error` (Qwen) | Qwen 서버 다운 | auto_runner 30분 후 자동 재시도 |
| `groq/compound RPD 250 exhausted` | compound 일일 한도 소진 | auto_runner가 다음날 자정 UTC까지 대기 |
| `413 Request Entity Too Large` | compound 모델 요청 크기 초과 | CHUNK_SIZE 축소 또는 llama 직접 모델 사용 |
