"""품질 평가 2안 — LLM Judge.

비결정성 통제:
  - 위반마다 보고서 종합 서술의 문장을 그대로 인용(quote)하게 하고, 코드가 그 문장이 실제로
    서술에 있는지 대조한다. 없는 문장을 인용한 위반은 버린다 (judge의 환각 차단).
  - 축 판정은 "점수 < judge_pass_score 이고, 검증된 위반이 1건 이상"일 때만 실패로 본다.
    점수만 낮고 근거 문장을 못 댄 판정으로는 루프를 돌리지 않는다.
"""
from __future__ import annotations

import re
from collections import Counter

from pydantic import BaseModel, Field

from ..config import ROOT
from ..graph.state import MainState
from ..graph.task_schema import TechId
from .checks import _cited, narrative_texts
from .schema import Axis, QualityIssue


class _AxisScore(BaseModel):
    axis: Axis
    score: int = Field(ge=1, le=5)
    comment: str


class _Violation(BaseModel):
    axis: Axis
    quote: str
    problem: str
    tech_id: TechId | None = None
    criterion_id: str | None = None


class _Judgement(BaseModel):
    axes: list[_AxisScore]
    violations: list[_Violation]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _sections() -> dict[str, str]:
    text = (ROOT / "prompts" / "quality" / "judge.md").read_text(encoding="utf-8")
    parts = re.split(r"^## (\w+)\s*$", text, flags=re.M)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


def _inputs(state: MainState) -> dict:
    finals, by_tech = _cited(state)
    cov_lines = []
    for t in state["technologies"]:
        cnt, tot = Counter(), Counter()
        for r in finals.values():
            if r.tech_id == t["tech_id"]:
                tot[r.agent_type] += 1
                cnt[r.agent_type] += r.score != "NA"
        cov_lines.append(f"- {t['name']}: " + ", ".join(f"{a} {cnt[a]}/{tot[a]}" for a in sorted(tot)))
    ev_lines = [f"{e.evidence_id} | {e.tech_id} | {e.criterion_id} | {e.stance} | {e.evidence_grade} | "
                f"{e.publisher} | {e.claim[:160]}" for evs in by_tech.values() for e in evs]
    return {"techs": ", ".join(f"{t['name']} ({t['tech_id']})" for t in state["technologies"]),
            "coverage": "\n".join(cov_lines),
            "evidence": "\n".join(ev_lines[:150]) or "(없음)",
            "narrative": "\n\n".join(narrative_texts(state))[:12000]}


def judge(state: MainState) -> tuple[dict[str, int], list[QualityIssue], int]:
    """→ (축별 점수, 검증된 위반, 버린 위반 수)"""
    from ..llm import chat_model

    p = _sections()
    inp = _inputs(state)
    out: _Judgement = chat_model().with_structured_output(_Judgement).invoke(
        [("system", p["system"]), ("user", p["user"].format(**inp))])

    haystack = _norm(inp["narrative"])
    valid_cells = {(r.tech_id, r.criterion_id) for r in _cited(state)[0].values()}
    issues, dropped = [], 0
    for v in out.violations:
        q = _norm(v.quote)
        if len(q) < 8 or q not in haystack:
            dropped += 1
            continue
        cell = (v.tech_id, v.criterion_id) in valid_cells
        issues.append(QualityIssue(axis=v.axis, source="judge", quote=q[:200], detail=v.problem,
                                   tech_id=v.tech_id if cell else None,
                                   criterion_id=v.criterion_id if cell else None))
    scores = {a.axis: a.score for a in out.axes}
    return scores, issues, dropped

