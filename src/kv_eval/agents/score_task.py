"""score_task 노드: 네 관점을 같은 노드로 호출 (agent_type만 다름). 담당 4."""
from __future__ import annotations

from ..config import rubrics
from ..graph.task_schema import ScoreTask
from . import domain, market, stakeholder, trl

AGENTS = {"trl": trl, "market": market, "stakeholder": stakeholder, "domain": domain}


def score_task(task: ScoreTask) -> dict:
    results = AGENTS[task.agent_type].score(task, rubrics())
    # TODO(담당 4): JSON 스키마 불일치 시 즉시 1회 재요청 (설계서 D-10)
    return {"criterion_results": results}
