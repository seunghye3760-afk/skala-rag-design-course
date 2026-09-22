"""논문 검색 도구 (다른 담당자가 호출하는 창구). 담당 1.

사용 예 (근거 담당 evidence/collect.py):
    from kv_eval.tools import paper_search
    chunks = paper_search.search("TurboQuant accuracy long context LongBench", k=5, doc_ids=["turboquant"])
    for c in chunks:
        c.locator   # → Evidence.locator 에 그대로 (doc_id·version·page·section·figure_table·chunk_id)
        c.text      # → 원문 확인 후 Evidence.excerpt
반환 순서가 곧 순위이고, score는 RRF 점수(비교용이 아니라 순위 참고용)다.
"""
from __future__ import annotations

from ..graph.task_schema import Chunk
from ..rag.corpus import doc_ids as known_doc_ids
from ..rag.corpus import doc_row
from ..rag.retriever import hybrid_search


def doc_published(doc_id: str) -> str | None:
    """manifest의 게재일(YYYY-MM-DD). 논문 Evidence의 published_at에 쓴다 (recency 규칙용)."""
    return doc_row(doc_id).get("published") or None


def search(query: str, k: int = 5, doc_ids: list[str] | None = None) -> list[Chunk]:
    """한국어/영어 질의 → 논문 청크(locator 포함). doc_ids는 "turboquant" / "cxl_pnm"."""
    if doc_ids:
        unknown = set(doc_ids) - set(known_doc_ids())
        if unknown:
            raise ValueError(f"알 수 없는 doc_id {unknown} (data/corpus_manifest.csv 참고)")
    return hybrid_search(query, k=k, doc_ids=doc_ids)


def format_chunks(chunks: list[Chunk]) -> str:
    """LLM 입력용. 실습 rag/utils.format_docs 형식에 chunk_id·section·figure를 더했다."""
    return "\n".join(
        f'<document chunk_id="{c.chunk_id}" doc="{c.locator.doc_id}" page="{c.locator.page}" '
        f'section="{c.locator.section or ""}" figure_table="{c.locator.figure_table or ""}">\n'
        f"{c.text}\n</document>"
        for c in chunks)
