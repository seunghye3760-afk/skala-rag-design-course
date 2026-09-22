"""synthesize(): KV_FAKE 경로 + 실제 LLM 출력 매핑. 담당 4."""
from kv_eval.agents import synthesis as syn_mod
from kv_eval.graph.task_schema import ConflictCandidate


def _state():
    return {
        "technologies": [{"tech_id": "turboquant", "name": "TurboQuant"},
                         {"tech_id": "cxl_pnm", "name": "CXL-PNM"}],
        "rubrics": {"criteria": [{"id": "DOM-4", "name": "품질"}],
                   "comparison_pairs": {"pairs": [{"id": "P1", "theme": "품질", "a": "MKT-2", "b": "DOM-4"}]}},
        "final_results": [],
        "trl_results": [],
        "conflicts": [ConflictCandidate(tech_id="turboquant", comparison_id="P1", status="candidate",
                                        score_gap=2, evidence_refs=["e1"])],
    }


def test_fake_path(monkeypatch):
    monkeypatch.setenv("KV_FAKE", "1")
    out = syn_mod.synthesize(_state())
    assert out["conflicts"][0].interpretation == "(FAKE) 미해석"
    a = out["final_assessment"]
    assert a["turboquant"]["요약"] == "(FAKE) 미구현"
    assert "_cross" in a and "_scenario" in a and "_overall" in a


def test_real_path_maps_llm_output(monkeypatch):
    monkeypatch.delenv("KV_FAKE", raising=False)

    class FakeOut:
        def __init__(self):
            CI = syn_mod._ConflictInterpretation
            TA = syn_mod._TechAssessment
            self.conflicts = [CI(comparison_id="P1", interpretation="조건 차이", note="조건이 다르다")]
            self.tech_assessment = [
                TA(tech_id="turboquant", summary="TRL 5, 시장성 보통", limitations=["당사자 발표 위주"],
                  implications=["운영 전 재현 필요"]),
                TA(tech_id="cxl_pnm", summary="TRL 3, 도메인 미검증", limitations=["시뮬레이션 결과"],
                  implications=["실측 필요"]),
            ]
            self.cross_comparison = syn_mod._CrossComparison(
                관점별_근거_대조="시장성은 둘 다 초기 단계", 트레이드오프="SW는 품질, HW는 비용",
                상호보완_가능성="가설 수준")
            self.scenario_guidance = syn_mod._ScenarioGuidance(
                시나리오1="기존 서버 유지 시 SW 우선 검토", 시나리오2="구조 변경 가능 시 HW 검토",
                이해관계자_충돌="투자자는 낙관, 운영자는 신중", 도입_요인_장벽="표준화 부족이 장벽")
            self.overall = "관점마다 결론이 다르다."

    monkeypatch.setattr(syn_mod, "_get_llm", lambda: type("L", (), {"invoke": lambda self, msgs: FakeOut()})())
    out = syn_mod.synthesize(_state())
    assert out["conflicts"][0].interpretation == "조건 차이 — 조건이 다르다"
    a = out["final_assessment"]
    assert a["turboquant"]["요약"] == "TRL 5, 시장성 보통"
    assert a["_cross"]["트레이드오프"] == "SW는 품질, HW는 비용"
    assert a["_overall"] == "관점마다 결론이 다르다."


def test_interpretation_is_constrained_to_three_values():
    """우열 판정 문구 등은 애초에 pydantic 스키마(Literal)가 막는다 — with_structured_output이
    LLM에게 세 값 중 하나만 함수 호출 스키마로 허용하므로, 여기서는 그 제약만 확인한다."""
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        syn_mod._ConflictInterpretation(comparison_id="P1", interpretation="TurboQuant가 더 우수함", note="x")


def test_by_id_lookup_falls_back_when_llm_skips_a_conflict(monkeypatch):
    """LLM이 상충 후보 하나를 누락해도(conflicts에 없음) 그래프는 죽지 않고 '근거 부족'으로 채운다."""
    monkeypatch.delenv("KV_FAKE", raising=False)

    class FakeOut:
        def __init__(self):
            self.conflicts = []             # P1 누락
            self.tech_assessment = [
                syn_mod._TechAssessment(tech_id="turboquant", summary="s", limitations=[], implications=[]),
                syn_mod._TechAssessment(tech_id="cxl_pnm", summary="s", limitations=[], implications=[]),
            ]
            self.cross_comparison = syn_mod._CrossComparison(관점별_근거_대조="a", 트레이드오프="b",
                                                              상호보완_가능성="c")
            self.scenario_guidance = syn_mod._ScenarioGuidance(시나리오1="a", 시나리오2="b",
                                                                이해관계자_충돌="c", 도입_요인_장벽="d")
            self.overall = "o"

    monkeypatch.setattr(syn_mod, "_get_llm", lambda: type("L", (), {"invoke": lambda self, msgs: FakeOut()})())
    out = syn_mod.synthesize(_state())
    assert out["conflicts"][0].interpretation == "근거 부족"
