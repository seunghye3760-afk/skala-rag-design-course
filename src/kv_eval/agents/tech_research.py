"""기술 조사 에이전트 (LLM + 논문 RAG). 담당 1.
논문에서 원리·수치·검증 조건을 공통 양식(설계서 B-3 tech_briefs)으로 추출한다."""
from __future__ import annotations

from ..graph.state import MainState

BRIEF_FIELDS = ["kv_cache_구성", "처리_구조", "실험_조건", "평가_결과", "적용_한계"]


def tech_research(state: MainState) -> dict:
    """TODO(담당 1): prompts/tech_research.md + tools.paper_search.search 로 채우기.
    보고되지 않은 항목은 '미보고', 판단 불가는 '확인 불가'."""
    briefs = {t["tech_id"]: {f: "(FAKE) 미구현" for f in BRIEF_FIELDS} for t in state["technologies"]}
    return {"tech_briefs": briefs}
