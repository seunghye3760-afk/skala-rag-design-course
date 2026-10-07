"""병렬 작업 분배 (설계서 D-1, 그림 2).

- 수집: 최초 36개(항목 18 × 기술 2), 재시도 때는 kind="research" 대상만.
- 채점: 최초 8개(관점 4 × 기술 2), 재시도 때는 retry_targets 항목만 관점·기술별로 묶어서.
"""
from __future__ import annotations

from langgraph.types import Send

from .. import progress
from ..config import criterion
from .reducers import evidence_for
from .state import MainState
from .task_schema import CollectTask, ScoreTask


def _tech(state: MainState, tech_id: str) -> dict:
    return next(t for t in state["technologies"] if t["tech_id"] == tech_id)


def dispatch_collect(state: MainState) -> dict:
    """노드 자체는 하는 일이 없고, 뒤의 fan_out_collect가 Send를 만든다."""
    return {}


def fan_out_collect(state: MainState) -> list[Send]:
    plan = state["task_plan"]
    rnd = plan.round
    rub = state["rubrics"]
    cells = [(_tech(state, item.tech_id), criterion(rub, item.criterion_ids[0]), item.rewrite_hint)
             for item in plan.items if item.kind == "collect"]
    progress.step("dispatch_collect", f"수집 작업 {len(cells)}개 생성 (round {rnd})")
    return [Send("collect_worker", CollectTask(tech=t, criterion=c, round=rnd, rewrite_hint=h))
            for t, c, h in cells]


def score_dispatch(state: MainState) -> dict:
    return {}


def fan_out_score(state: MainState) -> list[Send]:
    plan = state["task_plan"]
    rnd = plan.round
    pool = state.get("evidence_pool", [])
    sends = []
    for item in plan.items:
        if item.kind != "score":
            continue
        agent, tech_id, cids = item.agent_type, item.tech_id, item.criterion_ids
        ev = [e for cid in cids for e in evidence_for(pool, tech_id, cid)]
        sends.append(Send("score_worker", ScoreTask(
            tech=_tech(state, tech_id), agent_type=agent, criterion_ids=cids, evidence=ev,
            tech_brief=state.get("tech_briefs", {}).get(tech_id, {}), round=rnd)))
    progress.step("score_dispatch", f"채점 작업 {len(sends)}개 생성 (round {rnd})")
    return sends


def evidence_join(state: MainState) -> dict:
    """수집 병렬 작업이 모두 끝난 뒤 한 번 실행된다 (join)."""
    progress.step("evidence_join", f"근거 {len(state.get('evidence_pool', []))}건 누적")
    return {"phase": "evidence_ready"}


def score_join(state: MainState) -> dict:
    progress.step("score_join", f"채점 결과 {len(state.get('criterion_results', []))}건 누적")
    return {"phase": "scored"}
