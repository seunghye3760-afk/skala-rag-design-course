from kv_eval.graph.task_schema import CriterionResult, Evidence, Locator
from kv_eval.rules.caps import apply_caps
from kv_eval.rules.trl_gate import trl_level


def ev(grade, stance):
    return Evidence(evidence_id=f"{grade}{stance}", tech_id="turboquant", criterion_id="DOM-4", claim="c",
                    source_title="s", publisher="p", accessed_at="2026-09-22", source_type="t",
                    evidence_grade=grade, stance=stance, measurement_type="m", excerpt="e", locator=Locator())


def res(score, agent="domain"):
    return CriterionResult(tech_id="turboquant", criterion_id="DOM-4", agent_type=agent, score=score,
                           rationale="r", confidence="low")


def test_cap_by_best_grade():
    r = apply_caps(res(5), [ev("B", "pro"), ev("C", "con")])
    assert r.score == 4 and r.raw_score == 5


def test_symmetric_floor():
    assert apply_caps(res(1), [ev("C", "pro"), ev("D", "con")]).score == 2
    assert apply_caps(res(1), [ev("C", "pro"), ev("A", "con")]).score == 1


def test_trl_dimension_not_capped():
    assert apply_caps(res(5, agent="trl"), [ev("D", "pro")]).score == 5


def test_trl_gates():
    assert trl_level({"TRL-1": 2})[0] == 3
    assert trl_level({"TRL-1": 4, "TRL-2": 2, "TRL-3": 3})[0] == 6
    assert trl_level({"TRL-1": 4, "TRL-2": "NA", "TRL-3": 2})[0] == 5
    assert trl_level({"TRL-1": 5, "TRL-3": 4, "TRL-4": 5, "TRL-5": 5})[0] == 9
