"""evaluate_report / judge_node / route_after_quality / finalize (KV_FAKE=1).
(a) 통과, (b) 금지 표현 주입 → neutrality 미달 → synthesize_report 재실행 후 통과, (c) 항상 미달 → 한도에서 정상 종료."""
import json
from pathlib import Path

from kv_eval.agents import synthesis as syn_mod
from kv_eval.config import output_root
from kv_eval.orchestrator import quality as q
from kv_eval.orchestrator.graph import build_orchestrator_graph
from kv_eval.orchestrator.state import QualityVerdict

CFG = {"configurable": {"thread_id": "q"}}


def _run(run_id):
    return build_orchestrator_graph().invoke({"run_id": run_id, "only_criteria": ["TRL-1", "DOM-4"]}, config=CFG)


def _decisions(run_id):
    return [json.loads(l)["decision"] for l in (output_root() / run_id / "decision_log.jsonl").read_text().splitlines()]


def test_pass_case():
    final = _run("qa")
    v = final["quality_verdict"]
    assert v.passed and v.judge_called and all(v.rule_pass.values()) and v.judge_scores == {k: 5 for k in q.ITEMS}
    assert final["quality_round"] == 1 and final["status"] == "SUCCESS"
    assert "judge_pass" in _decisions("qa")


def test_forbidden_phrase_triggers_regeneration(monkeypatch):
    original, calls = syn_mod._fake, {"n": 0}

    def biased_once(state):
        out = original(state)
        calls["n"] += 1
        if calls["n"] == 1:
            out["final_assessment"]["_overall"] = "TurboQuant가 CXL-PNM보다 더 우수하므로 추천한다."
        return out

    monkeypatch.setattr(syn_mod, "_fake", biased_once)
    final = _run("qb")
    assert calls["n"] == 2                                   # 1회 재생성
    assert final["quality_round"] == 2 and final["quality_verdict"].passed
    d = _decisions("qb")
    assert "rules_fail" in d and "regenerate_with_feedback" in d and d.index("rules_fail") < d.index("regenerate_with_feedback")
    assert "더 우수" not in Path(final["report_path"]).read_text(encoding="utf-8")
    assert "더 우수" in final["synthesis_feedback"] or "금지 표현" in final["synthesis_feedback"]


def test_limit_reached_terminates_normally(monkeypatch):
    original = syn_mod._fake

    def always_biased(state):
        out = original(state)
        out["final_assessment"]["_overall"] = "결론: 승자는 TurboQuant이며 총점이 더 높다."
        return out

    monkeypatch.setattr(syn_mod, "_fake", always_biased)
    final = _run("qc")
    assert final["quality_round"] == q.max_quality_rounds() == 2    # 상한은 코드 상수
    assert not final["quality_verdict"].passed and final["status"] == "PARTIAL"
    text = Path(final["report_path"]).read_text(encoding="utf-8")
    heads = [l for l in text.splitlines() if l.startswith("## ")]
    assert heads[0] == "## SUMMARY" and heads[-1] == "## REFERENCE"
    assert "### 품질 평가 미달 항목" in text.split("## 1. 개요")[0]      # SUMMARY 끝에 기록
    meta = json.loads((output_root() / "qc" / "report_meta.json").read_text())
    assert meta["status"] == "PARTIAL" and meta["quality_verdict"]["failed_items"] == ["neutrality"]


def test_route_is_pure_function():
    base = {"round": 1, "rule_pass": {k: True for k in q.ITEMS}}
    s = {"quality_round": 1, "quality_verdict": QualityVerdict(**base, passed=True)}
    assert q.route_after_quality(s) == "finalize"
    s["quality_verdict"] = QualityVerdict(**base, failed_items=["neutrality"])
    assert q.route_after_quality(s) == "synthesize_report"
    s["quality_verdict"] = QualityVerdict(**base, failed_items=["bias"], failed_cells=["turboquant:TRL-1"])
    assert q.route_after_quality(s) == "plan_tasks"
    s["quality_verdict"] = QualityVerdict(**base, failed_items=["coverage"], failed_cells=["cxl_pnm:MKT-1"])
    assert q.route_after_quality(s) == "score_dispatch"
    s["quality_round"] = q.max_quality_rounds()
    assert q.route_after_quality(s) == "finalize"                     # 한도 도달 → 종료
    assert q.judge_node is not q.route_after_quality                  # Judge(판정)와 Gate(라우팅) 분리
