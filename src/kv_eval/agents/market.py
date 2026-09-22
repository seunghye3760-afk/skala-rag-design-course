"""관점 에이전트: market. 담당 3.
MKT-1~4 채점. 시장 수치는 조사 기관·기준 연도·시장 정의와 함께, 상위 시장 수치로 대체 금지 (설계서 D-8)."""
from __future__ import annotations

from ..graph.task_schema import CriterionResult, ScoreTask
from ._fake import fake_scores

AGENT = "market"
PROMPT_FILE = "prompts/score/market.md"


def score(task: ScoreTask, rubrics: dict) -> list[CriterionResult]:
    """TODO(담당 3): rubrics["judge"]["common_prompt"] + rubrics["agents"]["market"]["judge_addon"]
    +  로 LLM 채점. langchain-openai with_structured_output(CriterionResult) 권장.
    task.evidence만 근거로 쓰고, tech_brief는 배경 참고용."""
    return fake_scores(task)
