# 계층형 Multi-Agent Orchestration 아키텍처

기존 RAG 설계 기준은 `deliverables/RAG-Design_*.pdf`이며, 새 orchestration 흐름은
`docs/main_graph.mmd`가 기준이다. `docs/main_graph.png`는 이전 구조의 참고본이다.

## 흐름
load_config → load_rubrics → tech_research → plan_collect →(Send × N) collect_worker → join
→ plan_score →(Send × N) score_worker → join → balance_check → supervisor
→ (재검색: plan_collect / 재채점: plan_score / 충분: apply_rules)
→ supervisor → synthesize_report → supervisor → evaluate_report
→ (미달: synthesize_report / 통과·수정 한도 소진: finalize_report)

## 계약 파일
- `src/kv_eval/graph/task_schema.py`: 기존 RAG 계약 + WorkItem, TaskPlan, WorkerResult, SupervisorDecision, QualityEvaluation
- `src/kv_eval/graph/state.py`: Control / Planning / Domain / Report / Observability 레이어를 합성한 MainState
- 근거는 모든 round 누적·중복 제거, 채점은 (기술, 항목)별 최신 round만 사용 (`graph/reducers.py`)

## 패턴 선택과 경계
- 주 패턴: Orchestrator-Workers
- 상위 제어: Supervisor
- Worker 간 직접 edge는 없으며 모든 결과는 join 후 Supervisor가 판단한다.
- Orchestrator는 현재 루브릭, `only_criteria`, `retry_targets`를 읽어 `TaskPlan`을 만든다.

## 실패와 종료
- Worker 예외는 실패 결과로 기록하고 나머지 fan-out은 계속한다.
- 실패로 결과가 비면 balance_check가 해당 항목만 재계획한다.
- 재시도 한도 소진 시 info_gaps로 제외하고 계속한다.
- 보고서 평가 실패는 최대 2회 수정하고 이후 `completed_with_gaps`로 종료한다.
- Supervisor 전체 단계도 12회로 제한한다.

## 관측성과 지속성
- State에는 복구에 필요한 최소 제어 정보와 구조화 결과만 둔다.
- 상세 결정은 `decision_log`, 전체 실행은 LangSmith trace로 확인한다.
- `run_id`, `thread_id`, task ID를 상관 키로 사용한다.
- CLI 실행은 SQLite checkpointer를 사용한다.

## Mermaid
`docs/main_graph.mmd` 참조
