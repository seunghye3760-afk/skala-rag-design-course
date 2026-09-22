"""TRL 1~9 산출 (설계서 C-4 TRL 산출 규칙). rubrics.json trl_mapping과 같은 내용이다.
규칙을 바꾸면 rubrics.json과 설계서도 같이 바꾼다."""
from __future__ import annotations

from ..graph.task_schema import Evidence, TRLResult


def _ge(s: dict, k: str, v: int) -> bool:
    x = s.get(k)
    return isinstance(x, int) and x >= v          # NA는 조건 불충족 (보수적 하한)


def _eq(s: dict, k: str, v: int) -> bool:
    return s.get(k) == v


GATES = [
    (2, "TRL-1 ≥ 1", lambda s: _ge(s, "TRL-1", 1)),
    (3, "TRL-1 ≥ 2", lambda s: _ge(s, "TRL-1", 2)),
    (4, "TRL-1 ≥ 3", lambda s: _ge(s, "TRL-1", 3)),
    (5, "TRL-1 ≥ 4", lambda s: _ge(s, "TRL-1", 4)),
    (6, "TRL-1 ≥ 4 AND (TRL-2 ≥ 4 OR TRL-3 ≥ 3)",
     lambda s: _ge(s, "TRL-1", 4) and (_ge(s, "TRL-2", 4) or _ge(s, "TRL-3", 3))),
    (7, "TRL-1 = 5 AND TRL-4 ≥ 4", lambda s: _eq(s, "TRL-1", 5) and _ge(s, "TRL-4", 4)),
    (8, "TRL-1 = 5 AND TRL-3 ≥ 4 AND TRL-4 ≥ 4 AND TRL-5 ≥ 4",
     lambda s: _eq(s, "TRL-1", 5) and _ge(s, "TRL-3", 4) and _ge(s, "TRL-4", 4) and _ge(s, "TRL-5", 4)),
    (9, "TRL-1 = 5 AND TRL-3 ≥ 4 AND TRL-4 = 5 AND TRL-5 = 5",
     lambda s: _eq(s, "TRL-1", 5) and _ge(s, "TRL-3", 4) and _eq(s, "TRL-4", 5) and _eq(s, "TRL-5", 5)),
]


def trl_level(scores: dict) -> tuple[int, list[str]]:
    """모든 조건을 만족하는 가장 높은 단계. scores 예: {"TRL-1": 4, "TRL-2": "NA", ...}"""
    level, trace = 1, []
    for lvl, text, ok in GATES:
        passed = ok(scores)
        trace.append(f"TRL {lvl} 조건 {'충족' if passed else '미충족'}: {text}")
        if passed:
            level = lvl
    return level, trace


def trl_confidence(scores: dict, evidence: list[Evidence]) -> str:
    grades = {e.evidence_grade for e in evidence}
    na = sum(1 for v in scores.values() if v == "NA")
    if "D" in grades or na >= 2:
        return "low"
    if "A" in grades and grades <= {"A", "B"} and na == 0:
        return "high"
    return "medium"


def compute_trl(tech_id: str, scores: dict, evidence: list[Evidence]) -> TRLResult:
    level, trace = trl_level(scores)
    comp = scores.get("TRL-5", "NA")
    return TRLResult(tech_id=tech_id, trl_level=level, trl_confidence=trl_confidence(scores, evidence),
                     gate_trace=trace, component_maturity=f"{comp}/5")
