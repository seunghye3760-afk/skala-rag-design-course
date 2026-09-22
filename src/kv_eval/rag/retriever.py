"""하이브리드 검색 (FAISS dense + BM25 sparse → RRF 결합). 담당 1.

- dense: 한국어·영어 질문 모두 그대로 임베딩 (bge-m3 교차언어 검색)
- sparse: BM25는 질문 속 영문 용어로만 매칭된다. 영문 토큰이 없으면 dense 결과만 쓴다.
- 결합: 가중 RRF  score = Σ w / (rrf_k + rank)  (LangChain EnsembleRetriever와 같은 식, 실습 14-04)
- doc_ids 필터: manifest의 doc_id == tech_id 이므로 doc_ids=["turboquant"]가 곧 기술 필터다.
"""
from __future__ import annotations

from ..config import runtime
from ..graph.task_schema import Chunk
from .embeddings import encode_query
from .index import english_tokenize, load_index


def _retrieval_cfg() -> dict:
    r = runtime().get("retrieval", {})
    return {"candidate_k": int(r.get("candidate_k", 20)), "rrf_k": int(r.get("rrf_k", 60)),
            "weights": r.get("weights", [0.5, 0.5])}


def _allowed(chunk: Chunk, doc_ids: list[str] | None) -> bool:
    return not doc_ids or chunk.locator.doc_id in doc_ids


def dense_search(query: str, k: int = 5, doc_ids: list[str] | None = None,
                 model_name: str | None = None) -> list[Chunk]:
    """FAISS 코사인 유사도. 청크 수가 적어 전체 점수를 구한 뒤 필터링한다."""
    idx = load_index(model_name)
    qv = encode_query(query, idx.model, runtime()["embedding"].get("device", "cpu"))
    scores, ids = idx.faiss.search(qv, idx.faiss.ntotal)
    out = []
    for s, i in zip(scores[0], ids[0]):
        if i < 0:
            continue
        c = idx.chunks[int(i)]
        if _allowed(c, doc_ids):
            out.append(c.model_copy(update={"score": float(s)}))
            if len(out) == k:
                break
    return out


def bm25_search(query: str, k: int = 5, doc_ids: list[str] | None = None,
                model_name: str | None = None) -> list[Chunk]:
    tokens = english_tokenize(query)
    if not tokens:
        return []
    idx = load_index(model_name)
    scores = idx.bm25.get_scores(tokens)
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    out = []
    for i in order:
        if scores[i] <= 0:
            break
        if _allowed(idx.chunks[i], doc_ids):
            out.append(idx.chunks[i].model_copy(update={"score": float(scores[i])}))
            if len(out) == k:
                break
    return out


def rrf_fuse(ranked_lists: list[list[Chunk]], weights: list[float], k: int, rrf_k: int = 60) -> list[Chunk]:
    """순위 기반 결합. 같은 chunk_id는 합산, 동점이면 먼저 나온 순서(결정적)."""
    score: dict[str, float] = {}
    first: dict[str, Chunk] = {}
    for w, lst in zip(weights, ranked_lists):
        for rank, c in enumerate(lst, start=1):
            score[c.chunk_id] = score.get(c.chunk_id, 0.0) + w / (rrf_k + rank)
            first.setdefault(c.chunk_id, c)
    order = sorted(score, key=lambda cid: -score[cid])[:k]
    return [first[cid].model_copy(update={"score": round(score[cid], 6)}) for cid in order]


def hybrid_search(query: str, k: int = 5, doc_ids: list[str] | None = None) -> list[Chunk]:
    """두 검색 결과를 순위 기반으로 결합해 상위 k개 Chunk 반환 (locator 필수)."""
    cfg = _retrieval_cfg()
    n = max(cfg["candidate_k"], k)
    sparse = bm25_search(query, n, doc_ids)
    dense = dense_search(query, n, doc_ids)
    if not sparse:                       # 질문에 영문 용어가 없으면 dense만
        return dense[:k]
    return rrf_fuse([sparse, dense], cfg["weights"], k, cfg["rrf_k"])
