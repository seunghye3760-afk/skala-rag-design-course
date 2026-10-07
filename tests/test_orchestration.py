"""동적 계획, Supervisor 라우팅, Worker 실패 격리, 보고서 품질 루프."""
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from kv_eval.agents.report_quality import evaluate_report
from kv_eval.graph.checkpoint import checkpoint_serializer
from kv_eval.graph.main import build_graph
from kv_eval.graph.supervisor import route_supervisor, supervise
from kv_eval.graph.task_schema import CollectTask, RetryTarget
from kv_eval.graph.workers import collect_worker


def test_dynamic_plan_and_supervisor_complete_end_to_end():
    final = build_graph().invoke({"run_id": "orchestration", "only_criteria": ["TRL-1", "DOM-4"]})

    collect_plan, score_plan = final["plan_history"]
    assert collect_plan.phase == "collect" and len(collect_plan.items) == 4
    assert score_plan.phase == "score" and len(score_plan.items) == 4
    assert len(final["worker_results"]) == 8
    assert [d.action for d in final["decision_log"]] == [
        "apply_rules", "synthesize", "evaluate", "finalize"
    ]
    assert final["quality_evaluation"].passed
    assert final["final_status"] == "completed"
    run_dir = Path(final["report_path"]).parent
    assert (run_dir / "task_plans.json").exists()
    assert (run_dir / "supervisor_decisions.json").exists()
    assert (run_dir / "quality_evaluation.json").exists()


def test_supervisor_routes_research_and_report_revision():
    target = RetryTarget(tech_id="turboquant", criterion_id="DOM-4", kind="research",
                         reason="비판 근거 부족")
    reviewed = supervise({"phase": "reviewed", "retry_targets": [target], "supervisor_step": 0})
    assert route_supervisor(reviewed) == "plan_collect"

    failed_quality = evaluate_report({"report_draft": "근거 없는 짧은 초안", "evidence_pool": [],
                                      "technologies": []})["quality_evaluation"]
    evaluated = supervise({"phase": "evaluated", "quality_evaluation": failed_quality,
                            "report_revision": 0, "supervisor_step": 1})
    assert route_supervisor(evaluated) == "revise"
    assert evaluated["report_revision"] == 1


def test_worker_failure_becomes_state_instead_of_raising(monkeypatch):
    def boom(_task):
        raise RuntimeError("temporary search failure")

    monkeypatch.setattr("kv_eval.graph.workers.collect_evidence", boom)
    task = CollectTask(
        tech={"tech_id": "turboquant"},
        criterion={"id": "DOM-4"},
        round=1,
    )
    update = collect_worker(task)
    result = update["worker_results"][0]
    assert result.status == "failed"
    assert result.error_type == "RuntimeError"


def test_graph_accepts_checkpointer_for_resume_contract():
    graph = build_graph(InMemorySaver(serde=checkpoint_serializer()))
    final = graph.invoke(
        {"run_id": "checkpoint", "only_criteria": ["DOM-4"]},
        {"configurable": {"thread_id": "checkpoint"}},
    )
    snapshot = graph.get_state({"configurable": {"thread_id": "checkpoint"}})
    assert final["final_status"] == "completed"
    assert snapshot.values["run_id"] == "checkpoint"
