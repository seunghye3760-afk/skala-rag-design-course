"""관점 에이전트: trl. 담당 2.
TRL-1~5 차원 점수만 채점 (TRL 단계 산출은 apply_rules). 서열 척도, 상한·하한 미적용 (설계서 D-8)."""
from __future__ import annotations

from ..graph.task_schema import CriterionResult, ScoreTask
from ._judge import llm_scores

AGENT = "trl"
PROMPT_FILE = "prompts/score/trl.md"


def score(task: ScoreTask, rubrics: dict) -> list[CriterionResult]:
    return llm_scores(task, rubrics, AGENT, PROMPT_FILE)
