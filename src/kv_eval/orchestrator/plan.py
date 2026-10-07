"""plan_tasks: 셀(기술 × 항목)별 서브태스크를 구조화해 task_plan에 쓴다 (Orchestrator의 계획 단계).

동적 fan-out의 근거 두 가지 (체크리스트 F절 (c)):
  (a) 채널 선택 — 루브릭 evidence_sources(규칙) + tech_brief(규칙 + LLM 보조)로 셀마다 paper/web/open_source를 달리 정한다.
  (b) 셀 제외 — 재시도·재계획 라운드에서는 문제가 난 셀만 pending으로 만든다.

모드 (상태 조건으로만 결정, 스텝 고정 없음):
  initial : task_plan이 비어 있음 → 전 셀(only_criteria 범위) 계획
  retry   : balance_check가 kind="research" retry_targets를 남김 → 그 셀만
  replan  : 품질 평가가 미달이고 failed_cells가 있음 → 그 셀만 (Replan은 품질 미달 때만, quality.max_rounds 상한)

LLM은 "논문에 이 항목 근거가 없을 것 같다"는 판단(채널에서 paper 제거)만 보조하고, 셀 목록·종료는 코드가 정한다.
LLM 실패 시 규칙 계획 그대로 진행한다 (fallback).
"""
from __future__ import annotations

import os

from pydantic import BaseModel, Field

from .. import progress
from ..config import ROOT, criteria_list, uses_rag
from .observability import decision
from .state import Channel, OrchestratorState, SubTask

PROMPT_FILE = "prompts/plan.md"

# 루브릭 항목 → tech_brief 양식 항목. 그 항목이 논문에서 "미보고"면 paper 채널을 뺀다 (규칙 보조).
BRIEF_FIELD_FOR: dict[str, str] = {
    "TRL-1": "실험_조건", "TRL-2": "처리_구조", "TRL-3": "처리_구조", "TRL-5": "적용_한계",
    "DOM-1": "평가_결과", "DOM-2": "평가_결과", "DOM-3": "평가_결과", "DOM-4": "평가_결과", "DOM-5": "적용_한계",
}
OPEN_SOURCE_CRITERIA = {"TRL-2", "TRL-3", "STK-2"}   # 저장소·이슈 트래커·릴리스 노트가 1차 출처인 항목


class _PlanItem(BaseModel):
    criterion_id: str
    paper_has_evidence: bool = Field(description="tech_brief로 볼 때 논문에 이 항목의 근거가 있을 가능성")
    reason: str


class _PlanAssist(BaseModel):
    items: list[_PlanItem]


def _cell_id(tech_id: str, cid: str) -> str:
    return f"{tech_id}:{cid}"


def _brief_status(brief: dict, field: str) -> str | None:
    v = (brief or {}).get(field)
    if isinstance(v, dict):
        return v.get("status")
    return None


def rule_channels(crit: dict, brief: dict) -> tuple[list[Channel], str]:
    """규칙만으로 정하는 채널. (채널, 사유)"""
    ch: list[Channel] = []
    why = []
    if uses_rag(crit):
        field = BRIEF_FIELD_FOR.get(crit["id"])
        if field and _brief_status(brief, field) == "미보고":
            why.append(f"논문 brief '{field}' 미보고 → paper 제외")
        else:
            ch.append("paper")
            why.append("루브릭 evidence_sources에 RAG 포함")
    if crit["id"] in OPEN_SOURCE_CRITERIA:
        ch.append("open_source")
        why.append("저장소·이슈 트래커가 1차 출처")
    ch.append("web")                       # 모든 셀은 웹 검색 (설계서 C-5: 두 기술 같은 규칙)
    return ch, "; ".join(why)


def _llm_assist(tech: dict, brief: dict, cells: list[tuple[dict, list[Channel]]]) -> dict[str, _PlanItem]:
    """paper 채널이 남은 항목만 LLM에게 묻는다. 실패하면 빈 dict (규칙 계획 유지)."""
    targets = [c for c, ch in cells if "paper" in ch]
    if not targets or os.getenv("KV_FAKE") == "1":
        return {}
    from ..llm import chat_model

    system = (ROOT / PROMPT_FILE).read_text(encoding="utf-8")
    brief_txt = "\n".join(f"- {k}: {v.get('value') if isinstance(v, dict) else v}" for k, v in (brief or {}).items())
    items = "\n".join(f"- {c['id']} {c['name']}: {c['question']} / 출처: {c['evidence_sources']}" for c in targets)
    user = f"[기술] {tech['name']} ({tech['category']})\n[논문 brief]\n{brief_txt}\n\n[항목]\n{items}"
    out = chat_model().with_structured_output(_PlanAssist).invoke(
        [{"role": "system", "content": system}, {"role": "user", "content": user}])
    return {i.criterion_id: i for i in out.items}


def _next_round(plan: list[SubTask]) -> int:
    return (max((t.round for t in plan), default=-1) + 1)


def plan_tasks(state: OrchestratorState) -> dict:
    run_id = state.get("run_id", "run")
    rub = state["rubrics"]
    plan = state.get("task_plan") or []
    briefs = state.get("tech_briefs") or {}
    techs = {t["tech_id"]: t for t in state["technologies"]}
    crits = {c["id"]: c for c in criteria_list(rub, state.get("only_criteria"))}

    research = [x for x in state.get("retry_targets") or [] if x.kind == "research"]
    verdict = state.get("quality_verdict")
    if not plan:
        mode, rnd = "initial", 0
        cells = [(tid, cid, None) for tid in techs for cid in crits]
    elif research:
        mode, rnd = "retry", _next_round(plan)
        cells = [(x.tech_id, x.criterion_id, x.hint or x.reason) for x in research if x.criterion_id in crits]
    elif verdict is not None and not verdict.passed and verdict.failed_cells:
        mode, rnd = "replan", _next_round(plan)
        cells = []
        for key in verdict.failed_cells:
            tid, cid = key.split(":", 1)
            if tid in techs and cid in crits:
                cells.append((tid, cid, f"품질 미달({', '.join(verdict.failed_items)}) 재수집"))
    else:
        mode, rnd, cells = "noop", _next_round(plan), []

    new: list[SubTask] = []
    per_tech_assist: dict[str, dict[str, _PlanItem]] = {}
    for tid, cid, hint in cells:
        crit = crits[cid]
        ch, why = rule_channels(crit, briefs.get(tid, {}))
        if "paper" in ch and mode == "initial":
            if tid not in per_tech_assist:
                try:
                    per_tech_assist[tid] = _llm_assist(
                        techs[tid], briefs.get(tid, {}),
                        [(crits[c], rule_channels(crits[c], briefs.get(tid, {}))[0]) for c in crits])
                except Exception as e:  # noqa: BLE001 — LLM 보조 실패 → 규칙 계획만으로 fallback
                    per_tech_assist[tid] = {}
                    decision(run_id, "plan_tasks", "llm_assist_failed",
                             f"{tid}: {type(e).__name__}: {e} → 규칙 계획 유지")
            item = per_tech_assist[tid].get(cid)
            if item is not None and not item.paper_has_evidence:
                ch = [c for c in ch if c != "paper"]
                why += f"; LLM 보조: 논문 근거 없음({item.reason}) → paper 제외"
        new.append(SubTask(task_id=f"{_cell_id(tid, cid)}:r{rnd}", tech_id=tid, criterion_id=cid,
                           channels=ch, round=rnd, status="pending",
                           reason=(f"[{mode}] " + (hint or why))))

    channel_mix = {}
    for t in new:
        channel_mix["+".join(t.channels)] = channel_mix.get("+".join(t.channels), 0) + 1
    decision(run_id, "plan_tasks", f"plan_{mode}",
             f"round {rnd}: 서브태스크 {len(new)}개 (채널 구성 {channel_mix})",
             round=rnd, count=len(new), channels=channel_mix,
             tasks=[t.task_id for t in new])
    progress.step("plan_tasks", f"{mode} — pending {len(new)}개 (round {rnd})")
    return {"task_plan": new, "collect_round": rnd, "step_count": 1,
            "node_status": {t.task_id: "pending" for t in new},
            "fanout_log": [{"phase": "collect", "mode": mode, "round": rnd, "count": len(new),
                            "channels": channel_mix}]}
