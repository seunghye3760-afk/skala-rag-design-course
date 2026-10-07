"""채점 작업 분배와 join 노드.

- 수집 분배는 Orchestrator(orchestrator/planner.py)가 계획 기반으로 한다.
- 채점: round 0은 계획에 포함된 셀을 관점·기술별로 묶어서, 재시도 때는 retry_targets 항목만.
"""
from __future__ import annotations

from collections import defaultdict

from langgraph.types import Send

from .. import progress
from ..config import agent_of
from .reducers import evidence_for
from .state import MainState
from .task_schema import ScoreTask


def _tech(state: MainState, tech_id: str) -> dict:
    return next(t for t in state["technologies"] if t["tech_id"] == tech_id)


def score_dispatch(state: MainState) -> dict:
    return {}


def fan_out_score(state: MainState) -> list[Send]:
    rnd = state.get("retry_round", 0)
    rub = state["rubrics"]
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    if rnd == 0:
        for tech_id, cid in state["plan"].cells():
            groups[(agent_of(rub, cid), tech_id)].append(cid)
    else:
        for x in state.get("retry_targets", []):
            groups[(agent_of(rub, x.criterion_id), x.tech_id)].append(x.criterion_id)
    pool = state.get("evidence_pool", [])
    sends = []
    for (agent, tech_id), cids in groups.items():
        ev = [e for cid in cids for e in evidence_for(pool, tech_id, cid)]
        sends.append(Send("score_task", ScoreTask(
            tech=_tech(state, tech_id), agent_type=agent, criterion_ids=cids, evidence=ev,
            tech_brief=state.get("tech_briefs", {}).get(tech_id, {}), round=rnd)))
    progress.step("score_dispatch", f"채점 작업 {len(sends)}개 생성 (round {rnd})")
    return sends


def evidence_join(state: MainState) -> dict:
    """Worker가 모두 끝난 뒤 한 번 실행된다 (join)."""
    rnd = state.get("retry_round", 0)
    outs = [o for o in state.get("worker_outcomes", []) if o.round == rnd]
    excluded = [o.subtask_id for o in outs if o.status == "excluded"]
    progress.step("evidence_join", f"round {rnd} Worker {len(outs)}개 완료 (제외 {len(excluded)}) · "
                  f"근거 {len(state.get('evidence_pool', []))}건 누적")
    return {}


def score_join(state: MainState) -> dict:
    progress.step("score_join", f"채점 결과 {len(state.get('criterion_results', []))}건 누적")
    return {}
