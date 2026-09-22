"""종합 에이전트 (LLM). 담당 4.
상충 후보를 근거·조건과 대조해 '관점 간 상충 / 조건 차이 / 근거 부족' 중 하나로 서술한다.
점수 합산·순위·우열 판정 금지."""
from __future__ import annotations

from ..graph.state import MainState


def synthesize(state: MainState) -> dict:
    """TODO(담당 4): prompts/synthesis.md 로 LLM 호출."""
    conflicts = [c.model_copy(update={"interpretation": c.interpretation or "(FAKE) 미해석"})
                 for c in state.get("conflicts", [])]
    assessment = {t["tech_id"]: {"요약": "(FAKE) 미구현", "한계": [], "시사점": []} for t in state["technologies"]}
    return {"conflicts": conflicts, "final_assessment": assessment}
