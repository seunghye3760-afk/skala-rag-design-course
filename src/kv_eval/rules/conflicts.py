"""관점 간 상충 후보 추출 (설계서 D-11). 자동 판정이 아니라 후보만 뽑는다."""
from __future__ import annotations

from ..graph.task_schema import ConflictCandidate, CriterionResult, cell_key


def conflict_candidates(tech_id: str, finals: dict[str, CriterionResult], pairs: list[dict]) -> list[ConflictCandidate]:
    out = []
    for p in pairs:
        a, b = finals.get(cell_key(tech_id, p["a"])), finals.get(cell_key(tech_id, p["b"]))
        if a is None or b is None:
            continue                                   # 작게 돌린 실행에서 빠진 항목
        refs = [p["a"], p["b"]]
        if a.score == "NA" or b.score == "NA":
            out.append(ConflictCandidate(tech_id=tech_id, comparison_id=p["id"], status="info_gap", evidence_refs=refs))
        elif abs(a.score - b.score) >= 2:
            out.append(ConflictCandidate(tech_id=tech_id, comparison_id=p["id"], status="candidate",
                                         score_gap=abs(a.score - b.score), evidence_refs=refs))
    return out
