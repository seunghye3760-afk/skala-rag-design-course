"""현재 State만 보고 다음 노드를 선택하는 상위 Supervisor."""
from __future__ import annotations

from .. import progress
from ..config import runtime
from .state import MainState
from .task_schema import SupervisorDecision


def supervise(state: MainState) -> dict:
    step = state.get("supervisor_step", 0) + 1
    phase = state.get("phase", "")
    max_steps = runtime().get("orchestration", {}).get("supervisor_max_steps", 12)

    if step >= max_steps:
        action, reason = "finalize", f"Supervisor 단계 상한 {max_steps} 도달"
    elif phase == "reviewed":
        targets = state.get("retry_targets") or []
        if any(x.kind == "research" for x in targets):
            action, reason = "plan_collect", "근거 부족 항목이 있어 대상 항목만 재조사"
        elif targets:
            action, reason = "plan_score", "근거는 충분하나 채점 형식 오류가 있어 재채점"
        else:
            action, reason = "apply_rules", "근거 충분성 점검 통과 또는 재시도 한도 소진"
    elif phase == "rules_applied":
        action, reason = "synthesize", "확정 평가 결과가 준비되어 보고서 초안 작성 가능"
    elif phase == "drafted":
        action, reason = "evaluate", "새 보고서 초안의 품질평가가 필요"
    elif phase == "evaluated":
        quality = state.get("quality_evaluation")
        revision = state.get("report_revision", 0)
        limit = runtime().get("orchestration", {}).get("report_revision_max", 2)
        if quality and quality.passed:
            action, reason = "finalize", "보고서 품질평가 네 항목 통과"
        elif revision < limit:
            action, reason = "revise", f"품질 미달로 보고서 수정 {revision + 1}/{limit}"
        else:
            action, reason = "finalize", "보고서 수정 한도 소진; 품질 공백을 기록하고 종료"
    else:
        action, reason = "finalize", f"알 수 없는 단계({phase})에 대한 안전 종료"

    decision = SupervisorDecision(step=step, action=action, reason=reason)
    progress.step("supervisor", f"{action}: {reason}")
    update = {"supervisor_step": step, "supervisor_decision": decision,
              "decision_log": [decision]}
    if action == "revise":
        update["report_revision"] = state.get("report_revision", 0) + 1
    return update


def route_supervisor(state: MainState) -> str:
    return state["supervisor_decision"].action
