"""보고서 품질 평가 결과 형식 (Hybrid: 규칙 검사 + LLM Judge)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..graph.task_schema import TechId

Axis = Literal["groundedness", "neutrality", "bias", "coverage"]
AXES: tuple[Axis, ...] = ("groundedness", "neutrality", "bias", "coverage")
Action = Literal["pass", "replan", "resynthesize", "give_up"]


class QualityIssue(BaseModel):
    axis: Axis
    source: Literal["rule", "judge"]
    detail: str
    tech_id: TechId | None = None      # 셀 단위 문제면 채워짐 → 재계획 대상
    criterion_id: str | None = None
    quote: str | None = None           # judge 지적 문장 (보고서에 실제로 있는 문장만 남김)

    @property
    def is_cell(self) -> bool:
        return bool(self.tech_id and self.criterion_id)


class AxisVerdict(BaseModel):
    axis: Axis
    passed: bool
    rule_passed: bool
    judge_score: int | None = None     # 1~5, judge를 안 쓰면 None
    issues: list[QualityIssue] = Field(default_factory=list)


class EvalVerdict(BaseModel):
    round: int                         # quality_round (0부터)
    passed: bool
    action: Action                     # 다음 경로 결정
    axes: list[AxisVerdict]
    reason: str                        # 결정 사유

    def failed_issues(self) -> list[QualityIssue]:
        return [i for a in self.axes if not a.passed for i in a.issues]
