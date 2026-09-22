"""Tavily 웹 검색. 담당 2."""
from __future__ import annotations


def search(query: str, max_results: int = 5) -> list[dict]:
    """TODO(담당 2): tavily-python 호출 → [{"url", "title", "content", "published_date"}].
    - 두 기술에 같은 max_results·검색 기간 (configs/runtime.yaml)
    - 쿼리·검색일 기준으로 .cache/web/ 에 캐시 (재실행 때 API 재호출 안 하게)
    - 결과 요약(content)으로 claim을 만들지 말 것 → open_source.fetch로 원문 확인"""
    raise NotImplementedError("tools/web_search.py 구현 필요 (담당 2)")
