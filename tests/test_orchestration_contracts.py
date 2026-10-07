"""Orchestrator-Workers 최소 계약과 병렬 reducer 검증."""
from langgraph.graph import END, START, StateGraph

from kv_eval.graph.state import MainState
from kv_eval.graph.task_schema import (
    CollectTask,
    ReportQualityResult,
    RetryTarget,
    ScoreTask,
    WorkerResult,
    WorkPlan,
)


def _collect_task() -> CollectTask:
    return CollectTask(
        tech={"tech_id": "turboquant", "name": "TurboQuant"},
        criterion={"id": "TRL-1", "agent": "trl"},
    )


def _score_task() -> ScoreTask:
    return ScoreTask(
        tech={"tech_id": "turboquant", "name": "TurboQuant"},
        agent_type="trl",
        criterion_ids=["TRL-1"],
        evidence=[],
    )


def test_work_plan_round_trip_keeps_existing_task_contracts():
    collect = _collect_task()
    score = _score_task()
    plan = WorkPlan(collect_tasks=[collect], score_tasks=[score], round=1, reason="retry")

    restored = WorkPlan.model_validate_json(plan.model_dump_json())

    assert restored == plan
    assert restored.collect_tasks[0] == collect
    assert restored.score_tasks[0] == score
    assert RetryTarget(
        tech_id="turboquant", criterion_id="TRL-1", kind="research", reason="근거 부족"
    ).kind == "research"


def test_worker_and_report_quality_results_round_trip():
    worker = WorkerResult(
        task_id="collect:1:turboquant:TRL-1",
        kind="collect",
        status="partial",
        produced_count=1,
        retryable=True,
        error="원문 한 건 확인 실패",
    )
    quality = ReportQualityResult(
        passed=False,
        page_count=11,
        issues=["10페이지 초과"],
        retry_kind="compress",
    )

    assert WorkerResult.model_validate(worker.model_dump()) == worker
    assert ReportQualityResult.model_validate_json(quality.model_dump_json()) == quality


def test_worker_results_are_merged_across_parallel_branches():
    def collect_worker(state: MainState) -> dict:
        return {
            "worker_results": [
                WorkerResult(
                    task_id="collect:0:turboquant:TRL-1",
                    kind="collect",
                    status="completed",
                    produced_count=2,
                )
            ]
        }

    def score_worker(state: MainState) -> dict:
        return {
            "worker_results": [
                WorkerResult(
                    task_id="score:0:turboquant:trl:TRL-1",
                    kind="score",
                    status="completed",
                    produced_count=1,
                )
            ]
        }

    graph = StateGraph(MainState)
    graph.add_node("collect", collect_worker)
    graph.add_node("score", score_worker)
    graph.add_node("join", lambda state: {})
    graph.add_edge(START, "collect")
    graph.add_edge(START, "score")
    graph.add_edge(["collect", "score"], "join")
    graph.add_edge("join", END)

    final = graph.compile().invoke({"worker_results": []})

    assert {r.task_id for r in final["worker_results"]} == {
        "collect:0:turboquant:TRL-1",
        "score:0:turboquant:trl:TRL-1",
    }
