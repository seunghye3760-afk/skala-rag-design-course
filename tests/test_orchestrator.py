"""Orchestrator-Workers: 계획 가드, Dynamic Fan-out, 재계획 범위, Worker fallback."""
from kv_eval.config import rubrics, technologies_config
from kv_eval.evidence import collect as collect_mod
from kv_eval.graph.main import build_graph
from kv_eval.graph.task_schema import RetryTarget
from kv_eval.orchestrator import planner
from kv_eval.orchestrator import workers as workers_mod
from kv_eval.orchestrator.planner import _CellDecision, _PlanDraft, allowed_channels, guard
from kv_eval.orchestrator.schema import QueryPair


def _state(**kw):
    cfg = technologies_config()
    return {"run_id": "t", "technologies": cfg["technologies"], "domain": cfg["domain"],
            "rubrics": rubrics(), "retry_round": 0, **kw}


def _crit(cid):
    return next(c for c in rubrics()["criteria"] if c["id"] == cid)


def test_allowed_channels_follow_rubric():
    assert allowed_channels(_crit("MKT-2")) == ["web"]
    assert allowed_channels(_crit("TRL-2")) == ["rag", "web", "open_source"]
    assert allowed_channels(_crit("DOM-4")) == ["rag", "web"]


def test_guard_fixes_llm_plan():
    cells = [{"tech_id": "turboquant", "criterion_id": c, "issue": None, "hint": None}
             for c in ("MKT-2", "DOM-4")]
    draft = _PlanDraft(rationale="x", cells=[
        _CellDecision(tech_id="turboquant", criterion_id="MKT-2", channels=["rag", "web"], reason="r"),
        _CellDecision(tech_id="cxl_pnm", criterion_id="TRL-1", channels=["rag"], reason="대상 아님"),
    ])
    subtasks, notes = guard(draft, cells, _state(only_criteria=["MKT-2", "DOM-4"]))
    ids = {s.subtask_id for s in subtasks}
    assert "r0:turboquant:MKT-2:rag" not in ids                  # 허용 밖 채널 제거
    assert "r0:turboquant:MKT-2:web" in ids
    assert {"r0:turboquant:DOM-4:rag", "r0:turboquant:DOM-4:web"} <= ids   # 누락 셀 보충
    assert not any(s.tech_id == "cxl_pnm" for s in subtasks)     # 대상 아닌 셀 제거
    assert len(notes) == 3


def test_guard_cap_keeps_one_subtask_per_cell(monkeypatch):
    rt = dict(collect_mod.runtime())
    monkeypatch.setattr(planner, "runtime", lambda: {**rt, "orchestrator": {"max_subtasks_per_round": 3}})
    cells = [{"tech_id": "turboquant", "criterion_id": c, "issue": None, "hint": None}
             for c in ("TRL-2", "DOM-4")]
    subtasks, notes = guard(planner._default_draft(cells, rubrics(), ""), cells, _state())
    assert len(subtasks) == 3
    assert {s.criterion_id for s in subtasks} == {"TRL-2", "DOM-4"}
    assert any("상한" in n for n in notes)


def test_extra_queries_capped_and_paired():
    cells = [{"tech_id": "cxl_pnm", "criterion_id": "MKT-1", "issue": None, "hint": None}]
    extra = [QueryPair(pro=f"p{i}", con=f"c{i}") for i in range(5)]
    draft = _PlanDraft(rationale="x", cells=[_CellDecision(
        tech_id="cxl_pnm", criterion_id="MKT-1", channels=["web"], extra_queries=extra, reason="r")])
    (st,), _ = guard(draft, cells, _state())
    assert len(st.queries) == 4 and all(q.pro and q.con for q in st.queries)


def test_dynamic_fan_out_and_replan_only_targets(monkeypatch):
    original = collect_mod.fake_evidence

    def flaky(task):
        ev = original(task)
        if task.round == 0 and task.tech["tech_id"] == "cxl_pnm" and task.criterion["id"] == "DOM-4":
            return [e for e in ev if e.stance == "pro"]          # 비판 근거 없음 → 편향
        return ev

    monkeypatch.setattr(collect_mod, "fake_evidence", flaky)
    final = build_graph().invoke({"run_id": "d", "only_criteria": ["MKT-2", "DOM-4"]})
    r0 = [o for o in final["worker_outcomes"] if o.round == 0]
    r1 = [o for o in final["worker_outcomes"] if o.round == 1]
    assert len(r0) == 6                    # 셀 4개 → Worker 6개 (MKT-2: web / DOM-4: rag+web)
    assert {o.subtask_id for o in r1} == {"r1:cxl_pnm:DOM-4:rag", "r1:cxl_pnm:DOM-4:web"}
    assert final["plan"].mode == "replan" and final["plan"].subtasks[0].hint == "비판 근거 부족"


def test_worker_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}
    real = workers_mod.collect_evidence

    def once_broken(task, channel=None, queries=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("tavily")
        return real(task, channel=channel, queries=queries)

    monkeypatch.setattr(workers_mod, "collect_evidence", once_broken)
    final = build_graph().invoke({"run_id": "w", "only_criteria": ["MKT-2"]})
    attempts = sorted(o.attempts for o in final["worker_outcomes"])
    assert attempts == [1, 2] and all(o.status == "done" for o in final["worker_outcomes"])


def test_worker_excluded_and_run_continues(monkeypatch):
    real = workers_mod.collect_evidence

    def broken_rag(task, channel=None, queries=None):
        if channel == "rag":
            raise RuntimeError("index missing")
        return real(task, channel=channel, queries=queries)

    monkeypatch.setattr(workers_mod, "collect_evidence", broken_rag)
    final = build_graph().invoke({"run_id": "x", "only_criteria": ["DOM-4"]})
    excluded = [o for o in final["worker_outcomes"] if o.status == "excluded"]
    assert excluded and all(o.subtask_id.endswith(":rag") and o.attempts == 2 for o in excluded)
    assert final["report_path"]            # 일부 Worker 제외에도 보고서까지 완료


def test_llm_plan_failure_falls_back(monkeypatch):
    monkeypatch.setenv("KV_FAKE", "0")

    def boom(*a, **k):
        raise ValueError("bad json")

    monkeypatch.setattr(planner, "_llm_draft", boom)
    out = planner.plan_tasks(_state(only_criteria=["TRL-2"]))
    assert "fallback" in out["plan"].rationale
    assert len(out["plan"].subtasks) == 6  # 기술 2 × 허용 채널 3


def test_replan_targets_research_only():
    st = _state(retry_round=1, retry_targets=[
        RetryTarget(tech_id="turboquant", criterion_id="MKT-1", kind="research", reason="단일 출처"),
        RetryTarget(tech_id="turboquant", criterion_id="MKT-2", kind="rescore", reason="형식 오류")])
    assert [c["criterion_id"] for c in planner._target_cells(st)] == ["MKT-1"]
