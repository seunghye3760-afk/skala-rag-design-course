"""누적된 결과에서 '지금 쓸 값'을 고르는 규칙 (설계서 D-2 주석).

- 근거(evidence_pool): 모든 round를 합치고 중복 제거해서 쓴다.
- 채점(criterion_results): (기술, 항목)별 최신 round만 쓴다.
"""
from __future__ import annotations

from .task_schema import CriterionResult, Evidence, cell_key


def latest_results(results: list[CriterionResult]) -> dict[str, CriterionResult]:
    out: dict[str, CriterionResult] = {}
    for r in results:
        k = cell_key(r.tech_id, r.criterion_id)
        if k not in out or r.round >= out[k].round:
            out[k] = r
    return out


def evidence_for(pool: list[Evidence], tech_id: str, criterion_id: str) -> list[Evidence]:
    seen: set[str] = set()
    out: list[Evidence] = []
    for e in pool:
        if e.tech_id != tech_id or e.criterion_id != criterion_id or not e.relevant:
            continue
        key = e.duplicate_group or e.evidence_id
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out
