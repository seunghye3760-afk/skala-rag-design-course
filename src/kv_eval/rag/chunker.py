"""청킹. 절·문단 경계 기준, 표·그림 캡션은 해당 문단과 함께 (설계서 B-4-1). 담당 1.

규칙
- 청크는 절(section)과 쪽(page)을 넘지 않는다 → Locator.page 하나로 원문 위치가 정확하다.
- 문단을 max_chars(기본 1200자 ≈ 300토큰)까지 이어 붙인다. E5의 512토큰 제한 안이라
  세 후보 모델을 같은 청크로 비교할 수 있다 (설계서 B-4 (6)).
- 한 문단이 max_chars보다 길면 문장 경계에서 자른다.
- 같은 절·쪽 안에서 이전 청크 끝 문장을 overlap_chars 이내로 다음 청크 앞에 붙인다.
- 캡션(Table n / Figure n)은 바로 앞 문단과 합치고 figure_table에 기록한다.
- References 이후는 인덱스에서 제외한다 (쪽수 계산에는 포함, corpus.py 참고).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

from ..config import runtime
from ..graph.task_schema import Chunk, Locator
from .corpus import PROCESSED_DIR, doc_row
from .loader import CAPTION_RE, is_heading

_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])")


def _cfg() -> tuple[int, int]:
    c = runtime().get("chunk", {})
    return int(c.get("max_chars", 1200)), int(c.get("overlap_chars", 200))


def _caption_label(text: str) -> str | None:
    m = CAPTION_RE.match(text)
    if not m:
        return None
    kind = "Table" if m.group(1) == "Table" else "Figure"
    return f"{kind} {m.group(2)}"


def _split_long(text: str, max_chars: int) -> list[str]:
    """max_chars를 넘는 문단을 문장 경계로 나눈다 (문장 하나가 너무 길면 공백 기준)."""
    if len(text) <= max_chars:
        return [text]
    out, cur = [], ""
    for sent in _SENT_RE.split(text):
        while len(sent) > max_chars:              # 문장 자체가 너무 긴 경우
            cut = sent.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            out.append((cur + " " + sent[:cut]).strip() if cur else sent[:cut])
            cur, sent = "", sent[cut:].strip()
        if cur and len(cur) + 1 + len(sent) > max_chars:
            out.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        out.append(cur)
    return out


def _tail(text: str, limit: int) -> str:
    """overlap용: 끝에서부터 limit자 이내의 완결 문장들."""
    tail = ""
    for sent in reversed(_SENT_RE.split(text)):
        if len(tail) + len(sent) + 1 > limit:
            break
        tail = f"{sent} {tail}".strip()
    return tail


def _units(pages: list[dict]) -> list[dict]:
    """쪽 텍스트 → 문단 단위 목록. 각 단위에 page·section·figure_table을 붙인다."""
    units: list[dict] = []
    section: str | None = None
    for p in sorted(pages, key=lambda x: x["page"]):
        for para in (s.strip() for s in p["text"].split("\n\n")):
            if not para:
                continue
            if is_heading(para):
                if para.lower().startswith("references"):
                    return units                   # 참고문헌부터는 인덱스 제외
                section = para
                continue
            label = _caption_label(para)
            prev = units[-1] if units else None
            if label and prev and prev["page"] == p["page"] and prev["section"] == section:
                prev["text"] = f"{prev['text']}\n{para}"      # 캡션은 앞 문단과 함께
                prev["captions"].append((label, para[:40]))
                continue
            units.append({"page": p["page"], "section": section, "text": para,
                          "captions": [(label, para[:40])] if label else []})
    return units


def chunk_pages(pages: list[dict]) -> list[Chunk]:
    """load_pdf 결과(여러 문서 섞여도 됨) → Chunk 목록.

    chunk_id = "{doc_id}-p{page}-{n}" (n은 쪽마다 1부터), locator에 doc_id·version·page·section·figure_table.
    결과는 data/processed/{doc_id}.jsonl 로도 저장한다 (사람 검토·원문 대조용).
    """
    max_chars, overlap = _cfg()
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for p in pages:
        by_doc[p["doc_id"]].append(p)

    chunks: list[Chunk] = []
    for doc_id, doc_pages in by_doc.items():
        version = doc_row(doc_id)["version"]
        groups: dict[tuple, list[dict]] = defaultdict(list)       # (page, section) 순서 보존
        for u in _units(doc_pages):
            groups[(u["page"], u["section"])].append(u)

        doc_chunks: list[Chunk] = []
        counter: dict[int, int] = defaultdict(int)
        for (page, section), units in groups.items():
            buf, labels, prev_text = "", [], ""

            def flush() -> None:
                nonlocal buf, labels, prev_text
                if not buf:
                    return
                counter[page] += 1
                cid = f"{doc_id}-p{page}-{counter[page]}"
                doc_chunks.append(Chunk(chunk_id=cid, text=buf, locator=Locator(
                    doc_id=doc_id, version=version, page=page, section=section,
                    figure_table=", ".join(dict.fromkeys(labels)) or None, chunk_id=cid)))
                prev_text, buf, labels = buf, "", []

            for u in units:
                for piece in _split_long(u["text"], max_chars):
                    if buf and len(buf) + 2 + len(piece) > max_chars:
                        flush()
                    if not buf and prev_text and overlap:
                        tail = _tail(prev_text, overlap)
                        if tail and len(tail) + 2 + len(piece) <= max_chars:
                            buf = tail
                    buf = f"{buf}\n\n{piece}" if buf else piece
                    labels.extend(lab for lab, head in u["captions"] if head in piece)
            flush()

        _save(doc_id, doc_chunks)
        chunks.extend(doc_chunks)
    return chunks


def _save(doc_id: str, chunks: list[Chunk]) -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with (PROCESSED_DIR / f"{doc_id}.jsonl").open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(c.model_dump_json() + "\n")


def load_processed(doc_ids: list[str]) -> list[Chunk]:
    """저장된 청크를 다시 읽는다 (인덱스 없이 청크만 볼 때)."""
    out = []
    for d in doc_ids:
        for line in (PROCESSED_DIR / f"{d}.jsonl").read_text(encoding="utf-8").splitlines():
            out.append(Chunk(**json.loads(line)))
    return out
