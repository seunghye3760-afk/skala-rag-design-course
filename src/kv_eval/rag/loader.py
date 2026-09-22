"""논문 PDF 로딩. 담당 1(문서·임베딩·검색).

pypdf로 쪽별 텍스트를 뽑고, 줄 단위로 끊긴 텍스트를 문단으로 복원한다.
문단 사이는 빈 줄("\\n\\n")로 구분해 chunker가 절·문단 경계로 자를 수 있게 한다 (설계서 B-4-1).
"""
from __future__ import annotations

import re
import statistics

from .corpus import pdf_path

# 문단 분리에 쓰는 패턴 (chunker도 같은 규칙을 쓴다)
HEADING_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+([A-Z][^.!?]{1,90})$")
NAMED_HEADINGS = {"abstract", "introduction", "background", "related work", "conclusion", "conclusions",
                  "discussion", "acknowledgments", "acknowledgements", "references", "appendix"}
CAPTION_RE = re.compile(r"^(Table|Figure|Fig\.)\s*(\d+)\s*[:.|]")

_NOISE_RE = [
    re.compile(r"^arXiv:\d{4}\.\d{4,5}v\d"),       # arXiv 옆 도장
    re.compile(r"^\d{1,3}$"),                          # 쪽 번호만 있는 줄
    re.compile(r"^Preprint\b|^Under review\b", re.I),
]
_END_RE = re.compile(r"[.!?:;)\]]$")


def load_pdf(doc_id: str) -> list[dict]:
    """data/raw/papers/{doc_id}.pdf → [{"doc_id", "page", "text"}] (쪽 번호 보존, 1부터).

    text는 문단 단위로 복원되어 "\\n\\n"로 이어져 있다. 쪽 번호(page)는 Evidence locator에 그대로 쓰인다.
    """
    from pypdf import PdfReader  # 함수 안 import (CLAUDE.md 규칙)

    reader = PdfReader(str(pdf_path(doc_id)))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        raw = page.extract_text() or ""
        pages.append({"doc_id": doc_id, "page": i, "text": to_paragraphs(raw)})
    return pages


def is_heading(line: str) -> bool:
    line = line.strip()
    if line.lower().rstrip(".") in NAMED_HEADINGS:
        return True
    m = HEADING_RE.match(line)
    return bool(m) and len(m.group(2).split()) <= 12 and not line.endswith(",")


def to_paragraphs(raw: str) -> str:
    """줄 단위 텍스트 → 문단 텍스트.

    문단 경계로 보는 경우: 절 제목 줄, 표·그림 캡션 시작 줄, 또는
    앞 줄이 문장 부호로 끝나면서 평균 줄 길이보다 눈에 띄게 짧을 때(문단의 마지막 줄).
    줄 끝 하이픈으로 끊긴 단어("quanti-\\nzation")는 붙인다.
    """
    lines = [ln.strip() for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln and not any(p.match(ln) for p in _NOISE_RE)]
    if not lines:
        return ""
    typical = statistics.median(len(ln) for ln in lines)

    paras: list[str] = []
    cur = ""
    for ln in lines:
        boundary = is_heading(ln) or bool(CAPTION_RE.match(ln))
        if boundary:
            if cur:
                paras.append(cur)
            if is_heading(ln):          # 제목은 한 줄짜리 문단
                paras.append(ln)
                cur = ""
            else:                       # 캡션은 새 문단 시작
                cur = ln
            continue
        if not cur:
            cur = ln
        elif cur.endswith("-") and ln[:1].islower():
            cur = cur[:-1] + ln          # 하이픈 연결
        else:
            cur = f"{cur} {ln}"
        if _END_RE.search(ln) and len(ln) < 0.7 * typical:
            paras.append(cur)            # 짧게 끝난 줄 = 문단 끝
            cur = ""
    if cur:
        paras.append(cur)
    return "\n\n".join(paras)
