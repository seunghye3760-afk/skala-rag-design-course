"""Worker 경계. 예외를 State의 실패 결과로 바꿔 나머지 fan-out을 계속 실행한다."""
from __future__ import annotations

from ..agents.score_task import score_task
from ..evidence.collect import collect_evidence
from .task_schema import CollectTask, ScoreTask, WorkerResult


def collect_worker(task: CollectTask) -> dict:
    task_id = f"collect:r{task.round}:{task.tech['tech_id']}:{task.criterion['id']}"
    try:
        update = collect_evidence(task)
        count = len(update.get("evidence_pool", []))
        return {**update, "worker_results": [WorkerResult(
            task_id=task_id, kind="collect", status="completed", attempt=task.round,
            output_count=count)]}
    except Exception as exc:  # noqa: BLE001 - 실패를 그래프 상태로 승격
        return {"worker_results": [WorkerResult(
            task_id=task_id, kind="collect", status="failed", attempt=task.round,
            error_type=type(exc).__name__, error_message=str(exc)[:500])],
            "search_log": [{"task_id": task_id, "round": task.round, "results": 0,
                            "error": f"{type(exc).__name__}: {str(exc)[:500]}"}]}


def score_worker(task: ScoreTask) -> dict:
    task_id = f"score:r{task.round}:{task.agent_type}:{task.tech['tech_id']}"
    try:
        update = score_task(task)
        count = len(update.get("criterion_results", []))
        return {**update, "worker_results": [WorkerResult(
            task_id=task_id, kind="score", status="completed", attempt=task.round,
            output_count=count)]}
    except Exception as exc:  # noqa: BLE001 - Supervisor가 재시도/제외 판단
        return {"worker_results": [WorkerResult(
            task_id=task_id, kind="score", status="failed", attempt=task.round,
            error_type=type(exc).__name__, error_message=str(exc)[:500])]}
