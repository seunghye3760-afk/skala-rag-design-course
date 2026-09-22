"""병렬 작업 분배 (설계서 D-1, 그림 2).

- 수집: 최초 36개(항목 18 × 기술 2), 재시도 때는 kind="research" 대상만.
- 채점: 최초 8개(관점 4 × 기술 2), 재시도 때는 retry_targets 항목만 관점·기술별로 묶어서.
"""
from __future__ import annotations

from collections import defaultdict

from langgraph.types import Send

from ..config import agent_of, criteria_list, criterion
from .reducers import evidence_for
from .state import MainState
from .task_schema import CollectTask, ScoreTask


def _tech(state: MainState, tech_id: str) -> dict:
    return next(t for t in state["technologies"] if t["tech_id"] == tech_id)


def dispatch_collect(state: MainState) -> dict:
    """노드 자체는 하는 일이 없고, 뒤의 fan_out_collect가 Send를 만든다."""
    return {}


def fan_out_collect(state: MainState) -> list[Send]:
    rnd = state.get("retry_round", 0)
    rub = state["rubrics"]
    if rnd == 0:
        cells = [(t, c, None) for t in state["technologies"]
                 for c in criteria_list(rub, state.get("only_criteria"))]
    else:
        cells = [(_tech(state, x.tech_id), criterion(rub, x.criterion_id), x.hint)
                 for x in state.get("retry_targets", []) if x.kind == "research"]
    return [Send("collect_evidence", CollectTask(tech=t, criterion=c, round=rnd, rewrite_hint=h))
            for t, c, h in cells]


def score_dispatch(state: MainState) -> dict:
    return {}


def fan_out_score(state: MainState) -> list[Send]:
    rnd = state.get("retry_round", 0)
    rub = state["rubrics"]
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    if rnd == 0:
        for t in state["technologies"]:
            for c in criteria_list(rub, state.get("only_criteria")):
                groups[(c["agent"], t["tech_id"])].append(c["id"])
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
    return sends


def evidence_join(state: MainState) -> dict:
    """수집 병렬 작업이 모두 끝난 뒤 한 번 실행된다 (join)."""
    return {}


def score_join(state: MainState) -> dict:
    return {}
