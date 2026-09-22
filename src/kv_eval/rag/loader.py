"""논문 PDF 로딩. 담당 1(문서·임베딩·검색)."""
from __future__ import annotations


def load_pdf(doc_id: str) -> list[dict]:
    """data/raw/papers/{doc_id}.pdf → [{"doc_id", "page", "text"}] (쪽 번호 보존).

    TODO(담당 1): pypdf로 쪽별 텍스트 추출. 쪽 번호(page)는 반드시 남길 것 (Evidence locator에 쓰임).
    """
    raise NotImplementedError("rag/loader.py load_pdf 구현 필요 (담당 1)")
