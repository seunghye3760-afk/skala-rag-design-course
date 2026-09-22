"""점수 상한·대칭 하한·recency 제한 (설계서 C-4·C-5). TRL 차원은 서열 척도라 적용하지 않는다."""
from __future__ import annotations

from datetime import date, timedelta

from ..graph.task_schema import CriterionResult, Evidence

CAP = {"A": 5, "B": 4, "C": 3, "D": 3}
ORDER = "ABCD"

# 전망성(미래 서술) 측정 유형 — collect._Item 어휘 기준 부분 일치
_FORWARD = ("추정", "의견", "전망", "예측", "로드맵", "forecast", "projection", "opinion")
_RECENCY_DAYS = 548   # 18개월


def _stale_forward_only(evidence: list[Evidence]) -> bool:
    """긍정 근거가 전부 '18개월 넘은(또는 날짜 미상) 전망성 자료'뿐인가 (설계서 C-5 recency).

    실측·발표 등 비전망 근거가 하나라도 있거나, 18개월 이내 전망 자료가 있으면 해당 없음.
    날짜 미상 전망 자료는 보수적으로 오래된 것으로 취급한다."""
    pro = [e for e in evidence if e.stance == "pro"]
    if not pro:
        return False
    cutoff = (date.today() - timedelta(days=_RECENCY_DAYS)).isoformat()
    for e in pro:
        mt = e.measurement_type.lower()
        if not any(k in mt for k in _FORWARD):
            return False
        if e.published_at and e.published_at >= cutoff:
            return False
    return True


def apply_caps(result: CriterionResult, evidence: list[Evidence]) -> CriterionResult:
    raw = result.score
    if result.agent_type == "trl" or raw == "NA" or not evidence:
        return result.model_copy(update={"raw_score": raw})
    notes = []
    best = min((e.evidence_grade for e in evidence), key=ORDER.index)
    score = min(raw, CAP[best])
    if score < raw:
        notes.append(f"{best} 최고 등급 → 최대 {CAP[best]}")
    if score >= 4 and _stale_forward_only(evidence):
        score = 3
        notes.append("18개월 초과 전망 자료뿐 → 최대 3")
    strong_con = any(e.stance == "con" and e.evidence_grade in "AB" for e in evidence)
    if score < 2 and not strong_con:
        score = 2
        notes.append("A·B등급 부정 근거 없음 → 최저 2")
    return result.model_copy(update={"raw_score": raw, "score": score,
                                     "cap_applied": " / ".join(notes) or None})
