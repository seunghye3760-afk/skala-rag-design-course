# Orchestrator-Workers 아키텍처

이 문서는 `codex/orchestrator-workers` 브랜치의 실제 구현을 설명한다. 그래프 원본은
`docs/main_graph.mmd`, 전환 결정과 만들지 않은 추상화는 `docs/orchestration-plan.md`를 참고한다.

## 설계 기준

참조 프로젝트의 `OrchestratorState → Plan → Send → Worker → reducer → Synthesizer` 흐름을 유지하되,
현재 프로젝트에 이미 있는 `MainState`, `CollectTask`, `ScoreTask`, `RetryTarget`을 재사용한다.
Planner는 LLM이 아니라 기술·루브릭·재시도 State를 읽는 결정적 Python 함수다. 범용 Worker Runtime,
Registry, Repository, Manager, 이벤트 버스는 추가하지 않았다.

로컬 참조 경로는 1순위 `/Users/ash/ashley/LangGraph-KV/langgraph-v1`이 없어 2순위
`/Users/ash/ashley/RAG-pipeline/langgraph-v1`의 다음 예제를 사용했다.

- `12-Pattern/04-Orchestrator-Workers.ipynb`
- `01-Features/11-State-Customization.ipynb`
- `01-Features/21-Branching.ipynb`

## 역할

| 역할 | 구현 | 입력 | 출력 |
|---|---|---|---|
| Orchestrator / Planner | `graph/planner.py` | 기술, 루브릭, `only_criteria`, `retry_targets`, round | `WorkPlan` |
| Evidence Worker | `evidence/collect.py` | `CollectTask` | Evidence, 검색 로그, `WorkerResult` |
| Score Worker | `agents/score_task.py` | `ScoreTask` | `CriterionResult`, `WorkerResult` |
| Aggregator | reducer, join, `balance.py` | 병렬 Worker 결과 | 누적 결과, `RetryTarget`, `info_gaps` |
| Synthesizer | `agents/synthesis.py`, `reporting/report.py` | 확정 채점, TRL, 상충, 근거 | `report_draft` |
| Quality Evaluator | `rules/report_quality.py` | 초안과 State 근거 | `ReportQualityResult` |
| Renderer | `reporting/pdf.py` | 품질 통과 초안 | preview PDF, 실제 페이지 수, 최종 PDF |

## State와 계약

`MainState` 하나를 사용한다. 병렬 출력은 `Annotated[list, operator.add]` reducer로 합치고 현재 Plan과
판정 결과처럼 단일 값인 필드는 교체한다.

| State | 계약 및 갱신 방식 |
|---|---|
| `work_plan` | 현재 phase의 `WorkPlan`; Planner가 교체 |
| `evidence_pool` | `Evidence` 누적; 기술·항목·근거 ID 기준 중복 제거 시 사용 |
| `criterion_results` | `CriterionResult` 누적; 규칙 적용 시 셀별 최신 유효 round 선택 |
| `worker_results` | `WorkerResult` 단순 누적; completed/partial/failed 구분 |
| `retry_targets` | 기존 `RetryTarget`; research 또는 rescore 대상만 포함 |
| `retry_round` | 수집·채점 재시도 round, 최대 2 |
| `info_gaps` | 재시도 한도 뒤에도 해결되지 않은 항목 |
| `report_draft` | 저장 전 Markdown 문자열 |
| `report_quality` | 통과 여부, 실제 페이지 수, 문제, 다음 동작 |
| `report_retry_round` | 보고서 재작성·압축 공통 횟수, 최대 2 |
| `report_page_count` | `pypdf.PdfReader`가 센 실제 페이지 수 |

`WorkPlan`은 `collect_tasks`, `score_tasks`, `round`, `reason`만 가진다. `WorkerResult`는 작업 ID, 종류,
상태, 생성 건수, 재시도 가능 여부, 오류만 기록한다. 실제 근거와 점수는 기존 모델에 그대로 저장한다.

## 동적 Plan과 Send

최초 수집 Plan은 선택한 기술과 `criteria_list(rubrics, only_criteria)`의 곱으로 만든다. 재시도 Plan은
`retry_targets`의 research 셀만 만든다. 채점 Plan은 기술·관점별로 선택 항목을 묶고 해당 Evidence만
포함하며, 재채점에서는 지정된 셀만 묶는다. 따라서 36개 수집이나 8개 채점 같은 고정 개수에 의존하지
않는다.

`fan_out_collect`와 `fan_out_score`는 Plan을 다시 계산하지 않고 `WorkPlan`의 작업을 순서대로 LangGraph
`Send`로 변환한다. reducer가 Worker 결과를 합치고 명시적인 join 노드가 다음 Plan 단계로 연결한다.

## fallback과 유한 재시도

Evidence Worker는 정상 0건을 completed, 일부 URL 실패 뒤 근거 생성은 partial, 실행 오류 뒤 결과 0건은
failed로 구분한다. Score Worker는 기존 즉시 1회 재요청을 유지하고 최종 실패는 NA 결과와 failed
`WorkerResult`를 함께 남긴다. 이전 round의 성공 결과는 후속 실패 fallback으로 덮어쓰지 않는다.

`balance_check`는 retry 가능한 partial/failed를 `RetryTarget`으로 바꾼다. research가 하나라도 있으면
수집 Plan으로, rescore만 있으면 채점 Plan으로 돌아간다. `retry_round == 2`이면 더 반복하지 않고 남은
항목을 `info_gaps`로 확정한다. Worker 내부 쿼리 재작성도 항목당 1회다.

| loop | 종료 조건 |
|---|---|
| 수집 쿼리 재작성 | `query_rewrite_max = 1` |
| 근거 수집·채점 재계획 | `retry.max_rounds = 2` |
| Score 구조화 출력 재요청 | 최초 호출 뒤 1회 |
| 보고서 재작성·압축 | 두 동작 합산 `report_retry_round = 2` |

## 보고서 품질 loop

`synthesize_report`는 기존 Main Graph의 호환 노드 이름이며 내부 단계는 다음처럼 분리되어 있다.

1. `synthesize_draft`: 종합 결과와 Markdown 초안만 만들고 파일은 쓰지 않는다.
2. `evaluate_report`: 필수 섹션, Evidence ID, 근거 없는 수치, 정보 공백·한계, 점수 합산·순위·단정적
   우열 표현을 코드로 검사한다.
3. 품질 미달이면 문제 목록을 Synthesizer 입력에 추가해 `rewrite_report`를 실행한다.
4. 품질 통과 뒤 `render_preview`가 숨김 preview PDF를 만들고 `pypdf`로 실제 페이지를 센다.
5. 10페이지를 초과하면 `compress_report`가 중복, 장문 rationale, 반복 방법론부터 축약하고 다시 검사한다.
6. 재작성과 압축을 합쳐 2회 안에 통과하지 못하면 `retry_kind=None`인 명시적 실패로 끝나며 최종 파일을
   저장하지 않는다.
7. 통과한 preview만 `persist_report`가 `report.pdf`로 승격한다.

본문과 표는 9pt 이상이며 여백·글꼴보다 내용 압축을 우선한다. 근거 ID, 실험 조건 차이, 상충 지점,
정보 공백, 한계와 적용 시사점은 압축 대상에서 보호한다. 8~10페이지는 권장 범위이고 10페이지 이하는
하드 게이트다.

## 상세 산출물과 PDF

`report.pdf`와 `report.md`는 의사결정자가 읽는 요약본이다. 전체 검색 로그와 상세 루브릭은 넣지 않고,
실제로 인용한 출처만 REFERENCE에 남긴다. 상세 추적과 재현에는 다음 JSON을 사용한다.

| 파일 | 역할 |
|---|---|
| `evidence.json` | 전체 구조화 근거와 원문 위치 |
| `scores.json` | PDF에서 축약한 전체 항목별 채점과 rationale |
| `search_log.json` | 질의, 재작성, 검색 오류와 Worker 상태 |
| `info_gaps.json` | 재시도 한도 뒤에도 남은 정보 공백 |

이 분리는 10페이지 제한을 지키면서도 판단의 추적 가능성을 잃지 않기 위한 것이다. 실행 산출물은
`outputs/runs/` 또는 `KV_OUTPUT_DIR` 아래에 생성되며 Git에는 커밋하지 않는다.
