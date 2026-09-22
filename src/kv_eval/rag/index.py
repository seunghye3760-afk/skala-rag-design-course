"""임베딩 + FAISS·BM25 인덱스 생성·로드. 담당 1."""
from __future__ import annotations

from ..graph.task_schema import Chunk


def build_indexes(chunks: list[Chunk]) -> None:
    """TODO(담당 1): configs/runtime.yaml embedding.model(기본 bge-m3)로 dense 임베딩 → FAISS,
    같은 청크로 BM25 → data/indexes/ 에 저장. (sentence-transformers는 함수 안에서 import: 무거움)"""
    raise NotImplementedError("rag/index.py 구현 필요 (담당 1)")
