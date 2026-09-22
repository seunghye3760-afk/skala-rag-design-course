"""파이프라인 진행 상황을 터미널에 한 줄씩 찍는 공용 유틸리티.

지금까지 각 노드가 제각각 print(f"[score_task] ...")·print(f"[report] ...") 식으로
찍던 걸 하나로 통일한다. 임베딩 인덱스 로드 이후 report.md 생성까지 아무 출력이 없어서
어느 단계가 진행 중인지 알 수 없었던 문제를 해결하기 위한 최소한의 장치다.

로그 자체가 기능에 영향을 주면 안 되므로 예외를 삼키지 않고 그냥 print만 한다 —
app.py·pytest·노트북 등 어떤 실행 방식에서도 그대로 보인다.
"""
from __future__ import annotations

import time


def step(tag: str, msg: str) -> None:
    """한 줄짜리 진행 로그. 예: step("collect_evidence", "turboquant TRL-1 시작")"""
    print(f"[{time.strftime('%H:%M:%S')}] [{tag}] {msg}", flush=True)
