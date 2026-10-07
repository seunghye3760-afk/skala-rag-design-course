"""State를 보고 구조화된 작업 목록을 만드는 Orchestrator."""
from __future__ import annotations

from collections import defaultdict

from .. import progress
from ..config import agent_of, criteria_list
from .state import MainState
from .task_schema import TaskPlan, WorkItem


def plan_collect(state: MainState) -> dict:
    rnd = state.get("retry_round", 0)
    targets = [x for x in state.get("retry_targets", []) if x.kind == "research"]
    cells: list[tuple[str, str, str | None]]
    if rnd == 0 and not targets:
        cells = [(t["tech_id"], c["id"], None) for t in state["technologies"]
                 for c in criteria_list(state["rubrics"], state.get("only_criteria"))]
        why = "선택된 기술과 루브릭에서 최초 근거 수집 작업을 동적으로 생성"
    else:
        cells = [(x.tech_id, x.criterion_id, x.hint) for x in targets]
        why = "Supervisor가 지정한 근거 부족 항목만 재계획"
    items = [WorkItem(
        task_id=f"collect:r{rnd}:{tid}:{cid}", kind="collect", tech_id=tid,
        criterion_ids=[cid], objective=f"{tid}의 {cid} 긍정·비판 근거 수집",
        success_criteria=["관련 근거 2건 이상", "긍정·비판 근거 포함", "원문 위치 추적"],
        rewrite_hint=hint, attempt=rnd,
    ) for tid, cid, hint in cells]
    plan = TaskPlan(phase="collect", round=rnd, rationale=why, items=items)
    progress.step("orchestrator", f"근거 수집 계획 {len(items)}개 생성 (round {rnd})")
    return {"task_plan": plan, "plan_history": [plan], "phase": "collect_planned"}


def plan_score(state: MainState) -> dict:
    rnd = state.get("retry_round", 0)
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    targets = state.get("retry_targets", [])
    if rnd == 0 and not targets:
        for tech in state["technologies"]:
            for crit in criteria_list(state["rubrics"], state.get("only_criteria")):
                groups[(crit["agent"], tech["tech_id"])].append(crit["id"])
        why = "수집된 근거를 관점과 기술별 채점 작업으로 동적 분할"
    else:
        for target in targets:
            groups[(agent_of(state["rubrics"], target.criterion_id), target.tech_id)].append(
                target.criterion_id)
        why = "재검색·재채점 대상으로 지정된 항목만 재계획"
    items = [WorkItem(
        task_id=f"score:r{rnd}:{agent}:{tid}", kind="score", tech_id=tid,
        agent_type=agent, criterion_ids=sorted(set(cids)),
        objective=f"{tid}의 {agent} 관점 평가",
        success_criteria=["근거 ID 인용", "점수 근거 설명", "바로 위 점수 미부여 사유"],
        attempt=rnd,
    ) for (agent, tid), cids in sorted(groups.items())]
    plan = TaskPlan(phase="score", round=rnd, rationale=why, items=items)
    progress.step("orchestrator", f"관점별 채점 계획 {len(items)}개 생성 (round {rnd})")
    return {"task_plan": plan, "plan_history": [plan], "phase": "score_planned"}
