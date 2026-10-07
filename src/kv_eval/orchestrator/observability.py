"""결정 로그 + LangSmith 실행 설정 (과제 프롬프트 §3 관측성·상관).

- State에는 사유의 요약만 남기고(SubTask.reason, QualityVerdict.feedback), 결정 로그 본문은
  outputs/runs/<run_id>/decision_log.jsonl 에 append 한다.
- run_id 하나가 LangSmith run_name·metadata, decision_log, 보고서 폴더의 공통 상관 키다.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .. import progress
from ..config import output_root, runtime


def decision_log_path(run_id: str) -> Path:
    d = output_root() / (run_id or "run")
    d.mkdir(parents=True, exist_ok=True)
    return d / "decision_log.jsonl"


def decision(run_id: str, node: str, decision: str, reason: str, **extra) -> None:
    """계획·제외·재시도·라우팅 결정을 한 줄씩 기록한다. 로그 실패가 파이프라인을 멈추면 안 된다."""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "run_id": run_id, "node": node,
           "decision": decision, "reason": reason, **extra}
    try:
        with decision_log_path(run_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except OSError as e:
        progress.step("decision_log", f"기록 실패 ({type(e).__name__}): {decision}")
    progress.step(node, f"[결정] {decision} — {reason}")


def run_config(run_id: str, thread_id: str | None = None) -> dict:
    """graph.invoke(..., config=run_config(run_id)). LangSmith가 켜져 있으면 run_name·metadata로 전달된다."""
    rt = runtime()
    return {
        "configurable": {"thread_id": thread_id or run_id},
        "max_concurrency": rt.get("concurrency", {}).get("max_concurrency", 8),
        "recursion_limit": rt.get("limits", {}).get("recursion_limit", 150),
        "run_name": run_id,
        "metadata": {"run_id": run_id, "pattern": "orchestrator-workers"},
        "tags": ["orchestrator", "kv-eval"],
    }
