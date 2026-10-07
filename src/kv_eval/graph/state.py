"""Main State — Orchestrator-Workers.

설계 원칙 (README "State Schema" 절과 같은 내용):
  - 제어 vs 페이로드: 라우팅·계획·종료·재개에 필요한 최소 상태(제어)와 작업 결과(페이로드)를 구역으로 나눈다.
  - 관측성: 결정 로그(사유 포함)는 State에 넣지 않고 obs.py가 decisions.jsonl·LangSmith로 보낸다.
  - 지속성 비용: 체크포인트마다 저장되므로 원문 본문·검색 결과 원본은 넣지 않는다 (캐시·출력 폴더).
    계획은 현재 라운드만 두고 이전 계획은 plans/r{n}.json으로. 누적 필드는 라운드·Worker 상한으로 크기가 묶인다.
  - 상관: trace_id(LangSmith metadata)·run_id(체크포인트 thread_id·출력 폴더)로 외부 로그와 잇는다.
  - 재개/복구: node_status·last_error·라운드 카운터만 있으면 SQLite 체크포인트에서 이어서 실행할 수 있다.
  - 동시 처리: 병렬 Worker가 쓰는 필드는 모두 reducer(operator.add / _merge / _last_error)를 둔다.
  - 종료 보장: retry_round·quality_round(루프별 상한) + step_count(조정 노드 전체 상한) + recursion_limit.
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages

from ..orchestrator.schema import Plan, WorkerOutcome
from ..quality.schema import EvalVerdict
from .task_schema import ConflictCandidate, CriterionResult, Evidence, RetryTarget, TRLResult


def _merge(a: dict | None, b: dict | None) -> dict:
    """node_status: 노드별 최신 상태로 덮어쓰며 병합 (동시 쓰기 안전)."""
    return {**(a or {}), **(b or {})}


def _last_error(a: str | None, b: str | None) -> str | None:
    """last_error: 새 에러가 있을 때만 갱신 (병렬 Worker 중 하나만 실패해도 남는다)."""
    return b if b else a


class MainState(TypedDict, total=False):
    # ── 입력·설정 (실행 중 바뀌지 않음) ───────────────────────────
    run_id: str                        # 체크포인트 thread_id · outputs/runs/<run_id>
    only_criteria: list[str]           # 작게 돌릴 때만 (app.py --criteria)
    technologies: list[dict]
    domain: dict
    rubrics: dict

    # ── 제어 메타데이터 (조정·종료·재개에 필요한 최소치) ──────────────
    trace_id: str                      # 외부 로그·LangSmith metadata와 잇는 상관 키
    step_count: int                    # 조정 노드(plan/balance/quality) 통과 수 — 전체 상한 가드
    node_status: Annotated[dict[str, str], _merge]   # {"plan_tasks": "done r0", ...} 재개·디버깅용
    last_error: Annotated[str | None, _last_error]
    plan: Plan                         # Orchestrator의 현재 라운드 계획 (라운드마다 덮어씀)
    worker_outcomes: Annotated[list[WorkerOutcome], operator.add]   # Worker별 done/excluded
    retry_targets: list[RetryTarget]   # 다음 라운드 재작업 대상 (balance_check·quality_eval이 지정)
    retry_round: int                   # 근거 수집 라운드 (balance 재시도 상한 max_rounds)
    quality_round: int                 # 품질 평가 루프 카운터 (상한 quality.max_rounds)
    eval_result: EvalVerdict           # 최근 품질 판정 (축별 결과·다음 경로·사유)
    quality_feedback: list[str]        # 재작성 때 종합 에이전트에 넘기는 지적 사항

    # ── 작업 페이로드 (Worker·에이전트 산출물) ──────────────────────
    tech_briefs: dict
    evidence_pool: Annotated[list[Evidence], operator.add]   # 원문 전체가 아닌 excerpt·locator만
    search_log: Annotated[list[dict], operator.add]
    criterion_results: Annotated[list[CriterionResult], operator.add]
    balance_issues: list[RetryTarget]
    info_gaps: list[RetryTarget]
    final_results: list[CriterionResult]   # apply_rules가 상한·하한 적용 후 확정한 결과
    trl_results: list[TRLResult]
    conflicts: list[ConflictCandidate]
    final_assessment: dict
    report_path: str                   # 보고서 본문은 파일로, State에는 경로만
    messages: Annotated[list, add_messages]
