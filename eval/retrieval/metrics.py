"""Hit@K·MRR (설계서 B-4 (6)). 담당 1.

실습 14-Retriever/10-Retriever-Evaluation의 hit_rate_at_k·mrr_score를 옮겼다. 두 가지를 바꿨다.
- 정답 판정: 텍스트 겹침 비율 → 사람이 표시한 정답 위치(doc_id·쪽·절) 일치
- 질문마다 검색을 한 번만 하고, 그 순위로 Hit@1·3·5와 MRR을 함께 계산
"""
from __future__ import annotations


def hit_at_k(ranked: list[bool], k: int = 5) -> float:
    return float(any(ranked[:k]))


def mrr(ranked: list[bool]) -> float:
    return next((1 / (i + 1) for i, ok in enumerate(ranked) if ok), 0.0)


def is_gold(locator, gold: dict) -> bool:
    """검색된 청크가 정답 위치인가. 정답에 절이 적혀 있으면 절 번호까지 맞아야 한다.

    section은 청크 쪽에 "4.3 Long-context ..." 형태로 저장되므로 앞부분(번호)으로 비교한다.
    """
    if locator.doc_id != gold["doc_id"] or locator.page != gold["page"]:
        return False
    want = (gold.get("section") or "").strip()
    return not want or (locator.section or "").startswith(want)


def summarize(ranked_lists: list[list[bool]], ks: tuple[int, ...] = (1, 3, 5)) -> dict:
    n = len(ranked_lists)
    out = {f"Hit@{k}": round(sum(hit_at_k(r, k) for r in ranked_lists) / n, 4) for k in ks}
    out["MRR"] = round(sum(mrr(r) for r in ranked_lists) / n, 4)
    out["n"] = n
    return out
