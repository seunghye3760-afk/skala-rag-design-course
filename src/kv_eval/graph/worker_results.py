"""Worker 작업 ID와 실패를 기존 RetryTarget으로 바꾸는 작은 helper."""
from __future__ import annotations

from .task_schema import CollectTask, RetryTarget, ScoreTask, WorkerResult


def collect_task_id(task: CollectTask) -> str:
    return f"collect:{task.round}:{task.tech['tech_id']}:{task.criterion['id']}"


def score_task_id(task: ScoreTask) -> str:
    criteria = ",".join(task.criterion_ids)
    return f"score:{task.round}:{task.tech['tech_id']}:{task.agent_type}:{criteria}"


def failed_score_cells(results: list[WorkerResult]) -> set[tuple[int, str, str, str]]:
    """최종 실패한 Score Worker가 만든 fallback 결과의 식별자를 반환한다."""
    latest = {result.task_id: result for result in results}
    cells: set[tuple[int, str, str, str]] = set()
    for result in latest.values():
        parts = result.task_id.split(":", 4)
        if result.kind != "score" or result.status != "failed" or len(parts) != 5:
            continue
        try:
            round_ = int(parts[1])
        except ValueError:
            continue
        cells.update((round_, parts[2], parts[3], criterion_id)
                     for criterion_id in parts[4].split(","))
    return cells


def retry_targets_from_workers(results: list[WorkerResult], round_: int) -> list[RetryTarget]:
    """현재 round의 retry 가능한 실패·부분 성공을 셀 단위 재시도로 바꾼다."""
    latest = {result.task_id: result for result in results}
    targets: list[RetryTarget] = []
    for result in latest.values():
        if result.status == "completed" or not result.retryable:
            continue
        parts = result.task_id.split(":", 4)
        expected_parts = 4 if result.kind == "collect" else 5
        if len(parts) != expected_parts or parts[0] != result.kind:
            continue
        try:
            result_round = int(parts[1])
        except ValueError:
            continue
        if result_round != round_:
            continue

        tech_id = parts[2]
        criterion_ids = [parts[3]] if result.kind == "collect" else parts[4].split(",")
        kind = "research" if result.kind == "collect" else "rescore"
        status = "부분 성공" if result.status == "partial" else "실패"
        reason = f"Worker {status}: {result.error or '결과 생성 실패'}"
        for criterion_id in criterion_ids:
            targets.append(RetryTarget(
                tech_id=tech_id,
                criterion_id=criterion_id,
                kind=kind,
                reason=reason,
                hint=("실패한 검색 경로를 피하고 대체 출처로 재검색"
                      if kind == "research" else None),
            ))
    return targets
