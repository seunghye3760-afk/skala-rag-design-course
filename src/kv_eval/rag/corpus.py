"""코퍼스 목록(data/corpus_manifest.csv)과 경로. 담당 1.

노션 조건: Doc Pool 문서만, 총 200쪽 이내. 쪽수는 참고문헌 포함으로 세고(보수적),
인덱스에서는 참고문헌을 뺀다(검색 노이즈 감소). 설계서 B-3 (1).
"""
from __future__ import annotations

import csv
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from ..config import ROOT

MAX_PAGES = 200
MANIFEST = ROOT / "data" / "corpus_manifest.csv"
PAPERS_DIR = ROOT / "data" / "raw" / "papers"
PROCESSED_DIR = ROOT / "data" / "processed"
INDEX_ROOT = ROOT / "data" / "indexes"


@lru_cache
def manifest() -> tuple[dict, ...]:
    """manifest 행을 읽고 200쪽 제한을 검사한다. 넘으면 실행을 멈춘다."""
    rows = tuple(csv.DictReader(MANIFEST.open(encoding="utf-8")))
    total = sum(int(r["pages"]) for r in rows)
    if total > MAX_PAGES:
        raise ValueError(f"코퍼스 {total}쪽 > {MAX_PAGES}쪽 제한 (data/corpus_manifest.csv 확인)")
    return rows


def doc_ids() -> list[str]:
    return [r["doc_id"] for r in manifest()]


def doc_row(doc_id: str) -> dict:
    for r in manifest():
        if r["doc_id"] == doc_id:
            return r
    raise KeyError(f"manifest에 없는 doc_id: {doc_id}")


def pdf_path(doc_id: str) -> Path:
    path = PAPERS_DIR / f"{doc_id}.pdf"
    if not path.exists():
        raise FileNotFoundError(f"{path} 없음 → `uv run python scripts/download_corpus.py` 먼저 실행")
    return path


def manifest_hash() -> str:
    """인덱스 재사용 판단용. 문서·버전·쪽수가 바뀌면 인덱스를 다시 만든다."""
    key = json.dumps([(r["doc_id"], r["version"], r["pages"]) for r in manifest()])
    return hashlib.sha256(key.encode()).hexdigest()[:12]
