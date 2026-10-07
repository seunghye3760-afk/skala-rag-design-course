"""State를 읽어 현재 round의 수집·채점 작업 계획을 결정적으로 만든다."""
from __future__ import annotations

from collections import defaultdict

from .. import progress
from ..config import agent_of, criteria_list, criterion
from .reducers import evidence_for
from .state import MainState
from .task_schema import CollectTask, ScoreTask, WorkPlan


def _tech(state: MainState, tech_id: str) -> dict:
    return next(t for t in state["technologies"] if t["tech_id"] == tech_id)


def plan_collect_work(state: MainState) -> dict:
    """최초에는 선택한 전체 셀을, 재시도에는 research 대상 셀만 계획한다."""
    rnd = state.get("retry_round", 0)
    rub = state["rubrics"]
    tasks: list[CollectTask] = []

    if rnd == 0:
        for tech in state["technologies"]:
            for crit in criteria_list(rub, state.get("only_criteria")):
                tasks.append(CollectTask(tech=tech, criterion=crit, round=rnd))
        reason = "initial"
    else:
        for target in state.get("retry_targets", []):
            if target.kind != "research":
                continue
            tasks.append(CollectTask(
                tech=_tech(state, target.tech_id),
                criterion=criterion(rub, target.criterion_id),
                round=rnd,
                rewrite_hint=target.hint,
            ))
        reason = "research_retry"

    progress.step("plan_collect_work", f"수집 작업 {len(tasks)}개 계획 (round {rnd})")
    return {"work_plan": WorkPlan(collect_tasks=tasks, round=rnd, reason=reason)}


def plan_score_work(state: MainState) -> dict:
    """기술·관점별 ScoreTask를 만들고, 재시도에는 지정된 항목만 포함한다."""
    rnd = state.get("retry_round", 0)
    rub = state["rubrics"]
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)

    if rnd == 0:
        for tech in state["technologies"]:
            for crit in criteria_list(rub, state.get("only_criteria")):
                groups[(crit["agent"], tech["tech_id"])].append(crit["id"])
        reason = "initial"
    else:
        for target in state.get("retry_targets", []):
            groups[(agent_of(rub, target.criterion_id), target.tech_id)].append(target.criterion_id)
        reason = "score_retry"

    pool = state.get("evidence_pool", [])
    tasks = []
    for (agent, tech_id), criterion_ids in groups.items():
        evidence = [e for cid in criterion_ids for e in evidence_for(pool, tech_id, cid)]
        tasks.append(ScoreTask(
            tech=_tech(state, tech_id),
            agent_type=agent,
            criterion_ids=criterion_ids,
            evidence=evidence,
            tech_brief=state.get("tech_briefs", {}).get(tech_id, {}),
            round=rnd,
        ))

    progress.step("plan_score_work", f"채점 작업 {len(tasks)}개 계획 (round {rnd})")
    return {"work_plan": WorkPlan(score_tasks=tasks, round=rnd, reason=reason)}
