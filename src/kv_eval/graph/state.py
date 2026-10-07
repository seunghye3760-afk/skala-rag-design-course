"""계층형 State. 병렬 쓰기 채널만 reducer로 합치고 제어/계획/보고서는 단일 노드가 갱신한다."""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages

from .task_schema import (
    ConflictCandidate,
    CriterionResult,
    Evidence,
    QualityEvaluation,
    RetryTarget,
    SupervisorDecision,
    TaskPlan,
    TRLResult,
    WorkerResult,
)


class ControlState(TypedDict, total=False):
    run_id: str
    only_criteria: list[str]           # 작게 돌릴 때만 (app.py --criteria)
    phase: str
    supervisor_step: int
    supervisor_decision: SupervisorDecision
    decision_log: Annotated[list[SupervisorDecision], operator.add]
    final_status: str


class PlanningState(TypedDict, total=False):
    task_plan: TaskPlan
    plan_history: Annotated[list[TaskPlan], operator.add]
    worker_results: Annotated[list[WorkerResult], operator.add]


class DomainState(TypedDict, total=False):
    technologies: list[dict]
    domain: dict
    rubrics: dict
    tech_briefs: dict
    evidence_pool: Annotated[list[Evidence], operator.add]
    search_log: Annotated[list[dict], operator.add]
    criterion_results: Annotated[list[CriterionResult], operator.add]
    balance_issues: list[RetryTarget]
    retry_targets: list[RetryTarget]
    retry_round: int                   # 전역 카운터, 최대 2
    info_gaps: list[RetryTarget]
    final_results: list[CriterionResult]   # apply_rules가 상한·하한 적용 후 확정한 결과
    trl_results: list[TRLResult]
    conflicts: list[ConflictCandidate]


class ReportState(TypedDict, total=False):
    final_assessment: dict
    report_draft: str
    quality_evaluation: QualityEvaluation
    report_revision: int
    report_path: str


class ObservabilityState(TypedDict, total=False):
    messages: Annotated[list, add_messages]


class MainState(ControlState, PlanningState, DomainState, ReportState, ObservabilityState, total=False):
    """그래프 전체 공개 State. 레이어별 소유권은 위 TypedDict로 구분한다."""
