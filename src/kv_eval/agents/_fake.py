"""뼈대 확인용 가짜 채점. 각 관점 에이전트 구현 후 사용 중단."""
from __future__ import annotations

from ..graph.task_schema import CriterionResult, EvidenceBrief, ScoreTask


def fake_scores(task: ScoreTask) -> list[CriterionResult]:
    out = []
    for cid in task.criterion_ids:
        ev = [e for e in task.evidence if e.criterion_id == cid]
        out.append(CriterionResult(
            tech_id=task.tech["tech_id"], criterion_id=cid, agent_type=task.agent_type, round=task.round,
            score=3 if ev else "NA", confidence="low", rationale="[FAKE] 미구현",
            evidence=[EvidenceBrief(evidence_id=e.evidence_id, claim=e.claim, source=e.source_title,
                                    date=e.published_at, grade=e.evidence_grade, stance=e.stance) for e in ev]))
    return out
