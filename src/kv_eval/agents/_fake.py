"""뼈대 확인용 가짜 채점. 각 관점 에이전트 구현 후 사용 중단.

rationale에 "바로 위 점수를 주지 않은 이유"(다음 점수 숫자)를 흉내만 내는 문구를 넣는다 —
rules/balance.py의 형식 오류 판정(담당 4)이 실제 채점 결과와 동일한 기준으로 이 가짜 결과도
검사하기 때문에, "[FAKE] 미구현"만 있으면 항상 재채점 대상으로 잡혀 KV_FAKE 그래프 테스트가
불필요하게 재시도를 반복한다."""
from __future__ import annotations

from ..graph.task_schema import CriterionResult, EvidenceBrief, ScoreTask


def fake_scores(task: ScoreTask) -> list[CriterionResult]:
    out = []
    for cid in task.criterion_ids:
        ev = [e for e in task.evidence if e.criterion_id == cid]
        score = 3 if ev else "NA"
        rationale = "[FAKE] 미구현"
        if isinstance(score, int) and score < 5:
            rationale += f" ({score + 1}점은 주지 않음 — 실제 채점 미구현 상태의 placeholder)"
        out.append(CriterionResult(
            tech_id=task.tech["tech_id"], criterion_id=cid, agent_type=task.agent_type, round=task.round,
            score=score, confidence="low", rationale=rationale,
            evidence=[EvidenceBrief(evidence_id=e.evidence_id, claim=e.claim, source=e.source_title,
                                    date=e.published_at, grade=e.evidence_grade, stance=e.stance) for e in ev]))
    return out
