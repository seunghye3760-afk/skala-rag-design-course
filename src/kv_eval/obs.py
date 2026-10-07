"""관측성 계층 — 결정 로그는 State가 아니라 여기서 외부 파일로 보낸다.

- decisions.jsonl: {ts, trace_id, run_id, node, decision, reason, ...} 한 줄씩 (라우팅 사유·계획·재작업 판단)
- plans/r{n}.json : 라운드별 전체 계획 (State에는 현재 계획만 두고 이전 계획은 여기로)
- LangSmith       : app.py가 invoke config의 metadata에 trace_id·run_id를 넣어 모든 하위 run에 전파

State와 외부 로그는 trace_id(=LangSmith metadata)와 run_id(=체크포인트 thread_id·출력 폴더명)로 잇는다.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import output_root


def run_dir(state: dict) -> Path:
    d = output_root() / state.get("run_id", "run")
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_decision(state: dict, node: str, decision: str, reason: str, **data) -> None:
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "trace_id": state.get("trace_id"),
           "run_id": state.get("run_id"), "node": node, "decision": decision, "reason": reason, **data}
    with (run_dir(state) / "decisions.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def dump_plan(state: dict, plan) -> Path:
    d = run_dir(state) / "plans"
    d.mkdir(exist_ok=True)
    path = d / f"r{plan.round}.json"
    path.write_text(json.dumps(plan.model_dump(), ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def langgraph_config(run_id: str, trace_id: str, recursion_limit: int) -> dict:
    """invoke config. thread_id=run_id로 체크포인트를 잇고, metadata로 LangSmith 트레이스와 잇는다."""
    return {"run_name": f"kv-eval {run_id}", "tags": ["orchestrator-workers"],
            "metadata": {"trace_id": trace_id, "run_id": run_id},
            "configurable": {"thread_id": run_id}, "recursion_limit": recursion_limit}
