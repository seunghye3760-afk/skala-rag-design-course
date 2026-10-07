"""plan_tasks 동적 fan-out + collect_worker fallback (KV_FAKE=1, conftest가 설정)."""
from kv_eval.config import rubrics, technologies_config
from kv_eval.orchestrator import dispatch as dispatch_mod
from kv_eval.orchestrator.graph import build_orchestrator_graph
from kv_eval.orchestrator.plan import plan_tasks
from kv_eval.orchestrator.state import SubTask


def _state(only, briefs=None):
    cfg = technologies_config()
    return {"run_id": "t", "technologies": cfg["technologies"], "rubrics": rubrics(), "only_criteria": only,
            "tech_briefs": briefs or {}, "task_plan": [], "retry_targets": [], "quality_verdict": None}


def test_initial_plan_size_follows_criteria():
    out = plan_tasks(_state(["TRL-1", "DOM-4"]))
    plan = out["task_plan"]
    assert len(plan) == 4 and all(t.status == "pending" and t.round == 0 for t in plan)
    assert {t.task_id for t in plan} == {"turboquant:TRL-1:r0", "turboquant:DOM-4:r0",
                                         "cxl_pnm:TRL-1:r0", "cxl_pnm:DOM-4:r0"}
    assert out["fanout_log"][0]["count"] == 4
    assert len(plan_tasks(_state(["TRL-1"]))["task_plan"]) == 2      # 입력이 다르면 fan-out 수가 다르다


def test_channels_depend_on_tech_brief_and_rubric():
    briefs = {"turboquant": {"실험_조건": {"value": "", "status": "미보고"}},
              "cxl_pnm": {"실험_조건": {"value": "A100, 32k ctx", "status": "reported"}}}
    plan = {t.task_id: t for t in plan_tasks(_state(["TRL-1", "MKT-1", "TRL-2"], briefs))["task_plan"]}
    assert "paper" not in plan["turboquant:TRL-1:r0"].channels      # brief 미보고 → 논문 채널 제외
    assert "paper" in plan["cxl_pnm:TRL-1:r0"].channels             # 같은 항목이라도 기술별로 다름
    assert plan["turboquant:MKT-1:r0"].channels == ["web"]          # 시장 항목은 웹만 (루브릭에 RAG 없음)
    assert "open_source" in plan["cxl_pnm:TRL-2:r0"].channels       # 저장소 관련 항목
    assert "미보고" in plan["turboquant:TRL-1:r0"].reason


def test_retry_mode_plans_only_research_targets():
    from kv_eval.graph.task_schema import RetryTarget

    st = _state(["TRL-1", "DOM-4"])
    st["task_plan"] = [SubTask(task_id="turboquant:TRL-1:r0", tech_id="turboquant", criterion_id="TRL-1",
                               channels=["web"], status="done")]
    st["retry_targets"] = [RetryTarget(tech_id="cxl_pnm", criterion_id="DOM-4", kind="research", reason="근거 공백"),
                           RetryTarget(tech_id="cxl_pnm", criterion_id="TRL-1", kind="rescore", reason="형식")]
    plan = plan_tasks(st)["task_plan"]
    assert [t.task_id for t in plan] == ["cxl_pnm:DOM-4:r1"] and plan[0].reason.startswith("[retry]")


def test_failed_worker_is_excluded_and_others_continue(monkeypatch):
    calls = {"n": 0}
    original = dispatch_mod.collect_evidence

    def flaky(task):
        if task.round == 0 and task.tech["tech_id"] == "cxl_pnm" and task.criterion["id"] == "DOM-4":
            calls["n"] += 1
            raise RuntimeError("tavily down")
        return original(task)

    monkeypatch.setattr(dispatch_mod, "collect_evidence", flaky)
    final = build_orchestrator_graph().invoke({"run_id": "f", "only_criteria": ["TRL-1", "DOM-4"]},
                                              config={"configurable": {"thread_id": "f"}})
    assert calls["n"] == 2                                            # 1회 재시도 후 포기
    assert final["node_status"]["cxl_pnm:DOM-4:r0"] == "excluded"
    assert final["node_status"].get("cxl_pnm:DOM-4:r1") == "done"     # 균형 점검이 공백을 잡아 다음 라운드에 재수집
    assert [e["task_id"] for e in final["errors"]] == ["cxl_pnm:DOM-4:r0"] and final["errors"][0]["type"] == "RuntimeError"
    assert sum(1 for t in final["task_plan"] if t.status == "done") >= 3  # 나머지 브랜치는 정상 누적
    assert any(e.tech_id == "turboquant" and e.criterion_id == "DOM-4" for e in final["evidence_pool"])
    assert any(g.tech_id == "cxl_pnm" and g.criterion_id == "DOM-4" for g in final["info_gaps"])
    assert final["status"] == "PARTIAL"
