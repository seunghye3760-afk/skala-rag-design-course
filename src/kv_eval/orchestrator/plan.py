"""plan_tasks: LLM 플래너가 셀(기술 × 항목)별 수집 서브태스크를 계획해 task_plan에 쓴다 (Orchestrator의 계획 단계).

누가 무엇을 정하나
  LLM (prompts/plan.md, 구조화 출력) — 항목마다
    · 어떤 채널을 쓸지 (paper = 논문 RAG / web = Tavily / open_source = 저장소·릴리스 노트), 기술별로 다르게 가능
    · 검색 쿼리 템플릿을 몇 쌍 만들지 (1~max_subtasks_per_cell). 쌍 하나가 서브태스크 하나 = worker 하나
      → fan-out 수 = Σ(셀 × 템플릿 수)가 모델 결정이 된다
    · 재시도·재계획 라운드에서는 균형 점검 사유(비판 근거 없음, 단일 출처 …)·품질 피드백을 보고 쿼리를 다시 짠다
  코드 (가드, 계획을 "바꾸지" 않고 빠진 것만 메운다)
    · 커버리지: 범위 안의 모든 셀에 서브태스크 ≥ 1 (LLM이 빠뜨리면 루브릭 기본 템플릿으로 채움)
    · 대칭(설계서 C-5): 템플릿은 {tech} 자리를 갖고 두 기술에 같은 것을 적용
    · 비용 상한: 셀당 템플릿 ≤ plan.max_subtasks_per_cell, 채널 비면 web
  Fallback (필수 항목 ④): LLM 호출 실패 → plan.llm_retry 회 재시도 → 그래도 실패하면 루브릭 기본 템플릿 전 채널로
    "비상 기본 계획"을 쓰고 decision_log에 plan_fallback_default 로 남긴다. 정상 경로에서는 규칙이 계획을 만들지 않는다.

모드 (상태 조건으로만 결정, 스텝 고정 없음)
  initial : task_plan 비어 있음 → 전 셀
  retry   : balance_check의 kind="research" retry_targets → 그 셀만
  replan  : 품질 미달 + failed_cells → 그 셀만 (quality.max_rounds 상한)
"""
from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field

from .. import progress
from ..config import ROOT, criteria_list, runtime, uses_rag
from .observability import decision
from .state import Channel, OrchestratorState, SubTask

PROMPT_FILE = "prompts/plan.md"
Mode = Literal["initial", "retry", "replan"]


# ---------- LLM 출력 스키마 ----------

class QueryPair(BaseModel):
    pro: str = Field(description="긍정 근거 검색 템플릿. {tech} 자리 포함, 영어")
    con: str = Field(description="비판 근거 검색 템플릿. {tech} 자리 포함, 영어")
    focus: str = Field(description="이 쌍이 노리는 근거 유형 한 줄 (예: 독립 벤치마크, 공식 문서, 이슈 트래커)")


class TechChannels(BaseModel):
    tech_id: str
    channels: list[Channel]


class CriterionPlan(BaseModel):
    criterion_id: str
    channels: list[TechChannels] = Field(description="기술별 채널. 논문에 근거가 없을 기술은 paper를 빼라")
    query_templates: list[QueryPair] = Field(description="1개 이상. 쌍 하나 = 수집 worker 하나")
    reason: str = Field(description="왜 이 채널·이 쿼리 수인지 한 문장")


class Plan(BaseModel):
    items: list[CriterionPlan]


# ---------- 입력 조립 ----------

def _brief_text(brief: dict) -> str:
    lines = []
    for k, v in (brief or {}).items():
        if isinstance(v, dict):
            lines.append(f"- {k} [{v.get('status', '?')}]: {str(v.get('value', ''))[:300]}")
        else:
            lines.append(f"- {k}: {str(v)[:300]}")
    return "\n".join(lines) or "(brief 없음)"


def _planner_input(state: OrchestratorState, mode: Mode, cells: list[tuple[str, str, str | None]],
                   crits: dict[str, dict]) -> str:
    techs = {t["tech_id"]: t for t in state["technologies"]}
    briefs = state.get("tech_briefs") or {}
    rub = state["rubrics"]
    parts = [f"[모드] {mode}", f"[검색 규칙] {rub.get('query_rules', {})}", ""]
    for tid, t in techs.items():
        parts += [f"[기술] {tid}: {t['name']} ({t['group']}, {t['category']}) — 검색명 {t['query_names']}",
                  f"[논문 brief: {tid}]", _brief_text(briefs.get(tid, {})), ""]
    by_cid: dict[str, list[tuple[str, str | None]]] = {}
    for tid, cid, hint in cells:
        by_cid.setdefault(cid, []).append((tid, hint))
    parts.append("[계획할 항목]")
    for cid, pairs in by_cid.items():
        c = crits[cid]
        parts.append(f"- {cid} {c['name']} ({c['agent']}): {c['question']}")
        parts.append(f"  출처 힌트: {c['evidence_sources']} / 루브릭 기본 템플릿: {c['queries']}")
        parts.append(f"  대상 기술: {[t for t, _ in pairs]}")
        for tid, hint in pairs:
            if hint:
                parts.append(f"  이전 라운드 사유({tid}): {hint}")
    v = state.get("quality_verdict")
    if mode == "replan" and v is not None and v.feedback:
        parts += ["", "[품질 평가 피드백]", v.feedback]
    return "\n".join(parts)


# ---------- 플래너 ----------

def _llm_plan(state: OrchestratorState, mode: Mode, cells, crits) -> Plan:
    from ..llm import chat_model

    system = (ROOT / PROMPT_FILE).read_text(encoding="utf-8")
    user = _planner_input(state, mode, cells, crits)
    return chat_model().with_structured_output(Plan).invoke(
        [{"role": "system", "content": system}, {"role": "user", "content": user}])


def _fake_plan(state: OrchestratorState, mode: Mode, cells, crits) -> Plan:
    """KV_FAKE=1: LLM 없이 '그럴듯한' 계획. 테스트가 기대하는 성질 — brief 미보고면 paper 제외,
    RAG 항목은 템플릿 2쌍(fan-out이 셀 수와 달라짐), 재시도면 사유를 focus에 반영."""
    briefs = state.get("tech_briefs") or {}
    by_cid: dict[str, list[tuple[str, str | None]]] = {}
    for tid, cid, hint in cells:
        by_cid.setdefault(cid, []).append((tid, hint))
    items = []
    for cid, pairs in by_cid.items():
        c = crits[cid]
        chans, notes = [], []
        for tid, _ in pairs:
            ch: list[Channel] = []
            b = briefs.get(tid, {})
            reported = not (isinstance(b.get("실험_조건"), dict) and b["실험_조건"].get("status") == "미보고")
            if uses_rag(c) and reported:
                ch.append("paper")
            elif uses_rag(c):
                notes.append(f"{tid} brief 미보고 → paper 제외")
            if cid in ("TRL-2", "TRL-3", "STK-2"):
                ch.append("open_source")
            ch.append("web")
            chans.append(TechChannels(tech_id=tid, channels=ch))
        q = c["queries"]
        templates = [QueryPair(pro=q["pro"], con=q["con"], focus="루브릭 기본")]
        if uses_rag(c):
            templates.append(QueryPair(pro="{tech} independent benchmark reproduction",
                                       con="{tech} limitations reported by third party", focus="독립 검증"))
        hint = next((h for _, h in pairs if h), None)
        if hint:
            templates = [QueryPair(pro=q["pro"], con=f"{{tech}} criticism {hint[:30]}", focus=f"재시도: {hint[:40]}")]
        items.append(CriterionPlan(criterion_id=cid, channels=chans, query_templates=templates,
                                   reason=f"(FAKE) {cid}: 채널 {[x.channels for x in chans]}"
                                          + (f"; {'; '.join(notes)}" if notes else "")))
    return Plan(items=items)


def _default_plan(cells, crits) -> Plan:
    """비상 기본 계획 (LLM 실패 시). 루브릭 템플릿 1쌍, 채널은 루브릭 출처대로."""
    by_cid: dict[str, list[str]] = {}
    for tid, cid, _ in cells:
        by_cid.setdefault(cid, []).append(tid)
    items = []
    for cid, tids in by_cid.items():
        c = crits[cid]
        ch: list[Channel] = (["paper"] if uses_rag(c) else []) + ["web"]
        items.append(CriterionPlan(criterion_id=cid, channels=[TechChannels(tech_id=t, channels=ch) for t in tids],
                                   query_templates=[QueryPair(**c["queries"], focus="루브릭 기본(비상)")],
                                   reason="LLM 플래너 실패 → 루브릭 기본 템플릿"))
    return Plan(items=items)


# ---------- 가드 (코드) ----------

def _guard_template(tpl: str) -> str:
    tpl = tpl.strip()
    if "{tech}" not in tpl:
        tpl = "{tech} " + tpl                       # 대칭: 두 기술에 같은 템플릿
    try:
        tpl.format(tech="x", category="y")
    except (KeyError, IndexError, ValueError):
        tpl = tpl.replace("{", "").replace("}", "") # 치환 불가 중괄호 제거
        tpl = "{tech} " + tpl
    return tpl


def _apply_guards(plan: Plan, cells, crits, run_id: str) -> tuple[dict[str, CriterionPlan], list[str]]:
    cap = int(runtime().get("plan", {}).get("max_subtasks_per_cell", 3))
    by_cid = {p.criterion_id: p for p in plan.items if p.criterion_id in crits}
    notes = []
    wanted: dict[str, list[str]] = {}
    for tid, cid, _ in cells:
        wanted.setdefault(cid, []).append(tid)
    for cid, tids in wanted.items():
        p = by_cid.get(cid)
        if p is None or not p.query_templates:                       # 커버리지: 빠진 셀은 기본으로 메움
            c = crits[cid]
            by_cid[cid] = p = CriterionPlan(criterion_id=cid, channels=[], query_templates=[
                QueryPair(**c["queries"], focus="루브릭 기본(플래너 누락 보완)")], reason="플래너 누락 → 기본 보완")
            notes.append(f"{cid}: 플래너 누락 보완")
        if len(p.query_templates) > cap:                              # 비용 상한
            notes.append(f"{cid}: 템플릿 {len(p.query_templates)} → {cap}")
            p.query_templates = p.query_templates[:cap]
        for q in p.query_templates:
            q.pro, q.con = _guard_template(q.pro), _guard_template(q.con)
        have = {x.tech_id for x in p.channels}
        for tid in tids:
            if tid not in have:
                p.channels.append(TechChannels(tech_id=tid, channels=["web"]))
        for x in p.channels:
            if not x.channels:
                x.channels = ["web"]
    if notes:
        decision(run_id, "plan_tasks", "plan_guard", "; ".join(notes))
    return by_cid, notes


# ---------- 노드 ----------

def _next_round(plan: list[SubTask]) -> int:
    return max((t.round for t in plan), default=-1) + 1


def plan_tasks(state: OrchestratorState) -> dict:
    run_id = state.get("run_id", "run")
    rub = state["rubrics"]
    plan = state.get("task_plan") or []
    techs = {t["tech_id"] for t in state["technologies"]}
    crits = {c["id"]: c for c in criteria_list(rub, state.get("only_criteria"))}

    research = [x for x in state.get("retry_targets") or [] if x.kind == "research"]
    verdict = state.get("quality_verdict")
    if not plan:
        mode: Mode = "initial"
        rnd = 0
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
                cells.append((tid, cid, f"품질 미달({', '.join(verdict.failed_items)})"))
    else:
        mode, rnd, cells = "initial", _next_round(plan), []

    source = "llm"
    if not cells:
        planned = Plan(items=[])
    elif os.getenv("KV_FAKE") == "1":
        planned, source = _fake_plan(state, mode, cells, crits), "fake"
    else:
        planned = None
        for attempt in range(1 + int(runtime().get("plan", {}).get("llm_retry", 1))):
            try:
                planned = _llm_plan(state, mode, cells, crits)
                break
            except Exception as e:  # noqa: BLE001 — 재시도 후 비상 기본 계획 (fallback 정책)
                progress.step("plan_tasks", f"LLM 플래너 실패 {attempt + 1}회: {type(e).__name__}: {e}")
                last = e
        if planned is None:
            planned, source = _default_plan(cells, crits), "default"
            decision(run_id, "plan_tasks", "plan_fallback_default",
                     f"LLM 플래너 재시도 소진 ({type(last).__name__}) → 루브릭 기본 템플릿·채널로 진행")

    by_cid, _ = _apply_guards(planned, cells, crits, run_id)
    new: list[SubTask] = []
    for tid, cid, hint in cells:
        p = by_cid[cid]
        ch = next(x.channels for x in p.channels if x.tech_id == tid)
        for slot, q in enumerate(p.query_templates):
            tid_ = f"{tid}:{cid}:r{rnd}" + (f"-{slot}" if slot else "")
            new.append(SubTask(task_id=tid_, tech_id=tid, criterion_id=cid, channels=ch, round=rnd, slot=slot,
                               queries={"pro": q.pro, "con": q.con}, status="pending",
                               reason=f"[{mode}] {q.focus} — {p.reason}" + (f" / 이전: {hint}" if hint else "")))

    mix: dict[str, int] = {}
    for t in new:
        mix["+".join(t.channels)] = mix.get("+".join(t.channels), 0) + 1
    decision(run_id, "plan_tasks", f"plan_{mode}",
             f"round {rnd}: 셀 {len(cells)}개 → 서브태스크 {len(new)}개 (플래너 {source}, 채널 구성 {mix})",
             round=rnd, cells=len(cells), count=len(new), planner=source, channels=mix,
             tasks=[t.task_id for t in new])
    progress.step("plan_tasks", f"{mode} — pending {len(new)}개 (round {rnd}, 플래너 {source})")
    return {"task_plan": new, "collect_round": rnd, "step_count": 1,
            "node_status": {t.task_id: "pending" for t in new},
            "fanout_log": [{"phase": "collect", "mode": mode, "round": rnd, "cells": len(cells), "count": len(new),
                            "planner": source, "channels": mix}]}
