"""임베딩 + FAISS·BM25 인덱스 생성·로드. 담당 1.

저장 위치: data/indexes/{모델 slug}/
  faiss.index   정규화 벡터의 내적(IndexFlatIP) — 청크 수백 개 규모라 정확 검색으로 충분
  chunks.jsonl  벡터 순서와 같은 순서의 Chunk (locator 포함)
  meta.json     모델명, manifest 해시, 청크 설정, 청크 지문 → 설정이 바뀌면 재생성하라고 알린다
BM25는 pickle 대신 chunks.jsonl로 로드 시 다시 만든다 (재현성·보안, 수백 개라 즉시 생성).

실행: uv run python -m kv_eval.rag.index            (runtime.yaml의 embedding.model)
      uv run python -m kv_eval.rag.index --model intfloat/multilingual-e5-large
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from ..config import runtime
from ..graph.task_schema import Chunk
from .corpus import INDEX_ROOT, doc_ids, manifest, manifest_hash
from .embeddings import encode_docs, slug

# ---------------------------------------------------------------- BM25 토크나이저
# 코퍼스는 영어 논문뿐이다. 한국어 질문에서는 영문 용어(TurboQuant, KV, LongBench …)만 남는다.
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.\-_][a-z0-9]+)*")
_STOP = set("""a an the and or of to in on for with by from at as is are was were be been being this that
these those it its we our they their which who whom what when where how than then there here such via into
can could may might will would should do does did not no nor also only both each other more most very using
use used based per""".split())


def english_tokenize(text: str) -> list[str]:
    """소문자 → 영문·숫자 토큰 → 불용어 제거. 'cxl-pnm'은 통째로 + 나눠서 둘 다 넣는다."""
    out = []
    for tok in _TOKEN_RE.findall(text.lower()):
        if tok in _STOP:
            continue
        out.append(tok)
        if "-" in tok or "_" in tok:
            out.extend(p for p in re.split(r"[-_]", tok) if p and p not in _STOP)
    return out


# ---------------------------------------------------------------- 생성
def _cfg_key() -> dict:
    c = runtime().get("chunk", {})
    return {"max_chars": int(c.get("max_chars", 1200)), "overlap_chars": int(c.get("overlap_chars", 200))}


def _fingerprint(chunks: list[Chunk]) -> str:
    h = hashlib.sha256()
    for c in chunks:
        h.update(c.chunk_id.encode())
        h.update(c.text.encode())
    return h.hexdigest()[:12]


def index_dir(model_name: str) -> Path:
    return INDEX_ROOT / slug(model_name)


def build_indexes(chunks: list[Chunk]) -> None:
    """configs/runtime.yaml embedding.model(기본 bge-m3)로 dense 임베딩 → FAISS,
    같은 청크로 BM25 → data/indexes/ 에 저장."""
    build_for_model(chunks, runtime()["embedding"]["model"])


def build_for_model(chunks: list[Chunk], model_name: str) -> Path:
    """임베딩 선정 실험(eval/retrieval)에서도 같은 함수로 모델별 인덱스를 만든다."""
    import faiss  # 무거움: 함수 안 import

    if not chunks:
        raise ValueError("청크가 0개입니다. PDF 로딩·청킹 결과를 확인하세요.")
    device = runtime()["embedding"].get("device", "cpu")
    vecs = encode_docs([c.text for c in chunks], model_name, device)
    index = faiss.IndexFlatIP(vecs.shape[1])
    index.add(vecs)

    out = index_dir(model_name)
    out.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(out / "faiss.index"))
    with (out / "chunks.jsonl").open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(c.model_dump_json() + "\n")
    meta = {"model": model_name, "dim": int(vecs.shape[1]), "n_chunks": len(chunks),
            "manifest_hash": manifest_hash(), "chunk": _cfg_key(), "fingerprint": _fingerprint(chunks),
            "built_at": datetime.now().isoformat(timespec="seconds")}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    load_index.cache_clear()
    return out


# ---------------------------------------------------------------- 로드
@dataclass
class LoadedIndex:
    model: str
    chunks: list[Chunk]
    faiss: object
    bm25: object
    meta: dict


@lru_cache(maxsize=4)
def load_index(model_name: str | None = None) -> LoadedIndex:
    """인덱스를 한 번만 읽어 프로세스 안에서 재사용한다 (모델 로딩이 가장 비싸다)."""
    import faiss
    from rank_bm25 import BM25Okapi

    model_name = model_name or runtime()["embedding"]["model"]
    d = index_dir(model_name)
    if not (d / "meta.json").exists():
        raise FileNotFoundError(f"{d} 에 인덱스 없음 → `uv run python -m kv_eval.rag.index --model {model_name}`")
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    if meta["manifest_hash"] != manifest_hash() or meta["chunk"] != _cfg_key():
        raise RuntimeError(f"{d} 인덱스가 현재 manifest·청크 설정과 다릅니다 → 인덱스를 다시 만드세요.")
    chunks = [Chunk(**json.loads(x)) for x in (d / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    bm25 = BM25Okapi([english_tokenize(c.text) or ["_"] for c in chunks])
    return LoadedIndex(model_name, chunks, faiss.read_index(str(d / "faiss.index")), bm25, meta)


# ---------------------------------------------------------------- CLI
def load_and_chunk() -> list[Chunk]:
    """manifest의 모든 논문 → 로딩 → 청킹 (data/processed/에도 저장)."""
    from .chunker import chunk_pages
    from .loader import load_pdf

    manifest()  # 200쪽 검사
    pages = [p for d in doc_ids() for p in load_pdf(d)]
    return chunk_pages(pages)


def main() -> None:
    ap = argparse.ArgumentParser(description="논문 인덱스 생성")
    ap.add_argument("--model", default=None, help="기본: configs/runtime.yaml embedding.model")
    args = ap.parse_args()
    chunks = load_and_chunk()
    out = build_for_model(chunks, args.model or runtime()["embedding"]["model"])
    per_doc = {d: sum(c.locator.doc_id == d for c in chunks) for d in doc_ids()}
    print(f"청크 {len(chunks)}개 {per_doc} → {out}")


if __name__ == "__main__":
    main()
