"""quality_eval 노드 + route_after_quality (보고서 생성 후 품질 평가 — 3안 Hybrid).

판정: 축마다 규칙 통과 AND judge 통과여야 축 통과. 4축 모두 통과해야 보고서 통과.
  - judge 실패 = 점수 < judge_pass_score 이고 보고서에서 검증된 위반 문장이 1건 이상

미달 시 경로 (결정과 사유는 EvalVerdict.reason과 quality_r{n}.json에 남는다):
  - 셀로 특정되는 근거 문제(groundedness·bias·coverage) → plan_tasks 재계획 (해당 셀만 재수집·재채점)
  - 서술 문제만(중립성 위반, 근거 없는 수치 등)        → synthesize_report 재작성 (지적 사항 전달)
  - quality_round가 max_rounds에 도달                → give_up: 판정 결과를 남기고 종료 (무한 루프 방지)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from langgraph.graph import END

from .. import progress
from ..config import runtime
from ..graph.state import MainState
from ..graph.task_schema import RetryTarget
from .checks import check_bias, check_coverage, check_groundedness, check_neutrality
from .schema import AXES, Action, AxisVerdict, EvalVerdict, QualityIssue

_HINTS = {"groundedness": "원문으로 확인 가능한 1차 근거 보강",
          "bias": "다른 발행 주체·독립 출처와 비판 근거 보강",
          "coverage": "다른 검색명·근거 유형으로 재검색"}


def _decide(axes: list[AxisVerdict], qround: int, max_rounds: int) -> tuple[Action, str]:
    failed = [a for a in axes if not a.passed]
    if not failed:
        return "pass", "4개 축 모두 통과"
    names = ", ".join(a.axis for a in failed)
    if qround >= max_rounds:
        return "give_up", f"미달({names})이지만 품질 재시도 {max_rounds}회 소진 → 결과를 기록하고 종료"
    cells = [i for a in failed for i in a.issues if i.is_cell and i.axis != "neutrality"]
    if cells:
        return "replan", f"미달({names}) — 근거 문제 셀 {len({(i.tech_id, i.criterion_id) for i in cells})}개 재계획"
    return "resynthesize", f"미달({names}) — 서술 문제만 있어 보고서 재작성"


def _retry_targets(verdict: EvalVerdict) -> list[RetryTarget]:
    seen, out = set(), []
    for i in verdict.failed_issues():
        key = (i.tech_id, i.criterion_id)
        if not i.is_cell or i.axis == "neutrality" or key in seen:
            continue
        seen.add(key)
        out.append(RetryTarget(tech_id=i.tech_id, criterion_id=i.criterion_id, kind="research",
                               reason=f"품질평가({i.axis}): {i.detail}", hint=_HINTS[i.axis]))
    return out


def _feedback(verdict: EvalVerdict) -> list[str]:
    return [f"[{i.axis}] {i.detail}" + (f" — 문제 문장: \"{i.quote}\"" if i.quote else "")
            for i in verdict.failed_issues() if not i.is_cell][:15]


def quality_eval(state: MainState) -> dict:
    cfg = runtime()["quality"]
    qround = state.get("quality_round", 0)
    report = Path(state["report_path"]).read_text(encoding="utf-8")

    rule_issues: dict[str, list[QualityIssue]] = {
        "groundedness": check_groundedness(state, report),
        "neutrality": check_neutrality(state),
        "bias": check_bias(state, cfg),
        "coverage": check_coverage(state, report),
    }

    scores: dict[str, int] = {}
    judge_issues: list[QualityIssue] = []
    judge_note = "judge 미사용"
    if cfg["judge"] and os.getenv("KV_FAKE") != "1":
        from .judge import judge
        try:
            scores, judge_issues, dropped = judge(state)
            judge_note = f"judge 점수 {scores}, 검증된 위반 {len(judge_issues)}건, 인용 불일치로 버린 위반 {dropped}건"
        except Exception as e:      # judge 실패로 그래프가 멈추지 않게 — 규칙 판정만으로 진행
            judge_note = f"judge 실패 → 규칙 판정만 사용 ({type(e).__name__}: {e})"

    axes = []
    for axis in AXES:
        j_iss = [i for i in judge_issues if i.axis == axis]
        score = scores.get(axis)
        judge_failed = score is not None and score < cfg["judge_pass_score"] and bool(j_iss)
        rule_passed = not rule_issues[axis]
        axes.append(AxisVerdict(axis=axis, passed=rule_passed and not judge_failed, rule_passed=rule_passed,
                                judge_score=score, issues=rule_issues[axis] + j_iss))

    action, reason = _decide(axes, qround, cfg["max_rounds"])
    verdict = EvalVerdict(round=qround, passed=action == "pass", action=action, axes=axes,
                          reason=f"{reason} · {judge_note}")

    run_dir = Path(state["report_path"]).parent
    (run_dir / f"quality_r{qround}.json").write_text(
        json.dumps(verdict.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    summary = " / ".join(f"{a.axis} {'O' if a.passed else 'X'}({len(a.issues)})" for a in axes)
    progress.step("quality_eval", f"round {qround}: {summary} → {action}")

    upd: dict = {"eval_result": verdict, "quality_round": qround + 1}
    if action == "replan":
        upd["retry_targets"] = _retry_targets(verdict)
        upd["retry_round"] = state.get("retry_round", 0) + 1
    if action in ("replan", "resynthesize"):
        upd["quality_feedback"] = _feedback(verdict)
    return upd


def route_after_quality(state: MainState) -> str:
    return {"replan": "plan_tasks", "resynthesize": "synthesize_report"}.get(state["eval_result"].action, END)
