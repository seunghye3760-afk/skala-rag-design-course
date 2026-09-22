"""점수 상한·대칭 하한 (설계서 C-4). TRL 차원은 서열 척도라 적용하지 않는다."""
from __future__ import annotations

from ..graph.task_schema import CriterionResult, Evidence

CAP = {"A": 5, "B": 4, "C": 3, "D": 3}
ORDER = "ABCD"


def apply_caps(result: CriterionResult, evidence: list[Evidence]) -> CriterionResult:
    raw = result.score
    if result.agent_type == "trl" or raw == "NA" or not evidence:
        return result.model_copy(update={"raw_score": raw})
    notes = []
    best = min((e.evidence_grade for e in evidence), key=ORDER.index)
    score = min(raw, CAP[best])
    if score < raw:
        notes.append(f"{best} 최고 등급 → 최대 {CAP[best]}")
    strong_con = any(e.stance == "con" and e.evidence_grade in "AB" for e in evidence)
    if score < 2 and not strong_con:
        score = 2
        notes.append("A·B등급 부정 근거 없음 → 최저 2")
    return result.model_copy(update={"raw_score": raw, "score": score,
                                     "cap_applied": " / ".join(notes) or None})
