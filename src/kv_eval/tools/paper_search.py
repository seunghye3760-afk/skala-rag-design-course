"""논문 검색 도구 (다른 담당자가 호출하는 창구). 담당 1."""
from __future__ import annotations

from ..graph.task_schema import Chunk
from ..rag.retriever import hybrid_search


def search(query: str, k: int = 5, doc_ids: list[str] | None = None) -> list[Chunk]:
    """한국어/영어 질의 → 논문 청크(locator 포함). 결과는 .cache/에 캐시하는 것을 권장."""
    return hybrid_search(query, k=k, doc_ids=doc_ids)
