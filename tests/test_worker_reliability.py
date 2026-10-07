"""Worker 실패 집계와 bounded fallback 회귀 테스트."""
from kv_eval.config import rubrics, runtime, technologies_config
from kv_eval.graph.reducers import latest_results
from kv_eval.graph.task_schema import CriterionResult, WorkerResult
from kv_eval.rules.balance import balance_check


def _score(score, round_):
    return CriterionResult(
        tech_id="turboquant",
        criterion_id="DOM-3",
        agent_type="domain",
        round=round_,
        score=score,
        rationale="결과",
        confidence="low",
    )


def _failed_collect(round_):
    return WorkerResult(
        task_id=f"collect:{round_}:turboquant:DOM-3",
        kind="collect",
        status="failed",
        retryable=True,
        error="RuntimeError: 검색 장애",
    )


def _score_worker(status, round_):
    return WorkerResult(
        task_id=f"score:{round_}:turboquant:domain:DOM-3",
        kind="score",
        status=status,
        produced_count=1 if status == "completed" else 0,
        retryable=status == "failed",
        error="ValueError: 출력 오류" if status == "failed" else None,
    )


def _balance_state(round_):
    tech = next(t for t in technologies_config()["technologies"]
                if t["tech_id"] == "turboquant")
    return {
        "technologies": [tech],
        "rubrics": rubrics(),
        "only_criteria": ["DOM-3"],
        "evidence_pool": [],
        "criterion_results": [],
        "worker_results": [_failed_collect(round_)],
        "retry_round": round_,
    }


def test_later_failed_na_does_not_replace_previous_success():
    latest = latest_results(
        [_score(3, 0), _score("NA", 1)], [_score_worker("failed", 1)]
    )

    assert latest["turboquant:DOM-3"].score == 3
    assert latest["turboquant:DOM-3"].round == 0


def test_successful_na_is_distinct_from_worker_failure():
    latest = latest_results(
        [_score(3, 0), _score("NA", 1)], [_score_worker("completed", 1)]
    )

    assert latest["turboquant:DOM-3"].score == "NA"
    assert latest["turboquant:DOM-3"].round == 1


def test_retryable_worker_failure_uses_existing_retry_targets():
    out = balance_check(_balance_state(0))

    assert out["retry_round"] == 1
    assert [(target.kind, target.criterion_id) for target in out["retry_targets"]] == [
        ("research", "DOM-3"),
    ]
    assert out["retry_targets"][0].reason.startswith("Worker 실패")


def test_worker_failure_becomes_info_gap_after_retry_limit():
    max_rounds = runtime()["retry"]["max_rounds"]
    out = balance_check(_balance_state(max_rounds))

    assert out["retry_targets"] == []
    assert [(gap.kind, gap.criterion_id) for gap in out["info_gaps"]] == [
        ("research", "DOM-3"),
    ]
    assert out["info_gaps"][0].reason.startswith("Worker 실패")
