"""보고서 품질 평가: 규칙 검사, judge 인용 검증, 경로 결정, 미달 시 루프."""
from kv_eval.agents import synthesis as synth_mod
from kv_eval.config import rubrics, runtime, technologies_config
from kv_eval.graph.main import build_graph
from kv_eval.graph.task_schema import CriterionResult, Evidence, EvidenceBrief, Locator
from kv_eval.quality import checks, evaluate
from kv_eval.quality import judge as judge_mod
from kv_eval.quality.schema import AxisVerdict, QualityIssue

CFG = runtime()["quality"]


def _ev(eid, tech="turboquant", cid="MKT-1", stance="pro", pub="P", claim="throughput 2.5x at 32K"):
    return Evidence(evidence_id=eid, tech_id=tech, criterion_id=cid, claim=claim, source_title=eid,
                    publisher=pub, accessed_at="2026-10-07", source_type="논문", evidence_grade="B",
                    stance=stance, measurement_type="실측", excerpt=claim, locator=Locator())


def _res(tech, cid, agent, evs, score=3):
    return CriterionResult(tech_id=tech, criterion_id=cid, agent_type=agent, score=score, confidence="medium",
                           rationale="r", evidence=[EvidenceBrief(evidence_id=e.evidence_id, claim=e.claim,
                                                                  source=e.source_title, grade="B",
                                                                  stance=e.stance) for e in evs])


def _state(results, pool, assessment=None):
    cfg = technologies_config()
    return {"technologies": cfg["technologies"], "rubrics": rubrics(), "final_results": results,
            "evidence_pool": pool, "final_assessment": assessment or {}, "conflicts": []}


def test_neutrality_rule_flags_superiority():
    st = _state([], [], {"_overall": "TurboQuant가 CXL-PNM보다 더 유망하다. 두 기술은 조건이 다르다."})
    issues = checks.check_neutrality(st)
    assert len(issues) == 1 and "유망" in issues[0].quote


def test_groundedness_flags_unsupported_number():
    e = _ev("e1")
    st = _state([_res("turboquant", "MKT-1", "market", [e])], [e],
                {"turboquant": {"요약": "처리량이 2.5배 개선된다. 비용은 40% 절감된다."}})
    issues = checks.check_groundedness(st, "## SUMMARY\n## REFERENCE\n- ref\n")
    assert [i.detail for i in issues] == ["근거에 없는 수치 ['40']"]


def test_groundedness_flags_missing_citation_cell():
    st = _state([_res("cxl_pnm", "DOM-1", "domain", [])], [])
    (issue,) = checks.check_groundedness(st, "## REFERENCE\n")
    assert issue.is_cell and issue.criterion_id == "DOM-1"


def test_bias_flags_single_publisher_cells():
    evs = [_ev(f"e{i}", cid=f"MKT-{i}", pub="Vendor") for i in range(1, 5)]
    st = _state([_res("turboquant", e.criterion_id, "market", [e]) for e in evs], evs)
    issues = checks.check_bias(st, {**CFG, "min_cited_ratio_between_techs": 0})
    assert {i.criterion_id for i in issues} == {"MKT-1", "MKT-2", "MKT-3", "MKT-4"}
    assert "발행 주체 1곳뿐" in issues[0].detail and "비판 근거 비중 0%" in issues[0].detail


def test_coverage_flags_all_na_perspective():
    st = _state([_res("cxl_pnm", "STK-1", "stakeholder", [], score="NA"),
                 _res("cxl_pnm", "STK-2", "stakeholder", [], score="NA")], [])
    st["only_criteria"] = ["STK-1", "STK-2"]
    issues = checks.check_coverage(st, "## SUMMARY\n## REFERENCE\n")
    assert {(i.tech_id, i.criterion_id) for i in issues} == {("cxl_pnm", "STK-1"), ("cxl_pnm", "STK-2")}


def test_judge_drops_unverifiable_quotes(monkeypatch):
    class FakeLLM:
        def with_structured_output(self, schema):
            return self

        def invoke(self, _):
            return judge_mod._Judgement(
                axes=[judge_mod._AxisScore(axis="neutrality", score=2, comment="c")],
                violations=[judge_mod._Violation(axis="neutrality", quote="도입을 먼저 검토할 만하다",
                                                 problem="추천 암시"),
                            judge_mod._Violation(axis="neutrality", quote="보고서에 없는 지어낸 문장입니다",
                                                 problem="환각")])

    monkeypatch.setattr("kv_eval.llm.chat_model", lambda: FakeLLM())
    st = _state([], [], {"_overall": "TurboQuant는 시나리오1에서 도입을 먼저 검토할 만하다."})
    scores, issues, dropped = judge_mod.judge(st)
    assert scores == {"neutrality": 2} and dropped == 1
    assert issues[0].quote == "도입을 먼저 검토할 만하다"


def test_decide_paths():
    cell = QualityIssue(axis="bias", source="rule", detail="d", tech_id="turboquant", criterion_id="MKT-1")
    text = QualityIssue(axis="neutrality", source="rule", detail="d", quote="q")
    ok = AxisVerdict(axis="coverage", passed=True, rule_passed=True)
    bad = lambda i: AxisVerdict(axis=i.axis, passed=False, rule_passed=False, issues=[i])  # noqa: E731
    assert evaluate._decide([ok], 0, 2)[0] == "pass"
    assert evaluate._decide([bad(cell), bad(text)], 0, 2)[0] == "replan"
    assert evaluate._decide([bad(text)], 0, 2)[0] == "resynthesize"
    assert evaluate._decide([bad(cell)], 2, 2)[0] == "give_up"


def test_resynthesize_loop_passes_feedback(monkeypatch):
    seen = []
    original = synth_mod._fake

    def biased_once(state):
        seen.append(state.get("quality_feedback"))
        out = original(state)
        if len(seen) == 1:
            out["final_assessment"]["_overall"] = "TurboQuant가 더 우수하다."
        return out

    monkeypatch.setattr(synth_mod, "_fake", biased_once)
    final = build_graph().invoke({"run_id": "q", "only_criteria": ["MKT-2"]})
    assert len(seen) == 2 and "우열" in seen[1][0]              # 2번째 종합에 지적 사항 전달
    assert final["quality_round"] == 2 and final["eval_result"].action == "pass"


def test_replan_loop_then_pass(monkeypatch):
    calls = {"n": 0}
    original = checks.check_coverage

    def gap_once(state, report):
        calls["n"] += 1
        if calls["n"] == 1:
            return [QualityIssue(axis="coverage", source="rule", detail="cxl_pnm market 전 항목 NA",
                                 tech_id="cxl_pnm", criterion_id="MKT-2")]
        return original(state, report)

    monkeypatch.setattr(evaluate, "check_coverage", gap_once)
    final = build_graph().invoke({"run_id": "p", "only_criteria": ["MKT-2"]})
    r1 = [o.subtask_id for o in final["worker_outcomes"] if o.round == 1]
    assert r1 == ["r1:cxl_pnm:MKT-2:web"]                     # 품질 미달 셀만 재계획
    assert final["eval_result"].action == "pass" and final["quality_round"] == 2


def test_quality_loop_terminates(monkeypatch):
    always = [QualityIssue(axis="neutrality", source="rule", detail="d", quote="q")]
    monkeypatch.setattr(evaluate, "check_neutrality", lambda state: always)
    final = build_graph().invoke({"run_id": "g", "only_criteria": ["MKT-2"]})
    assert final["eval_result"].action == "give_up"
    assert final["quality_round"] == CFG["max_rounds"] + 1
