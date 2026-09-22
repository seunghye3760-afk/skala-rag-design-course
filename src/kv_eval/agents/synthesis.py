"""종합 에이전트 (LLM). 담당 4.
상충 후보를 근거·조건과 대조해 '관점 간 상충 / 조건 차이 / 근거 부족' 중 하나로 서술한다.
점수 합산·순위·우열 판정 금지 (prompts/synthesis.md).

final_assessment 반환 형식 (state.py의 MainState.final_assessment: dict, 계약에 세부
스키마가 없어 여기서 정한다 — reporting/report.py만 이 형식을 읽는다):
    {
      "<tech_id>": {"요약": str, "한계": [str], "시사점": [str]},
      ...
      "_cross": {"근거_대조": str, "트레이드오프": str, "상호보완_가능성": str},
      "_scenario": {"시나리오1": str, "시나리오2": str, "이해관계자_충돌": str, "도입_요인_장벽": str},
      "_overall": str,
    }
"_"로 시작하는 키는 tech_id(turboquant/cxl_pnm)와 겹치지 않게 하려는 구분자일 뿐이다.
"""
from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field

from .. import progress
from ..config import ROOT, runtime
from ..graph.state import MainState
from ..graph.task_schema import ConflictCandidate, TechId

PROMPT_FILE = "prompts/synthesis.md"
Interpretation = Literal["관점 간 상충", "조건 차이", "근거 부족"]
_INTERPRETATIONS = ("관점 간 상충", "조건 차이", "근거 부족")


class _ConflictInterpretation(BaseModel):
    comparison_id: str = Field(description="상충 후보 ID (예: P1)")
    interpretation: Interpretation
    note: str = Field(description="1~2문장. 두 항목의 근거·조건을 실제로 언급하며 왜 그렇게 분류했는지")


class _TechAssessment(BaseModel):
    tech_id: TechId
    summary: str
    limitations: list[str]
    implications: list[str]


class _CrossComparison(BaseModel):
    관점별_근거_대조: str
    트레이드오프: str
    상호보완_가능성: str


class _ScenarioGuidance(BaseModel):
    시나리오1: str
    시나리오2: str
    이해관계자_충돌: str
    도입_요인_장벽: str


class _Synthesis(BaseModel):
    conflicts: list[_ConflictInterpretation]
    tech_assessment: list[_TechAssessment]
    cross_comparison: _CrossComparison
    scenario_guidance: _ScenarioGuidance
    overall: str


def _fake(state: MainState) -> dict:
    conflicts = [c.model_copy(update={"interpretation": c.interpretation or "(FAKE) 미해석"})
                 for c in state.get("conflicts", [])]
    assessment = {t["tech_id"]: {"요약": "(FAKE) 미구현", "한계": [], "시사점": []}
                 for t in state["technologies"]}
    assessment["_cross"] = {"근거_대조": "(FAKE) 미구현", "트레이드오프": "(FAKE) 미구현",
                            "상호보완_가능성": "(FAKE) 미구현"}
    assessment["_scenario"] = {"시나리오1": "(FAKE) 미구현", "시나리오2": "(FAKE) 미구현",
                               "이해관계자_충돌": "(FAKE) 미구현", "도입_요인_장벽": "(FAKE) 미구현"}
    assessment["_overall"] = "(FAKE) 미구현"
    return {"conflicts": conflicts, "final_assessment": assessment}


def _criterion_line(rub: dict, r) -> str:
    name = next((c["name"] for c in rub["criteria"] if c["id"] == r.criterion_id), r.criterion_id)
    cap = f" (상한 적용: {r.cap_applied})" if r.cap_applied else ""
    return (f"- {r.criterion_id} {name}: {r.score}점 (원점수 {r.raw_score}, 확신도 {r.confidence}){cap}\n"
           f"  rationale: {r.rationale}")


def _tech_block(state: MainState, tech_id: str) -> str:
    names = {t["tech_id"]: t["name"] for t in state["technologies"]}
    rub = state["rubrics"]
    lines = [f"## {names[tech_id]} ({tech_id})"]
    trl = next((tr for tr in state.get("trl_results", []) if tr.tech_id == tech_id), None)
    if trl:
        lines.append(f"TRL {trl.trl_level} (확신도 {trl.trl_confidence}, 기반 부품 성숙도 "
                     f"{trl.component_maturity})\n게이트: {' / '.join(trl.gate_trace)}")
    results = [r for r in state.get("final_results", []) if r.tech_id == tech_id]
    lines += [_criterion_line(rub, r) for r in sorted(results, key=lambda r: r.criterion_id)]
    return "\n".join(lines)


def _conflict_block(state: MainState, names: dict) -> str:
    rub = state["rubrics"]
    pairs = {p["id"]: p for p in rub["comparison_pairs"]["pairs"]}
    lines = []
    for c in state.get("conflicts", []):
        p = pairs.get(c.comparison_id, {})
        lines.append(f"- {c.comparison_id} ({names[c.tech_id]}, 주제: {p.get('theme', '?')}): "
                     f"{p.get('a', '?')} ↔ {p.get('b', '?')}, 점수 차 {c.score_gap}, "
                     f"상태 {c.status}, 근거 {c.evidence_refs}")
    return "\n".join(lines) or "(상충 후보 없음)"


def _build_messages(state: MainState) -> list[dict]:
    system = (ROOT / PROMPT_FILE).read_text(encoding="utf-8")
    names = {t["tech_id"]: t["name"] for t in state["technologies"]}
    user = "\n\n".join([_tech_block(state, t["tech_id"]) for t in state["technologies"]]
                       + ["## 상충 후보", _conflict_block(state, names)])
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _get_llm():
    from langchain_openai import ChatOpenAI  # 무거운 라이브러리는 함수 안에서 import
    rt = runtime()["llm"]
    model = rt.get("model") or "gpt-4.1-mini"
    return ChatOpenAI(model=model, temperature=rt.get("temperature", 0)).with_structured_output(_Synthesis)


def _apply(state: MainState, out: _Synthesis) -> dict:
    by_id = {c.comparison_id: c for c in out.conflicts}
    conflicts: list[ConflictCandidate] = []
    for c in state.get("conflicts", []):
        item = by_id.get(c.comparison_id)
        interp = item.interpretation if item and item.interpretation in _INTERPRETATIONS else "근거 부족"
        note = f" — {item.note}" if item else ""
        conflicts.append(c.model_copy(update={"interpretation": f"{interp}{note}"}))

    assessment: dict = {
        a.tech_id: {"요약": a.summary, "한계": a.limitations, "시사점": a.implications}
        for a in out.tech_assessment
    }
    assessment["_cross"] = out.cross_comparison.model_dump()
    assessment["_scenario"] = out.scenario_guidance.model_dump()
    assessment["_overall"] = out.overall
    return {"conflicts": conflicts, "final_assessment": assessment}


def synthesize(state: MainState) -> dict:
    if os.getenv("KV_FAKE") == "1":
        return _fake(state)
    progress.step("synthesize", "관점 간 상충 해석·종합 서술 LLM 호출")
    messages = _build_messages(state)
    out: _Synthesis = _get_llm().invoke(messages)
    progress.step("synthesize", "완료")
    return _apply(state, out)
