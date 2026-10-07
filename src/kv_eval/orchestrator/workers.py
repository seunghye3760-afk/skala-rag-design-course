"""run_subtask 노드 (Worker): SubTask 1개 = 셀 1개 × 채널 1개 근거 수집.

Worker는 자기 입력(WorkerInput)만 보고 일하며 결과는 reducer 필드에만 쓴다.
다른 Worker와 통신하지 않는다.

Fallback 정책 (재시도 → 제외 후 계속):
  - 예외(검색 API·원문 fetch·LLM 추출 오류 등)가 나면 같은 SubTask를 worker_max_attempts까지 재시도
  - 그래도 실패하면 status="excluded"로 기록하고 근거 없이 반환 → 나머지 Worker는 그대로 진행
  - 근거 0건은 실패가 아니다 (collect가 쿼리 재작성 1회까지 이미 시도). 셀 전체가 비면
    balance_check가 '근거 공백'으로 잡아 Orchestrator 재계획으로 넘긴다.
"""
from __future__ import annotations

from datetime import date

from .. import obs, progress
from ..config import runtime
from ..evidence.collect import collect_evidence
from ..graph.task_schema import CollectTask
from .schema import WorkerInput, WorkerOutcome


def run_subtask(inp: WorkerInput) -> dict:
    st = inp.subtask
    max_attempts = runtime()["orchestrator"]["worker_max_attempts"]
    task = CollectTask(tech=inp.tech, criterion=inp.criterion, round=st.round, rewrite_hint=st.hint)
    queries = {"pro": [q.pro for q in st.queries], "con": [q.con for q in st.queries]}

    error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            out = collect_evidence(task, channel=st.channel, queries=queries)
        except Exception as e:  # noqa: BLE001 — 어떤 수집 오류든 같은 재시도·제외 규칙 적용
            error = e
            progress.step("run_subtask", f"{st.subtask_id} 실패 ({attempt}/{max_attempts}): "
                          f"{type(e).__name__}: {e}")
            continue
        outcome = WorkerOutcome(subtask_id=st.subtask_id, round=st.round, status="done",
                                attempts=attempt, evidence_count=len(out["evidence_pool"]))
        return {**out, "worker_outcomes": [outcome]}

    msg = f"{type(error).__name__}: {error}"[:300]
    progress.step("run_subtask", f"{st.subtask_id} 재시도 소진 → 제외하고 계속")
    obs.log_decision({"run_id": inp.run_id, "trace_id": inp.trace_id}, "run_subtask", "excluded", msg,
                     subtask_id=st.subtask_id, attempts=max_attempts)
    log = {"tech_id": st.tech_id, "criterion_id": st.criterion_id, "channel": st.channel,
           "round": st.round, "queries": queries, "results": 0, "rewritten": False,
           "searched_at": date.today().isoformat(), "error": msg}
    return {"search_log": [log], "last_error": f"{st.subtask_id}: {msg}", "worker_outcomes": [WorkerOutcome(
        subtask_id=st.subtask_id, round=st.round, status="excluded", attempts=max_attempts,
        evidence_count=0, error=msg)]}
