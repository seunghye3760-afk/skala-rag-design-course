"""Hit@K·MRR (설계서 B-4 (6)). 담당 1."""


def hit_at_k(ranked: list[bool], k: int = 5) -> float:
    return float(any(ranked[:k]))


def mrr(ranked: list[bool]) -> float:
    return next((1 / (i + 1) for i, ok in enumerate(ranked) if ok), 0.0)
