"""문서·임베딩·검색 (담당 1). PDF·모델 없이 도는 테스트 + 인덱스가 있을 때만 도는 테스트."""
import importlib.util
import sys
from pathlib import Path

import pytest

from kv_eval.graph.task_schema import Chunk, Locator
from kv_eval.rag import chunker, corpus
from kv_eval.rag.index import english_tokenize
from kv_eval.rag.loader import is_heading, to_paragraphs
from kv_eval.rag.retriever import rrf_fuse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval" / "retrieval"))
from metrics import is_gold, summarize  # noqa: E402


def test_manifest_within_200_pages():
    assert sum(int(r["pages"]) for r in corpus.manifest()) <= corpus.MAX_PAGES
    assert set(corpus.doc_ids()) == {"turboquant", "cxl_pnm"}      # doc_id == tech_id


def test_heading_detection():
    assert is_heading("4.3 Long-Context Evaluation")
    assert is_heading("References")
    assert not is_heading("4 GPUs were used in total.")
    assert not is_heading("We evaluate TurboQuant on LongBench.")


def test_to_paragraphs_restores_paragraphs_and_hyphens():
    raw = ("1 Introduction\n"
           "Large language models store keys and values for every token, and the cache\n"
           "grows linearly with context length which quickly exhausts accelerator memo-\n"
           "ry capacity in long-context serving.\n"
           "Table 1: Accuracy on LongBench.\n"
           "12\n")
    paras = to_paragraphs(raw).split("\n\n")
    assert paras[0] == "1 Introduction"
    assert "memory capacity" in paras[1]                 # 하이픈 연결
    assert paras[-1].startswith("Table 1:")             # 캡션은 별도 문단, 쪽 번호 줄은 제거


@pytest.fixture
def pages():
    body = " ".join(["The KV cache is quantized to 3.5 bits per channel with negligible loss."] * 25)
    return [
        {"doc_id": "turboquant", "page": 1,
         "text": "Abstract\n\nWe study KV cache compression.\n\n1 Introduction\n\nKV cache grows with context."},
        {"doc_id": "turboquant", "page": 2,
         "text": f"4.3 Long-Context Evaluation\n\n{body}\n\nTable 2: LongBench results by bit-width."},
        {"doc_id": "turboquant", "page": 3, "text": "References\n\n[1] Some paper. 2024."},
    ]


def test_chunk_pages_locator_and_rules(pages, tmp_path, monkeypatch):
    monkeypatch.setattr(chunker, "PROCESSED_DIR", tmp_path)
    chunks = chunker.chunk_pages(pages)
    assert chunks and all(c.locator.page in (1, 2) for c in chunks)          # References(3쪽) 제외
    assert all(len(c.text) <= 1200 for c in chunks)
    for c in chunks:                                                        # chunk_id 규칙, locator 필수값
        assert c.chunk_id == c.locator.chunk_id
        assert c.chunk_id.startswith(f"turboquant-p{c.locator.page}-")
        assert c.locator.version == "v1"
    p2 = [c for c in chunks if c.locator.page == 2]
    assert len(p2) >= 2 and all(c.locator.section == "4.3 Long-Context Evaluation" for c in p2)
    assert p2[-1].locator.figure_table == "Table 2"                          # 캡션은 앞 문단과 함께
    assert (tmp_path / "turboquant.jsonl").exists()


def test_english_tokenize_korean_query():
    toks = english_tokenize("TurboQuant의 KV cache 양자화는 3.5-bit에서 LongBench 정확도를 유지하나?")
    assert {"turboquant", "kv", "cache", "longbench", "3.5-bit", "bit"} <= set(toks)
    assert "cxl" in english_tokenize("CXL-PNM 처리량") and "cxl-pnm" in english_tokenize("CXL-PNM 처리량")


def _c(cid: str) -> Chunk:
    return Chunk(chunk_id=cid, text=cid, locator=Locator(doc_id="turboquant", page=1, chunk_id=cid))


def test_rrf_prefers_items_found_by_both():
    sparse = [_c("a"), _c("b"), _c("c")]
    dense = [_c("b"), _c("d"), _c("a")]
    out = rrf_fuse([sparse, dense], [0.5, 0.5], k=3)
    assert [c.chunk_id for c in out][:2] == ["b", "a"] and out[0].score > out[-1].score


@pytest.mark.skipif(importlib.util.find_spec("rank_bm25") is None, reason="rank-bm25 미설치")
def test_bm25_search_with_doc_filter(monkeypatch):
    from rank_bm25 import BM25Okapi

    from kv_eval.rag import retriever
    from kv_eval.rag.index import LoadedIndex

    texts = [("turboquant", "TurboQuant quantizes the KV cache to 3 bits"),
             ("cxl_pnm", "CXL PNM offloads attention for 1M token contexts"),
             ("turboquant", "LongBench accuracy is preserved at 3.5 bits")]
    chunks = [Chunk(chunk_id=f"{d}-p1-{i}", text=t, locator=Locator(doc_id=d, page=1))
              for i, (d, t) in enumerate(texts)]
    fake = LoadedIndex("m", chunks, None, BM25Okapi([english_tokenize(c.text) for c in chunks]), {})
    monkeypatch.setattr(retriever, "load_index", lambda model_name=None: fake)
    out = retriever.bm25_search("KV cache 3 bits 양자화", k=5, doc_ids=["turboquant"])
    assert out and all(c.locator.doc_id == "turboquant" for c in out)
    assert retriever.bm25_search("양자화 정확도", k=5) == []            # 영문 용어 없으면 BM25 생략


def test_metrics_gold_match():
    gold = {"doc_id": "turboquant", "page": 12, "section": "4.3"}
    assert is_gold(Locator(doc_id="turboquant", page=12, section="4.3 Long-Context"), gold)
    assert not is_gold(Locator(doc_id="turboquant", page=12, section="4.2 Setup"), gold)
    assert summarize([[False, True], [True]])["MRR"] == 0.75


@pytest.mark.skipif(not (corpus.INDEX_ROOT / "bge-m3" / "meta.json").exists(), reason="인덱스 없음")
def test_real_search_is_deterministic_and_filtered():
    from kv_eval.tools.paper_search import search

    a = search("TurboQuant LongBench 정확도", k=5, doc_ids=["turboquant"])
    b = search("TurboQuant LongBench 정확도", k=5, doc_ids=["turboquant"])
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    assert a and all(c.locator.doc_id == "turboquant" and c.locator.page for c in a)
