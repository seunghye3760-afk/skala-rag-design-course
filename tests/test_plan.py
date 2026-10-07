"""plan_tasks(LLM 플래너, FAKE 모드) 동적 fan-out + 코드 가드 + collect_worker fallback (KV_FAKE=1, conftest가 설정).
FAKE 플래너: RAG 항목은 쿼리 템플릿 2쌍(→ 셀당 worker 2개), 그 외 1쌍. brief 실험_조건이 미보고면 paper 제외."""
from kv_eval.config import rubrics, technologies_config
from kv_eval.orchestrator import dispatch as dispatch_mod
from kv_eval.orchestrator.graph import build_orchestrator_graph
from kv_eval.orchestrator.plan import plan_tasks
from kv_eval.orchestrator.state import SubTask


def _state(only, briefs=None):
    cfg = technologies_config()
    return {"run_id": "t", "technologies": cfg["technologies"], "rubrics": rubrics(), "only_criteria": only,
            "tech_briefs": briefs or {}, "task_plan": [], "retry_targets": [], "quality_verdict": None}


def test_fanout_is_planner_decision_not_cell_count():
    out = plan_tasks(_state(["TRL-1", "DOM-4"]))                      # RAG 항목 2개 × 기술 2 = 셀 4
    plan = out["task_plan"]
    assert len(plan) == 8 and all(t.status == "pending" and t.round == 0 for t in plan)   # 셀당 템플릿 2쌍
    assert {t.task_id for t in plan} >= {"turboquant:TRL-1:r0", "turboquant:TRL-1:r0-1", "cxl_pnm:DOM-4:r0-1"}
    assert all(t.queries and "{tech}" in t.queries["pro"] and "{tech}" in t.queries["con"] for t in plan)
    assert out["fanout_log"][0] ["cells"] == 4 and out["fanout_log"][0]["count"] == 8
    assert len(plan_tasks(_state(["MKT-1"]))["task_plan"]) == 2      # 웹 전용 항목은 1쌍 → 셀 수와 같음


def test_guards_fill_missing_cells_and_cap_templates(monkeypatch):
    from kv_eval.orchestrator import plan as plan_mod

    def sloppy(state, mode, cells, crits):          # TRL-1만 계획하고 DOM-4는 빠뜨림, 템플릿 5쌍, {tech} 없음
        return plan_mod.Plan(items=[plan_mod.CriterionPlan(
            criterion_id="TRL-1", channels=[], reason="x",
            query_templates=[plan_mod.QueryPair(pro=f"real hw {i}", con="simulation only", focus=str(i))
                             for i in range(5)])])

    monkeypatch.setattr(plan_mod, "_fake_plan", sloppy)
    plan = plan_tasks(_state(["TRL-1", "DOM-4"]))["task_plan"]
    assert {(t.criterion_id, t.slot) for t in plan} == {("TRL-1", 0), ("TRL-1", 1), ("TRL-1", 2), ("DOM-4", 0)}
    assert all(t.queries["pro"].startswith("{tech}") for t in plan)   # 대칭 가드
    assert all(t.channels == ["web"] for t in plan if t.criterion_id == "TRL-1")  # 채널 비면 web


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
    assert "근거 공백" in plan[0].reason and "criticism" in plan[0].queries["con"]   # 사유를 쿼리에 반영


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
    assert calls["n"] == 4                                            # 서브태스크 2개 × (1회 + 재시도 1회)
    assert final["node_status"]["cxl_pnm:DOM-4:r0"] == "excluded" and final["node_status"]["cxl_pnm:DOM-4:r0-1"] == "excluded"
    assert final["node_status"].get("cxl_pnm:DOM-4:r1") == "done"     # 균형 점검이 공백을 잡아 다음 라운드에 재수집
    assert {e["task_id"] for e in final["errors"]} == {"cxl_pnm:DOM-4:r0", "cxl_pnm:DOM-4:r0-1"}
    assert sum(1 for t in final["task_plan"] if t.status == "done") >= 6  # 나머지 브랜치는 정상 누적
    ids = [e.evidence_id for e in final["evidence_pool"]]
    assert len(ids) == len(set(ids))                                  # slot 태그로 evidence_id 충돌 없음
    assert any(e.tech_id == "turboquant" and e.criterion_id == "DOM-4" for e in final["evidence_pool"])
    assert any(g.tech_id == "cxl_pnm" and g.criterion_id == "DOM-4" for g in final["info_gaps"])
    assert final["status"] == "PARTIAL"
