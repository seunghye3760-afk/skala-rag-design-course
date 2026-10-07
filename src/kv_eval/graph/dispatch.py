"""WorkPlan의 작업을 LangGraph Send로 병렬 분배하고 결과를 합류시킨다."""
from __future__ import annotations

from langgraph.types import Send

from .. import progress
from .planner import plan_collect_work, plan_score_work
from .state import MainState

# main.py의 기존 노드 이름을 유지하는 호환 alias. 실제 계획은 planner.py가 만든다.
dispatch_collect = plan_collect_work
score_dispatch = plan_score_work


def fan_out_collect(state: MainState) -> list[Send]:
    tasks = state["work_plan"].collect_tasks
    progress.step("dispatch_collect", f"계획된 수집 작업 {len(tasks)}개 분배")
    return [Send("collect_evidence", task) for task in tasks]


def fan_out_score(state: MainState) -> list[Send]:
    tasks = state["work_plan"].score_tasks
    progress.step("score_dispatch", f"계획된 채점 작업 {len(tasks)}개 분배")
    return [Send("score_task", task) for task in tasks]


def evidence_join(state: MainState) -> dict:
    """수집 병렬 작업이 모두 끝난 뒤 한 번 실행된다 (join)."""
    progress.step("evidence_join", f"근거 {len(state.get('evidence_pool', []))}건 누적")
    return {}


def score_join(state: MainState) -> dict:
    progress.step("score_join", f"채점 결과 {len(state.get('criterion_results', []))}건 누적")
    return {}
