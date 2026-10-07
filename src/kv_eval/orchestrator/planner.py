"""plan_tasks 노드 (Orchestrator) + fan_out_workers (Dynamic Fan-out).

흐름:
  계획할 셀 결정 ── round 0: 모든 (기술 × 항목)
                 └─ replan : balance_check가 재검색(research)으로 고른 셀만 (+ 미달 사유)
  → LLM이 셀별 채널·추가 쿼리·우선순위·사유를 정함 (_PlanDraft)
  → 코드 가드(guard)가 계획을 검증·보정 → Plan(SubTask 목록)을 State에 저장
  → fan_out_workers가 SubTask마다 Send 1건 → Worker 수는 계획이 정한다

LLM 호출이 실패하거나 KV_FAKE=1이면 '허용 채널 전부 + 기본 쿼리' 기본 계획으로 대체한다 (fallback).
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from typing import Literal

from langgraph.types import Send
from pydantic import BaseModel, Field

from .. import obs, progress
from ..config import ROOT, criteria_list, criterion, runtime
from ..graph.guards import control_step
from ..graph.state import MainState
from .schema import Channel, Plan, QueryPair, SubTask, WorkerInput

_OPEN_SOURCE_HINT = re.compile(r"오픈소스|이슈 트래커|저장소|풀 리퀘스트|릴리스 노트|토론 게시판")


# ---------- LLM 출력 형식 ----------

class _CellDecision(BaseModel):
    tech_id: str
    criterion_id: str
    channels: list[Channel]
    extra_queries: list[QueryPair] = Field(default_factory=list)
    priority: Literal["high", "normal"] = "normal"
    reason: str


class _PlanDraft(BaseModel):
    cells: list[_CellDecision]
    rationale: str


# ---------- 셀·채널 ----------

def allowed_channels(crit: dict) -> list[Channel]:
    """루브릭 evidence_sources가 허용하는 채널. 계획은 이 범위를 벗어날 수 없다."""
    srcs = crit["evidence_sources"]
    out: list[Channel] = []
    if any(s.startswith("RAG") for s in srcs):
        out.append("rag")
    if any(s.startswith("웹") for s in srcs):
        out.append("web")
        if any(_OPEN_SOURCE_HINT.search(s) for s in srcs):
            out.append("open_source")
    return out


def _target_cells(state: MainState) -> list[dict]:
    """계획 대상 셀. replan이면 재검색 대상과 그 사유·힌트를 같이 넘긴다."""
    rnd = state.get("retry_round", 0)
    if rnd == 0:
        return [{"tech_id": t["tech_id"], "criterion_id": c["id"], "issue": None, "hint": None}
                for t in state["technologies"]
                for c in criteria_list(state["rubrics"], state.get("only_criteria"))]
    seen, out = set(), []
    for x in state.get("retry_targets", []):
        if x.kind == "research" and (x.tech_id, x.criterion_id) not in seen:
            seen.add((x.tech_id, x.criterion_id))
            out.append({"tech_id": x.tech_id, "criterion_id": x.criterion_id,
                        "issue": x.reason, "hint": x.hint})
    return out


def _base_queries(tech: dict, crit: dict) -> list[QueryPair]:
    """루브릭 쿼리 템플릿을 기술 검색명마다 치환 (설계서 C-5: 두 기술에 같은 템플릿)."""
    q = crit["queries"]
    return [QueryPair(pro=q["pro"].format(tech=n, category=tech["category"]),
                      con=q["con"].format(tech=n, category=tech["category"]))
            for n in tech["query_names"]]


# ---------- 계획 생성 ----------

def _default_draft(cells: list[dict], rub: dict, why: str) -> _PlanDraft:
    """fallback 계획: 허용 채널 전부 + 기본 쿼리. replan이면 우선순위 high."""
    return _PlanDraft(rationale=why, cells=[
        _CellDecision(tech_id=c["tech_id"], criterion_id=c["criterion_id"],
                      channels=allowed_channels(criterion(rub, c["criterion_id"])),
                      priority="high" if c["issue"] else "normal",
                      reason="기본 계획: 허용 채널 전부" + (f" (미달 사유: {c['issue']})" if c["issue"] else ""))
        for c in cells])


def _prompt_sections() -> dict[str, str]:
    text = (ROOT / "prompts" / "orchestrator" / "plan.md").read_text(encoding="utf-8")
    parts = re.split(r"^## (\w+)\s*$", text, flags=re.MULTILINE)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


def _brief_summary(state: MainState) -> str:
    lines = []
    for t in state["technologies"]:
        brief = state.get("tech_briefs", {}).get(t["tech_id"], {})
        fields = [f"{k}={v.get('status', '?')}" for k, v in brief.items() if isinstance(v, dict)]
        lines.append(f"- {t['name']} ({t['tech_id']}): " + (", ".join(fields) or "브리프 없음"))
    return "\n".join(lines)


def _llm_draft(state: MainState, cells: list[dict], mode: str) -> _PlanDraft:
    from ..llm import chat_model

    rub = state["rubrics"]
    names = {t["tech_id"]: t["name"] for t in state["technologies"]}
    cell_rows = []
    for c in cells:
        crit = criterion(rub, c["criterion_id"])
        row = {"tech_id": c["tech_id"], "tech": names[c["tech_id"]], "criterion_id": crit["id"],
               "name": crit["name"], "question": crit["question"],
               "evidence_sources": crit["evidence_sources"], "allowed_channels": allowed_channels(crit)}
        if c["issue"]:
            row.update(issue=c["issue"], hint=c["hint"])
        cell_rows.append(row)
    p = _prompt_sections()
    user = p["user"].format(mode=mode, round=state.get("retry_round", 0),
                            domain=state["domain"]["name"], briefs=_brief_summary(state),
                            cells=json.dumps(cell_rows, ensure_ascii=False, indent=1))
    return chat_model().with_structured_output(_PlanDraft).invoke(
        [("system", p["system"]), ("user", user)])


# ---------- 코드 가드 ----------

def guard(draft: _PlanDraft, cells: list[dict], state: MainState) -> tuple[list[SubTask], list[str]]:
    """LLM 계획을 검증·보정한다. 이 함수를 통과한 계획만 실행된다.

    1. 커버리지: 대상 셀마다 SubTask 최소 1개 (누락 셀은 기본 채널로 보충)
    2. 허용 채널: 루브릭 evidence_sources 밖의 채널은 제거
    3. 범위: 대상이 아닌 셀에 대한 결정은 버림
    4. 상한: 라운드당 max_subtasks — 셀별 첫 채널은 보장하고, 나머지는 priority 순으로 채움
    """
    rub, rnd = state["rubrics"], state.get("retry_round", 0)
    techs = {t["tech_id"]: t for t in state["technologies"]}
    cap = runtime()["orchestrator"]["max_subtasks_per_round"]
    notes: list[str] = []
    targets = {(c["tech_id"], c["criterion_id"]): c for c in cells}
    decided: dict[tuple[str, str], _CellDecision] = {}

    for d in draft.cells:
        key = (d.tech_id, d.criterion_id)
        if key not in targets:
            notes.append(f"대상 아닌 셀 결정 제거: {key}")
            continue
        if key in decided:
            notes.append(f"중복 결정 제거: {key}")
            continue
        allowed = allowed_channels(criterion(rub, d.criterion_id))
        chans = [ch for ch in dict.fromkeys(d.channels) if ch in allowed]
        if dropped := [ch for ch in d.channels if ch not in allowed]:
            notes.append(f"허용 밖 채널 제거 {key}: {dropped}")
        if not chans:
            chans = allowed
            notes.append(f"채널 없음 → 허용 채널 전부로 보충: {key}")
        decided[key] = d.model_copy(update={"channels": chans, "extra_queries": d.extra_queries[:2]})

    for key, c in targets.items():
        if key not in decided:
            notes.append(f"계획 누락 셀 보충: {key}")
            decided[key] = _default_draft([c], rub, "").cells[0]

    # SubTask 생성: 셀별 첫 채널(필수) / 나머지(선택)로 나눠 상한 적용
    required, optional = [], []
    for (tid, cid), d in decided.items():
        crit = criterion(rub, cid)
        queries = (_base_queries(techs[tid], crit) + d.extra_queries)[:4]
        for i, ch in enumerate(d.channels):
            st = SubTask(subtask_id=f"r{rnd}:{tid}:{cid}:{ch}", round=rnd, tech_id=tid, criterion_id=cid,
                         agent_type=crit["agent"], channel=ch, queries=queries, priority=d.priority,
                         hint=targets[(tid, cid)]["hint"], reason=d.reason)
            (required if i == 0 else optional).append(st)
    optional.sort(key=lambda s: s.priority != "high")          # high 먼저 (stable sort)
    room = max(cap - len(required), 0)
    if len(optional) > room:
        notes.append(f"상한 {cap} 초과 → 선택 채널 {len(optional) - room}개 제외")
    subtasks = required + optional[:room]

    if rnd == 0:   # 관점 커버리지: 대상 항목의 관점이 계획에 모두 있는지
        want = {criterion(rub, c["criterion_id"])["agent"] for c in cells}
        if missing := want - {s.agent_type for s in subtasks}:
            notes.append(f"관점 누락: {sorted(missing)}")
    return subtasks, notes


# ---------- 노드 ----------

def plan_tasks(state: MainState) -> dict:
    rnd = state.get("retry_round", 0)
    mode = "initial" if rnd == 0 else "replan"
    cells = _target_cells(state)
    if os.getenv("KV_FAKE") == "1":
        draft = _default_draft(cells, state["rubrics"], "KV_FAKE: 기본 계획")
    else:
        try:
            draft = _llm_draft(state, cells, mode)
        except Exception as e:  # noqa: BLE001 — 계획 실패로 전체가 멈추지 않도록 기본 계획으로 대체
            progress.step("plan_tasks", f"LLM 계획 실패 → 기본 계획 사용: {type(e).__name__}: {e}")
            draft = _default_draft(cells, state["rubrics"], f"LLM 계획 실패 fallback ({type(e).__name__})")

    subtasks, notes = guard(draft, cells, state)
    plan = Plan(plan_id=f"{state.get('run_id', 'run')}:r{rnd}", round=rnd, mode=mode,
                subtasks=subtasks, rationale=draft.rationale, guard_notes=notes)
    by_ch = Counter(s.channel for s in subtasks)
    progress.step("plan_tasks", f"{mode} round {rnd}: 셀 {len(cells)}개 → Worker {len(subtasks)}개 "
                  f"({', '.join(f'{k} {v}' for k, v in sorted(by_ch.items()))})"
                  + (f" · 가드 보정 {len(notes)}건" if notes else ""))
    path = obs.dump_plan(state, plan)
    obs.log_decision(state, "plan_tasks", f"{mode}: workers={len(subtasks)}", draft.rationale,
                     round=rnd, cells=len(cells), by_channel=dict(by_ch), guard_notes=notes, plan_file=str(path))
    return {"plan": plan, **control_step(state, "plan_tasks", f"done r{rnd} workers={len(subtasks)}")}


def fan_out_workers(state: MainState) -> list[Send] | str:
    """계획의 SubTask마다 Send 1건. 계획이 비면 수집을 건너뛰고 join으로 간다."""
    plan: Plan = state["plan"]
    if not plan.subtasks:
        return "evidence_join"
    techs = {t["tech_id"]: t for t in state["technologies"]}
    rub = state["rubrics"]
    return [Send("run_subtask", WorkerInput(subtask=s, tech=techs[s.tech_id],
                                            criterion=criterion(rub, s.criterion_id),
                                            run_id=state.get("run_id", "run"), trace_id=state.get("trace_id")))
            for s in plan.subtasks]
