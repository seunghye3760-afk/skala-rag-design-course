"""Main State (설계서 D-2). 누적 키는 operator.add reducer로 병렬 결과를 합친다."""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages

from .task_schema import ConflictCandidate, CriterionResult, Evidence, RetryTarget, TRLResult


class MainState(TypedDict, total=False):
    run_id: str
    only_criteria: list[str]           # 작게 돌릴 때만 (app.py --criteria)
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
    final_assessment: dict
    report_path: str
    messages: Annotated[list, add_messages]
