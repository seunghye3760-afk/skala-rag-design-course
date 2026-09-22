"""관점 에이전트: stakeholder. 담당 3.
STK-1~4 채점. 근거마다 발언 주체 유형과 이해당사자 여부 표기 (설계서 D-8)."""
from __future__ import annotations

from ..graph.task_schema import CriterionResult, ScoreTask
from ._fake import fake_scores

AGENT = "stakeholder"
PROMPT_FILE = "prompts/score/stakeholder.md"


def score(task: ScoreTask, rubrics: dict) -> list[CriterionResult]:
    """TODO(담당 3): rubrics["judge"]["common_prompt"] + rubrics["agents"]["stakeholder"]["judge_addon"]
    +  로 LLM 채점. langchain-openai with_structured_output(CriterionResult) 권장.
    task.evidence만 근거로 쓰고, tech_brief는 배경 참고용."""
    return fake_scores(task)
