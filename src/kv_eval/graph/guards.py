"""종료 보장 — 조정 노드(plan_tasks·balance_check·quality_eval)를 지날 때마다 step_count를 올리고,
상한(orchestrator.max_control_steps)에 닿으면 각 라우터가 루프 대신 마무리 경로를 고른다.

루프별 상한(retry_round·quality_round)이 1차 장치이고, 이것은 두 루프가 서로를 다시 여는 경우까지
막는 전체 상한이다. LangGraph recursion_limit(app.py)이 마지막 안전망.
"""
from __future__ import annotations

from ..config import runtime


def control_step(state: dict, node: str, status: str) -> dict:
    return {"step_count": state.get("step_count", 0) + 1, "node_status": {node: status}}


def step_limit_reached(state: dict) -> bool:
    return state.get("step_count", 0) >= runtime()["orchestrator"]["max_control_steps"]
