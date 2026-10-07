# 아키텍처 (설계서 ver1.4 D장 요약)

전체 설계는 `deliverables/RAG-Design_*.pdf` 가 기준이다. 그림: `docs/flow.png`(전체 흐름), `docs/main_graph.png`(Main Graph).

## 흐름
load_config → load_rubrics → tech_research → dispatch_collect →(Send × 36) collect_evidence → join
→ score_dispatch →(Send × 8) score_task[trl·market·stakeholder·domain] → join → balance_check
→ (재검색 필요: dispatch_collect / 형식 오류만: score_dispatch / 통과·한도 소진: apply_rules) → synthesize_report

## 계약 파일
- `src/kv_eval/graph/task_schema.py`: Locator, Chunk, Evidence, CriterionResult, RetryTarget, CollectTask, ScoreTask …
- `src/kv_eval/graph/state.py`: MainState (설계서 D-2 + `final_results`)
- 근거는 모든 round 누적·중복 제거, 채점은 (기술, 항목)별 최신 round만 사용 (`graph/reducers.py`)

## Mermaid
`docs/main_graph.mmd` 참조

## Orchestrator-Workers (Agent 과제, `src/kv_eval/orchestrator/`, 실행 `app_agent.py`)

위 흐름(`graph/main.py`, RAG 실습)은 그대로 두고, 같은 worker·규칙·보고서 모듈을 재사용해 조정 계층을 따로 구성했다.
그림: `docs/main_graph_orchestrator.mmd` (`scripts/draw_orchestrator_graph.py`로 그래프 정의에서 생성).

```
init → load_config → load_rubrics → tech_research → plan_tasks
  →(route_after_plan: pending 서브태스크만 Send×N / 없으면 score_dispatch)
  → collect_worker ×N → evidence_join(defer) → score_dispatch →(fan_out_score) score_worker ×M → score_join(defer)
  → balance_check →(plan_tasks | score_dispatch | apply_rules) → apply_rules → synthesize_report
  → evaluate_report(규칙 4항목) →(PASS면 judge_node(LLM) / 미달이면 route_after_quality)
  → route_after_quality: plan_tasks(재수집) | score_dispatch(재채점) | synthesize_report(재생성) | finalize → END
```

| 구성 | 파일 | 역할 |
|---|---|---|
| State | `orchestrator/state.py` | `OrchestratorState` — 제어 메타(task_plan·node_status·errors·quality_verdict·step_count…) + 페이로드(MainState 키). `SubTask`·`QualityVerdict`·`CollectJob`·`ScoreJob`. reducer `merge_tasks`(task_id 최신), `merge_dict`. 계약 파일은 import만 |
| 계획 | `orchestrator/plan.py` | `plan_tasks` — **LLM 플래너**(`prompts/plan.md`, 구조화 출력)가 항목마다 기술별 채널(paper/web/open_source)과 검색 쿼리 템플릿 쌍 수(1~3, 쌍 하나 = worker 하나)를 정해 `task_plan`에 저장 → fan-out 수가 모델 결정. 코드는 가드만(커버리지 보완·`{tech}` 대칭·셀당 상한). LLM 실패 → 1회 재시도 → 루브릭 기본 템플릿 비상 계획(`plan_fallback_default`). 모드 initial / retry(balance 사유 반영) / replan(품질 피드백 반영) |
| 분배·worker | `orchestrator/dispatch.py` | `route_after_plan`(pending만 Send), `collect_worker`·`score_worker`(기존 함수 래퍼: 예외 → 1회 재시도 → 제외/NA, `node_status`·`errors` 기록), join은 `defer=True` |
| 품질 | `orchestrator/quality.py` | `evaluate_report`(groundedness·neutrality·bias·coverage 규칙, `configs/quality.yaml`) → `judge_node`(LLM, `prompts/quality_judge.md`, 판정만) → `route_after_quality`(순수 함수). `finalize`: 미달 항목 SUMMARY 기록, PDF > 10쪽이면 부록을 `appendix.md`로 분리, `report_meta.json` |
| 관측성 | `orchestrator/observability.py` | `decision()` → `outputs/runs/<run_id>/decision_log.jsonl`, `run_config()` → LangSmith run_name·metadata(run_id)·thread_id·recursion_limit·max_concurrency |
| 조립 | `orchestrator/graph.py` | `build_orchestrator_graph(checkpointer=MemorySaver)` |

종료 상한은 전부 코드 상수(`configs/runtime.yaml`: `retry.max_rounds=2`, `quality.max_rounds=2`, `limits.recursion_limit`).
모델이 런타임에 정하는 지점은 두 곳 — `plan_tasks`의 계획(채널·쿼리·worker 수)과 `judge_node`의 판정 — 이고 라우팅은 전부 순수 함수다.
같은 셀의 서브태스크가 여러 개면 `collect_worker`가 evidence_id 끝에 슬롯 글자(a, b, …)를 붙여 충돌을 막는다.
