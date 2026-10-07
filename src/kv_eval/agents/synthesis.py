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
      "_comparability": {"<criterion_id>": {"verdict": "비교 가능|조건 차이|정보 부족", "turboquant": str, "cxl_pnm": str, "note": str}},
    }
"_"로 시작하는 키는 tech_id(turboquant/cxl_pnm)와 겹치지 않게 하려는 구분자일 뿐이다.
"""
from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field

from .. import progress
from ..config import ROOT
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


Comparability = Literal["비교 가능", "조건 차이", "정보 부족"]


class _ConditionComparison(BaseModel):
    """2.4 실험 근거 비교표 한 행. 두 기술의 실험 조건(모델·문맥 길이·하드웨어·정밀도)을 대조한 판정."""
    criterion_id: str
    verdict: Comparability
    turboquant_conditions: str = Field(description="TurboQuant 쪽 대표 조건 요약 1줄 (없으면 '근거 없음')")
    cxl_pnm_conditions: str = Field(description="CXL-PNM 쪽 대표 조건 요약 1줄 (없으면 '근거 없음')")
    note: str = Field(description="판정 이유 1문장. 어떤 조건(모델/문맥 길이/HW/정밀도)이 같거나 다른지 지목")


class _Synthesis(BaseModel):
    condition_comparisons: list[_ConditionComparison]
    conflicts: list[_ConflictInterpretation]
    tech_assessment: list[_TechAssessment]
    cross_comparison: _CrossComparison
    scenario_guidance: _ScenarioGuidance
    overall: str


_GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


def _conditions_of(state: MainState, tech_id: str, cid: str, limit: int = 4) -> list[str]:
    """항목·기술별 실험 조건 문자열: 최고 등급 근거부터, 중복 제거, 최대 limit개."""
    pool = [e for e in state.get("evidence_pool", []) if e.tech_id == tech_id and e.criterion_id == cid
            and (e.conditions or "").strip()]
    out: list[str] = []
    for e in sorted(pool, key=lambda e: _GRADE_ORDER[e.evidence_grade]):
        c = " ".join(e.conditions.split())[:160]
        if c not in out:
            out.append(c)
        if len(out) >= limit:
            break
    return out


def _condition_block(state: MainState) -> str:
    rub = state["rubrics"]
    lines = []
    for c in rub["criteria"]:
        cid = c["id"]
        a, b = _conditions_of(state, "turboquant", cid), _conditions_of(state, "cxl_pnm", cid)
        if not a and not b:
            continue
        lines.append(f"- criterion_id={cid} ({c['name']})\n  TurboQuant: {' | '.join(a) or '(근거 없음)'}\n"
                     f"  CXL-PNM: {' | '.join(b) or '(근거 없음)'}")
    return "\n".join(lines) or "(조건이 기록된 근거 없음)"


def _fake_comparisons(state: MainState) -> dict:
    """KV_FAKE: 기계 규칙(한쪽 없으면 정보 부족, 문자열 같으면 비교 가능, 아니면 조건 차이)."""
    out = {}
    for c in state["rubrics"]["criteria"]:
        cid = c["id"]
        a, b = _conditions_of(state, "turboquant", cid), _conditions_of(state, "cxl_pnm", cid)
        if not a and not b:
            continue
        verdict = "정보 부족" if not a or not b else ("비교 가능" if set(a) & set(b) else "조건 차이")
        out[cid] = {"verdict": verdict, "turboquant": a[0] if a else "근거 없음", "cxl_pnm": b[0] if b else "근거 없음",
                    "note": "(FAKE) 문자열 대조"}
    return out


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
    assessment["_comparability"] = _fake_comparisons(state)
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
    feedback = state.get("synthesis_feedback")          # orchestrator 품질 평가 미달 시 재생성 피드백 (MainState에는 없음)
    if feedback:
        system += ("\n\n[이전 생성본의 품질 평가 미달 사유 — 이번 생성에서 반드시 고칠 것]\n" + str(feedback))
    names = {t["tech_id"]: t["name"] for t in state["technologies"]}
    user = "\n\n".join([_tech_block(state, t["tech_id"]) for t in state["technologies"]]
                       + ["## 상충 후보", _conflict_block(state, names),
                          "## 항목별 실험 조건 (근거 등급 높은 순, 2.4 비교표 판정용)", _condition_block(state)])
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _get_llm():
    from ..llm import chat_model   # 프로젝트 공용 (KV_LLM_MODEL 우선, gpt-5 계열 temperature 미지정, .env 키 우선)
    return chat_model().with_structured_output(_Synthesis)


def _normalize_cid(raw: str, known: list[str]) -> str:
    """LLM이 'TRL-1 검증 환경 수준'처럼 id에 이름을 붙여 돌려줘도 루브릭 id로 맞춘다."""
    raw = (raw or "").strip()
    for cid in sorted(known, key=len, reverse=True):
        if raw == cid or raw.startswith(cid + " ") or raw.startswith(cid + ":") or f" {cid} " in f" {raw} ":
            return cid
    return raw.split()[0] if raw else raw


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
    known = [c["id"] for c in state["rubrics"]["criteria"]]
    assessment["_comparability"] = {
        _normalize_cid(x.criterion_id, known): {"verdict": x.verdict, "turboquant": x.turboquant_conditions,
                                                "cxl_pnm": x.cxl_pnm_conditions, "note": x.note}
        for x in getattr(out, "condition_comparisons", [])}   # 구버전 출력(테스트 FakeOut)에도 관대하게
    return {"conflicts": conflicts, "final_assessment": assessment}


def synthesize(state: MainState) -> dict:
    if os.getenv("KV_FAKE") == "1":
        return _fake(state)
    progress.step("synthesize", "관점 간 상충 해석·종합 서술 LLM 호출")
    messages = _build_messages(state)
    out: _Synthesis = _get_llm().invoke(messages)
    progress.step("synthesize", "완료")
    return _apply(state, out)
