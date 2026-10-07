"""task_plan → Send fan-out, worker 래퍼(fallback 정책), join(defer).

Fallback 정책 (configs/runtime.yaml `fallback`): worker 예외 → worker_retry(1)회 재시도 → 그래도 실패하면
  - collect_worker: 그 셀을 제외(status="excluded")하고 errors·excluded_gaps(info_gap)에 기록, 나머지 브랜치는 계속
  - score_worker  : 해당 항목을 NA 결과로 만들고 errors에 기록
worker는 Send로 받은 Job만 읽고 자기 키(evidence_pool/search_log/criterion_results/task_plan/node_status/errors)만
반환한다. worker끼리 직접 통신하지 않는다.

기존 worker 함수(evidence.collect.collect_evidence, agents.score_task.score_task)는 시그니처를 그대로 두고
여기서 감싼다. 채널은 계약 파일을 고치지 않고 CollectTask.criterion(dict)의 evidence_sources를
조정해 전달한다 — paper 채널이 없으면 RAG 출처를 빼서 collect_evidence의 uses_rag()가 False가 된다.
"""
from __future__ import annotations

import time
import traceback
from collections import defaultdict

from langgraph.types import Send

from .. import progress
from ..agents.score_task import score_task
from ..config import agent_of, criteria_list, criterion, runtime
from ..evidence.collect import collect_evidence
from ..graph.reducers import evidence_for
from ..graph.task_schema import CollectTask, CriterionResult, RetryTarget, ScoreTask, cell_key
from .observability import decision
from .state import CollectJob, OrchestratorState, ScoreJob, SubTask


def _tech(state: OrchestratorState, tech_id: str) -> dict:
    return next(t for t in state["technologies"] if t["tech_id"] == tech_id)


def _worker_retry() -> int:
    return int(runtime().get("fallback", {}).get("worker_retry", 1))


def _err(task_id: str, node: str, e: Exception) -> dict:
    return {"task_id": task_id, "node": node, "type": type(e).__name__, "message": str(e)[:300],
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}


# ---------- 수집 ----------

def _criterion_for_task(crit: dict, t: SubTask) -> dict:
    """계약 파일을 고치지 않고 계획을 전달한다: 채널은 evidence_sources로(paper 없으면 RAG 출처 제거 →
    collect_evidence의 uses_rag()가 False), 쿼리는 criterion["queries"]로 (LLM 플래너 템플릿)."""
    out = dict(crit)
    if "paper" not in t.channels:
        out["evidence_sources"] = [s for s in crit["evidence_sources"] if not s.startswith("RAG")]
    if t.queries:
        out["queries"] = dict(t.queries)
    return out


def route_after_plan(state: OrchestratorState):
    """pending 서브태스크가 있으면 그것만 Send, 없으면 채점으로 건너뛴다 (Dynamic fan-out)."""
    pending = [t for t in state.get("task_plan") or [] if t.status == "pending"]
    if not pending:
        progress.step("dispatch", "pending 서브태스크 없음 → score_dispatch")
        return "score_dispatch"
    rub = state["rubrics"]
    sends = []
    for t in pending:
        hint = t.reason.split(" / 이전: ", 1)[1] if " / 이전: " in t.reason else None
        sends.append(Send("collect_worker", CollectJob(
            task_id=t.task_id, channels=t.channels, slot=t.slot, queries=t.queries,
            run_id=state.get("run_id", "run"),
            task=CollectTask(tech=_tech(state, t.tech_id),
                             criterion=_criterion_for_task(criterion(rub, t.criterion_id), t),
                             round=t.round, rewrite_hint=hint))))
    progress.step("dispatch", f"collect_worker {len(sends)}개 fan-out (round {pending[0].round})")
    return sends


def _subtask(job: CollectJob, status: str, err: str | None = None) -> SubTask:
    return SubTask(task_id=job.task_id, tech_id=job.task.tech["tech_id"], criterion_id=job.task.criterion["id"],
                   channels=job.channels, round=job.task.round, slot=job.slot, queries=job.queries,
                   status=status, last_error=err)


_SLOT = "abcdefghij"


def _tag_slot(out: dict, job: CollectJob) -> dict:
    """같은 셀·같은 round의 서브태스크가 여러 개면 evidence_id가 겹친다(collect_evidence는 round·순번만 씀).
    slot>0 이면 id 끝에 글자를 붙여 구분한다 (숫자를 붙이면 balance.py의 수치 대조가 숫자로 오인)."""
    if not job.slot:
        return out
    tag = _SLOT[job.slot % len(_SLOT)]
    return {**out, "evidence_pool": [e.model_copy(update={"evidence_id": f"{e.evidence_id}{tag}"})
                                     for e in out.get("evidence_pool", [])]}


def collect_worker(job: CollectJob) -> dict:
    """collect_evidence 래퍼. 예외 → 1회 재시도 → 제외(excluded) + info_gap."""
    last: Exception | None = None
    for attempt in range(1 + _worker_retry()):
        try:
            out = _tag_slot(collect_evidence(job.task), job)
            return {**out, "task_plan": [_subtask(job, "done")], "node_status": {job.task_id: "done"},
                    "step_count": 1}
        except Exception as e:  # noqa: BLE001 — 어떤 예외든 같은 정책(재시도 1회 → 제외)
            last = e
            progress.step("collect_worker", f"{job.task_id} 실패 {attempt + 1}회: {type(e).__name__}: {e}")
    tid, cid = job.task.tech["tech_id"], job.task.criterion["id"]
    msg = f"{type(last).__name__}: {last}"
    decision(job.run_id, "collect_worker", "exclude", f"{job.task_id} 재시도 소진 → 제외 ({msg})", task_id=job.task_id)
    return {"task_plan": [_subtask(job, "excluded", msg)], "node_status": {job.task_id: "excluded"},
            "errors": [_err(job.task_id, "collect_worker", last)], "last_error": f"{job.task_id}: {msg}",
            "excluded_gaps": [RetryTarget(tech_id=tid, criterion_id=cid, kind="research",
                                          reason=f"수집 worker 실패로 제외: {msg}")],
            "step_count": 1}


def evidence_join(state: OrchestratorState) -> dict:
    """defer=True로 등록: 모든 collect_worker 브랜치가 끝난 뒤 1회."""
    plan = state.get("task_plan") or []
    rnd = state.get("collect_round", 0)
    done = sum(1 for t in plan if t.round == rnd and t.status == "done")
    excl = sum(1 for t in plan if t.round == rnd and t.status == "excluded")
    progress.step("evidence_join", f"round {rnd}: done {done} / excluded {excl}, 근거 누적 "
                                   f"{len(state.get('evidence_pool', []))}건")
    return {"step_count": 1}


# ---------- 채점 ----------

def score_dispatch(state: OrchestratorState) -> dict:
    """이번에 채점할 셀을 상태 조건으로 정한다 (처음: 전 셀 / 이후: 새 근거가 들어온 셀 + 재채점 대상 + 커버리지 미달 셀)."""
    rub = state["rubrics"]
    all_cells = [cell_key(t["tech_id"], c["id"]) for t in state["technologies"]
                 for c in criteria_list(rub, state.get("only_criteria"))]
    results = state.get("criterion_results") or []
    if not results:
        cells, why = all_cells, "최초 채점: 전 셀"
    else:
        rnd = state.get("collect_round", 0)
        fresh = {cell_key(t.tech_id, t.criterion_id) for t in state.get("task_plan") or []
                 if t.round == rnd and t.status == "done"}
        retry = {cell_key(x.tech_id, x.criterion_id) for x in state.get("retry_targets") or []}
        v = state.get("quality_verdict")
        cov = set(v.failed_cells) if (v and not v.passed and "coverage" in v.failed_items) else set()
        cells = [c for c in all_cells if c in (fresh | retry | cov)]
        why = f"새 근거 {len(fresh)} / 재채점 {len(retry)} / 커버리지 {len(cov)}"
    score_round = max((r.round for r in results), default=-1) + 1
    decision(state.get("run_id", "run"), "score_dispatch", "score_cells", f"{why} → {len(cells)}셀 (round {score_round})",
             round=score_round, count=len(cells))
    return {"score_cells": cells, "score_round": score_round, "step_count": 1}


def fan_out_score(state: OrchestratorState) -> list[Send]:
    rub = state["rubrics"]
    rnd = state.get("score_round", 0)
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for key in state.get("score_cells") or []:
        tid, cid = key.split(":", 1)
        groups[(agent_of(rub, cid), tid)].append(cid)
    pool = state.get("evidence_pool", [])
    sends = []
    for (agent, tid), cids in groups.items():
        ev = [e for cid in cids for e in evidence_for(pool, tid, cid)]
        sends.append(Send("score_worker", ScoreJob(task_id=f"score:{agent}:{tid}:r{rnd}",
                                                   run_id=state.get("run_id", "run"), task=ScoreTask(
            tech=_tech(state, tid), agent_type=agent, criterion_ids=sorted(cids), evidence=ev,
            tech_brief=(state.get("tech_briefs") or {}).get(tid, {}), round=rnd))))
    progress.step("score_dispatch", f"score_worker {len(sends)}개 fan-out (round {rnd})")
    return sends


def _na_results(task: ScoreTask, e: Exception) -> list[CriterionResult]:
    return [CriterionResult(tech_id=task.tech["tech_id"], criterion_id=cid, agent_type=task.agent_type,
                            round=task.round, score="NA", confidence="low",
                            rationale=f"[fallback] 채점 worker 실패({type(e).__name__})로 NA 처리")
            for cid in task.criterion_ids]


def score_worker(job: ScoreJob) -> dict:
    """score_task 래퍼. 예외 → 1회 재시도 → 항목 NA + errors."""
    last: Exception | None = None
    for attempt in range(1 + _worker_retry()):
        try:
            out = score_task(job.task)
            return {**out, "node_status": {job.task_id: "done"}, "step_count": 1}
        except Exception as e:  # noqa: BLE001
            last = e
            progress.step("score_worker", f"{job.task_id} 실패 {attempt + 1}회:\n{traceback.format_exc(limit=2)}")
    decision(job.run_id, "score_worker", "na_fallback", f"{job.task_id} 재시도 소진 → NA", task_id=job.task_id)
    return {"criterion_results": _na_results(job.task, last), "node_status": {job.task_id: "failed"},
            "errors": [_err(job.task_id, "score_worker", last)], "last_error": f"{job.task_id}: {last}",
            "step_count": 1}


def score_join(state: OrchestratorState) -> dict:
    progress.step("score_join", f"채점 결과 {len(state.get('criterion_results', []))}건 누적")
    return {"step_count": 1}
