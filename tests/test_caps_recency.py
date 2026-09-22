"""recency 상한 (설계서 C-5): 18개월 넘은 전망 자료만으로는 4점 이상 불가. 담당 2 추가."""
from kv_eval.graph.task_schema import CriterionResult, Evidence, Locator
from kv_eval.rules.caps import apply_caps


def _ev(stance="pro", grade="B", measurement="의견", published="2024-01-01"):
    return Evidence(evidence_id=f"e-{stance}-{measurement}-{published}", tech_id="turboquant",
                    criterion_id="MKT-4", claim="c", source_title="s", publisher="p",
                    published_at=published, accessed_at="2026-09-22", source_type="보도자료",
                    evidence_grade=grade, stance=stance, measurement_type=measurement,
                    excerpt="e", locator=Locator())


def _res(score):
    return CriterionResult(tech_id="turboquant", criterion_id="MKT-4", agent_type="market",
                           score=score, rationale="r", confidence="medium")


def test_stale_forecast_only_capped_to_3():
    out = apply_caps(_res(4), [_ev(measurement="의견", published="2024-01-01"),
                               _ev(stance="con", grade="D", measurement="의견", published="2024-02-01")])
    assert out.score == 3 and "18개월" in out.cap_applied


def test_undated_forecast_treated_as_stale():
    out = apply_caps(_res(4), [_ev(measurement="추정", published=None)])
    assert out.score == 3


def test_recent_forecast_not_capped():
    out = apply_caps(_res(4), [_ev(measurement="의견", published="2026-05-01")])
    assert out.score == 4


def test_measured_evidence_disables_recency_cap():
    out = apply_caps(_res(4), [_ev(measurement="의견", published="2024-01-01"),
                               _ev(measurement="실측", published="2024-01-01")])
    assert out.score == 4


def test_low_score_unaffected():
    out = apply_caps(_res(3), [_ev(measurement="의견", published="2024-01-01")])
    assert out.score == 3 and (out.cap_applied is None or "18개월" not in out.cap_applied)
