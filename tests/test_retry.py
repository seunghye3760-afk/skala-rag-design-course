"""균형 점검 재시도: 걸린 항목만 다시 수집·채점하고, 이전 round 근거는 유지되는지."""
from kv_eval.evidence import collect as collect_mod
from kv_eval.graph.main import build_graph


def test_retry_only_targets(monkeypatch, fake_market_and_stakeholder_scoring):
    original = collect_mod.fake_evidence

    def flaky(task):
        ev = original(task)
        if task.round == 0 and task.tech["tech_id"] == "turboquant" and task.criterion["id"] == "MKT-2":
            return [e for e in ev if e.stance == "pro"]      # 비판 근거 없음 → 편향
        return ev

    monkeypatch.setattr(collect_mod, "fake_evidence", flaky)
    final = build_graph().invoke({"run_id": "r", "only_criteria": ["MKT-2", "DOM-4"]})
    assert final["retry_round"] == 1
    retried = [s for s in final["search_log"] if s["round"] == 1]
    assert [(s["tech_id"], s["criterion_id"]) for s in retried] == [("turboquant", "MKT-2")]
    pool = [e for e in final["evidence_pool"] if e.tech_id == "turboquant" and e.criterion_id == "MKT-2"]
    assert {e.round for e in pool} == {0, 1}                  # 누적 근거 유지
