"""하이브리드 검색 (FAISS dense + BM25 sparse → RRF 결합). 담당 1."""
from __future__ import annotations

from ..graph.task_schema import Chunk


def hybrid_search(query: str, k: int = 5, doc_ids: list[str] | None = None) -> list[Chunk]:
    """TODO(담당 1): 두 검색 결과를 순위 기반으로 결합해 상위 k개 Chunk 반환 (locator 필수)."""
    raise NotImplementedError("rag/retriever.py 구현 필요 (담당 1)")
