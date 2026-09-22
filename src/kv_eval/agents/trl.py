"""관점 에이전트: trl. 담당 2.
TRL-1~5 차원 점수만 채점 (TRL 단계 산출은 apply_rules). 서열 척도, 상한·하한 미적용 (설계서 D-8)."""
from __future__ import annotations

from ..graph.task_schema import CriterionResult, ScoreTask
from ._fake import fake_scores

AGENT = "trl"
PROMPT_FILE = "prompts/score/trl.md"


def score(task: ScoreTask, rubrics: dict) -> list[CriterionResult]:
    """TODO(담당 2): rubrics["judge"]["common_prompt"] + rubrics["agents"]["trl"]["judge_addon"]
    +  로 LLM 채점. langchain-openai with_structured_output(CriterionResult) 권장.
    task.evidence만 근거로 쓰고, tech_brief는 배경 참고용."""
    return fake_scores(task)
