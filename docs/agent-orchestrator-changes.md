# Agent 과제 변경 내역 — `main` → `agent/orchestrator-workers`

RAG 과제 그래프(`main`)를 **Orchestrator-Workers** 패턴으로 바꾼 내용을 팀 설명용으로 정리한 문서입니다.
기존 담당 영역(RAG·근거 수집·채점·규칙·보고서)의 **판단 로직은 바꾸지 않았고**, 그 위에 조정 계층과 품질 평가를 얹었습니다.

구조도(그림)는 [`diagrams.md`](diagrams.md)에 있습니다.

## 한눈에 보기

| | `main` (RAG 과제) | 이 브랜치 (Agent 과제) |
|---|---|---|
| 수집 작업 분배 | `dispatch_collect`가 셀(기술×항목) 36개를 **고정**으로 Send | `plan_tasks`(Orchestrator)가 셀마다 채널·쿼리를 **계획** → Worker 수가 계획에 따라 달라짐 |
| Worker 단위 | 셀 1개 (모든 채널 한꺼번에) | 셀 1개 × 채널 1개 (rag / web / open_source) |
| 재검색 | 걸린 셀을 같은 방식으로 다시 수집 | 걸린 셀만 Orchestrator가 **재계획** (미달 사유를 보고 채널·쿼리 변경) |
| Worker 실패 | 예외가 나면 그래프 중단 | 재시도 → 소진 시 그 Worker만 제외하고 계속 |
| 보고서 이후 | 바로 END | `quality_eval` 품질 평가 → 통과 / 재계획 / 재작성 / 종료 |
| State | 평면 구조 | 입력·제어·페이로드 구역 + trace_id·step_count·node_status·last_error |
| 관측성·재개 | 없음 | 결정 로그(`decisions.jsonl`), LangSmith metadata, SQLite 체크포인트 `--resume` |

## 그래프 흐름 변화

```text
main:   tech_research → dispatch_collect ─Send×36→ collect_evidence → … → synthesize_report → END
branch: tech_research → plan_tasks ─Send×N(계획)→ run_subtask → … → synthesize_report → quality_eval ─┬→ END
                         ▲                                                                          ├→ plan_tasks (근거 문제 셀)
                         └──────────── balance_check (근거 문제) ────────────────────────────────────┴→ synthesize_report (서술 문제)
```

## 새로 추가한 것

| 파일 | 내용 |
|---|---|
| `src/kv_eval/orchestrator/schema.py` | 계획 계약: `Plan`·`SubTask`·`QueryPair`(pro/con 쌍 강제)·`WorkerInput`·`WorkerOutcome` |
| `src/kv_eval/orchestrator/planner.py` | `plan_tasks` 노드: LLM 계획 → 코드 가드(커버리지·허용 채널·대상 셀·상한 80) → `State.plan`. LLM 실패 시 기본 계획 fallback. `fan_out_workers`가 SubTask마다 Send |
| `src/kv_eval/orchestrator/workers.py` | `run_subtask` 노드: 채널 1개 수집, 예외 시 재시도(총 2회) 후 excluded |
| `src/kv_eval/quality/` | `checks.py`(규칙 4축) · `judge.py`(LLM Judge, 인용 문장 검증) · `evaluate.py`(판정·라우팅) · `schema.py` |
| `src/kv_eval/graph/guards.py` | `step_count` 전체 상한 (조정 노드 통과 수) |
| `src/kv_eval/obs.py` | 결정 로그·계획 덤프·LangSmith config (trace_id·run_id) |
| `prompts/orchestrator/plan.md`, `prompts/quality/judge.md` | 계획·Judge 프롬프트 |
| `tests/test_orchestrator.py`, `test_quality.py`, `test_state_ops.py` | 가드·동적 fan-out·재계획·fallback·품질 루프·step 상한·체크포인트 재개 (23개) |
| `docs/agent_graph.mmd` | 코드에서 자동 생성한 그래프 |

## 기존 파일에서 바뀐 것 (담당자 확인 부탁)

| 파일 | 변경 | 영향 |
|---|---|---|
| `evidence/collect.py` | `collect_evidence(task, channel=None, queries=None)` 인자 추가. `channel`을 주면 그 채널만 수집, open_source는 `site:github.com` 쿼리. 채널별 근거 `evidence_id` 끝에 `-{channel}` 추가. 가짜 근거의 출처명을 항목별로 다르게 (가짜 모드 전용) | **기존 호출은 그대로 동작** (`channel=None` = 예전처럼 전체 채널) |
| `graph/dispatch.py` | `dispatch_collect`·`fan_out_collect` 삭제(→ `plan_tasks`로 대체). round 0 채점 분배를 `state["plan"]`의 셀 기준으로. `evidence_join`이 Worker 완료·제외 수 기록 | 채점 분배 로직(관점×기술 묶음)은 동일 |
| `graph/state.py` | 구역 재구성 + 필드 추가: `trace_id`, `step_count`, `node_status`, `last_error`, `plan`, `worker_outcomes`, `eval_result`, `quality_round`, `quality_feedback` | 기존 필드는 이름·타입 그대로 |
| `graph/main.py` | 노드 교체(`dispatch_collect`/`collect_evidence` → `plan_tasks`/`run_subtask`), `quality_eval` 추가, `build_graph(checkpointer=None)` | |
| `rules/balance.py` | 재검색 경로 `"dispatch_collect"` → `"plan_tasks"`. `step_count` 상한·결정 로그 추가 | **점검 조건(find_issues)은 그대로** |
| `agents/synthesis.py` | ① LLM 생성을 공용 `chat_model()`로 교체 ② 재작성 때 `quality_feedback`을 입력에 추가 | ① 기존 코드는 gpt-5.5에 temperature를 넘겨 실제 실행 시 오류 가능성이 있었음 |
| `app.py` | trace_id 생성, SQLite 체크포인터, `--resume`, LangSmith config, 결과 요약 출력 | 기존 `--criteria` 그대로 |
| `configs/runtime.yaml` | `orchestrator:`(Worker 상한·재시도·step 상한·recursion_limit), `quality:`(루프 상한·judge·편향 임계값) 블록 추가 | 기존 값 변경 없음 |
| `pyproject.toml` | `langgraph-checkpoint-sqlite` 의존성 추가 | `uv sync` 필요 |

`graph/task_schema.py`(팀 공통 계약)는 **수정하지 않았습니다.** 계획 관련 형식은 `orchestrator/schema.py`에 따로 두었습니다.

## 주요 설계 결정과 이유

1. **Worker 단위를 "셀 × 채널"로** — 셀 단위로 두면 계획이 무엇을 정하든 round 0은 항상 36개라 고정 fan-out처럼 보임. 채널까지 계획하게 해야 Worker 수가 실제로 달라짐 (평가 항목 "동적 동작 실증" 대응)
2. **LLM 계획 + 코드 가드** — 계획은 유연하게 LLM이, 커버리지·허용 채널·상한 같은 불변 조건은 코드가 강제. LLM이 실패해도 기본 계획으로 진행
3. **Worker 실패 정책 = 재시도 후 제외하고 계속** — 근거 0건은 실패로 보지 않음(collect가 쿼리 재작성까지 함). 셀이 비면 balance_check가 잡아 재계획
4. **품질 평가 Hybrid** — 규칙(결정적) + Judge(유연). Judge는 보고서 문장을 그대로 인용해야 하고, 실제로 없는 문장이면 버림 → Judge 환각으로 루프가 도는 것을 방지
5. **품질 미달 경로 분리** — 셀로 특정되는 근거 문제는 재계획(재수집·재채점), 서술 문제는 재작성만. 비용이 큰 재수집을 필요한 셀로 한정
6. **편향 분포 판정은 셀 4개 이상일 때만** — `--criteria`로 작게 돌리면 발행 주체 수 기준을 만족할 수 없어 항상 루프가 돌기 때문 (`min_cells_for_distribution`)

## 확인 상태

- `uv run pytest`: 101 passed, 1 skipped (모두 `KV_FAKE=1` 가짜 데이터 모드)
- **실제 LLM·검색 API로는 아직 실행 전** → LLM 계획이 실제로 셀마다 다른 채널을 고르는지, Judge 응답 형식은 실제 실행으로 확인 필요
- 남은 일: 실제 실행 + LangSmith 트레이스 캡처(`tracing-*.png`), 평가 보고서 PDF(10장 이내), Contributors 역할 갱신
