"""관점 에이전트: domain. 담당 2.
DOM-1~5 채점. 모든 수치에 조건(모델·문맥 길이·배치·부하), 검증 범위 밖이면 명시 (설계서 D-8)."""
from __future__ import annotations

from ..graph.task_schema import CriterionResult, ScoreTask
from ._fake import fake_scores

AGENT = "domain"
PROMPT_FILE = "prompts/score/domain.md"


def score(task: ScoreTask, rubrics: dict) -> list[CriterionResult]:
    """TODO(담당 2): rubrics["judge"]["common_prompt"] + rubrics["agents"]["domain"]["judge_addon"]
    +  로 LLM 채점. langchain-openai with_structured_output(CriterionResult) 권장.
    task.evidence만 근거로 쓰고, tech_brief는 배경 참고용."""
    return fake_scores(task)
