"""코드 기반 WorkPlan 생성과 Send fan-out 검증."""
from kv_eval.config import criteria_list, rubrics, technologies_config
from kv_eval.graph.dispatch import fan_out_collect, fan_out_score
from kv_eval.graph.planner import plan_collect_work, plan_score_work
from kv_eval.graph.task_schema import Evidence, Locator, RetryTarget


def _full_state() -> dict:
    return {
        "technologies": technologies_config()["technologies"],
        "rubrics": rubrics(),
        "evidence_pool": [],
        "tech_briefs": {},
        "retry_round": 0,
    }


def test_full_plans_are_derived_from_config():
    state = _full_state()

    collect_plan = plan_collect_work(state)["work_plan"]
    score_plan = plan_score_work(state)["work_plan"]

    selected = criteria_list(state["rubrics"])
    agents = {criterion["agent"] for criterion in selected}
    assert len(collect_plan.collect_tasks) == len(state["technologies"]) * len(selected)
    assert len(score_plan.score_tasks) == len(state["technologies"]) * len(agents)
    assert [(t.tech["tech_id"], t.criterion["id"]) for t in collect_plan.collect_tasks] == [
        (tech["tech_id"], criterion["id"])
        for tech in state["technologies"]
        for criterion in selected
    ]


def test_only_criteria_limits_collect_and_score_plans():
    state = {**_full_state(), "only_criteria": ["TRL-1", "MKT-2"]}

    collect_plan = plan_collect_work(state)["work_plan"]
    score_plan = plan_score_work(state)["work_plan"]

    assert {t.criterion["id"] for t in collect_plan.collect_tasks} == {"TRL-1", "MKT-2"}
    assert len(collect_plan.collect_tasks) == len(state["technologies"]) * 2
    assert len(score_plan.score_tasks) == len(state["technologies"]) * 2
    assert all(t.criterion_ids in (["TRL-1"], ["MKT-2"]) for t in score_plan.score_tasks)


def test_research_retry_plans_only_research_targets():
    state = {
        **_full_state(),
        "retry_round": 1,
        "retry_targets": [
            RetryTarget(
                tech_id="turboquant",
                criterion_id="MKT-2",
                kind="research",
                reason="비판 근거 없음",
                hint="비판 근거 부족",
            ),
            RetryTarget(
                tech_id="cxl_pnm",
                criterion_id="DOM-4",
                kind="rescore",
                reason="채점 형식 오류",
            ),
        ],
    }

    plan = plan_collect_work(state)["work_plan"]

    assert plan.round == 1 and plan.reason == "research_retry"
    assert len(plan.collect_tasks) == 1
    assert plan.collect_tasks[0].tech["tech_id"] == "turboquant"
    assert plan.collect_tasks[0].criterion["id"] == "MKT-2"
    assert plan.collect_tasks[0].rewrite_hint == "비판 근거 부족"


def test_score_retry_groups_only_targeted_criteria():
    state = {
        **_full_state(),
        "retry_round": 1,
        "retry_targets": [
            RetryTarget(
                tech_id="cxl_pnm",
                criterion_id="MKT-1",
                kind="rescore",
                reason="형식 오류",
            ),
            RetryTarget(
                tech_id="cxl_pnm",
                criterion_id="MKT-2",
                kind="research",
                reason="근거 부족",
            ),
        ],
    }

    plan = plan_score_work(state)["work_plan"]

    assert plan.round == 1 and plan.reason == "score_retry"
    assert len(plan.score_tasks) == 1
    assert plan.score_tasks[0].tech["tech_id"] == "cxl_pnm"
    assert plan.score_tasks[0].agent_type == "market"
    assert plan.score_tasks[0].criterion_ids == ["MKT-1", "MKT-2"]


def test_score_retry_deduplicates_targets_and_cross_round_evidence():
    evidence = []
    for round_ in range(3):
        for stance in ("pro", "con"):
            evidence.append(Evidence(
                evidence_id=f"turboquant-MKT-1-r{round_}-{stance}",
                tech_id="turboquant",
                criterion_id="MKT-1",
                claim=f"동일한 {stance} 주장",
                source_title=f"{stance} source",
                source_url=f"https://example.com/{stance}",
                publisher="Example",
                accessed_at="2026-10-07",
                source_type="공식 문서",
                evidence_grade="B",
                stance=stance,
                measurement_type="실측",
                excerpt="같은 원문",
                locator=Locator(url=f"https://example.com/{stance}"),
                round=round_,
            ))
    duplicate_targets = [
        RetryTarget(tech_id="turboquant", criterion_id="MKT-1", kind=kind, reason="retry")
        for kind in ("research", "rescore")
    ]
    state = {
        **_full_state(),
        "retry_round": 2,
        "retry_targets": duplicate_targets,
        "evidence_pool": evidence,
    }

    task = plan_score_work(state)["work_plan"].score_tasks[0]

    assert task.criterion_ids == ["MKT-1"]
    assert len(task.evidence) == 2
    assert {item.stance for item in task.evidence} == {"pro", "con"}


def test_fan_out_uses_tasks_already_stored_in_plan():
    state = _full_state()
    collect_plan = plan_collect_work({**state, "only_criteria": ["TRL-1"]})["work_plan"]
    collect_sends = fan_out_collect({"work_plan": collect_plan})

    score_plan = plan_score_work({**state, "only_criteria": ["TRL-1"]})["work_plan"]
    score_sends = fan_out_score({"work_plan": score_plan})

    assert [send.node for send in collect_sends] == ["collect_evidence"] * len(collect_plan.collect_tasks)
    assert [send.arg for send in collect_sends] == collect_plan.collect_tasks
    assert [send.node for send in score_sends] == ["score_task"] * len(score_plan.score_tasks)
    assert [send.arg for send in score_sends] == score_plan.score_tasks


def test_plans_are_deterministic_for_the_same_state():
    state = {**_full_state(), "only_criteria": ["TRL-1", "MKT-2", "DOM-4"]}

    collect_first = plan_collect_work(state)["work_plan"].model_dump()
    collect_second = plan_collect_work(state)["work_plan"].model_dump()
    score_first = plan_score_work(state)["work_plan"].model_dump()
    score_second = plan_score_work(state)["work_plan"].model_dump()

    assert collect_first == collect_second
    assert score_first == score_second
