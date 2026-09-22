"""근거 등급(A~D)·pro/con·측정 유형 분류 (설계서 C-4). 담당 2.
출처 유형·측정 유형 판정 규칙은 코드로 고정하고, LLM은 보조로만 쓴다 (설계서 D-8 주석)."""
from __future__ import annotations


def grade(source_type: str, measurement_type: str, is_independent: bool) -> str:
    """TODO(담당 2): A 1차·독립 실측 / B 1차·당사자 / C 시뮬레이션·추정 / D 2차 자료."""
    raise NotImplementedError("evidence/grading.py 구현 필요 (담당 2)")
