"""청킹. 절·문단 경계 기준, 표·그림 캡션은 해당 문단과 함께 (설계서 B-4-1). 담당 1."""
from __future__ import annotations

from ..graph.task_schema import Chunk


def chunk_pages(pages: list[dict]) -> list[Chunk]:
    """TODO(담당 1): 절 제목(section) 추정, chunk_id="{doc_id}-p{page}-{n}", locator 채우기.
    결과는 data/processed/{doc_id}.jsonl 로 저장."""
    raise NotImplementedError("rag/chunker.py 구현 필요 (담당 1)")
