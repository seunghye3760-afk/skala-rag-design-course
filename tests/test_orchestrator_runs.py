"""Orchestrator 그래프가 가짜 모드로 끝까지 돌고, 산출물(report.md·decision_log.jsonl·report_meta.json)을 만드는지."""
import json
from pathlib import Path

from kv_eval.config import output_root
from kv_eval.orchestrator import build_orchestrator_graph, run_config


def test_orchestrator_runs_end_to_end():
    final = build_orchestrator_graph().invoke({"run_id": "o", "only_criteria": ["TRL-1", "DOM-4"]},
                                              config=run_config("o"))
    assert final["status"] in {"SUCCESS", "PARTIAL"}
    text = Path(final["report_path"]).read_text(encoding="utf-8")
    heads = [l for l in text.splitlines() if l.startswith("## ")]
    assert heads[0] == "## SUMMARY" and heads[-1] == "## REFERENCE"
    assert len(final["final_results"]) == 4
    assert [f["count"] for f in final["fanout_log"]] == [4]            # 계획에서 나온 fan-out 수
    assert all(s == "done" for k, s in final["node_status"].items() if not k.startswith("score:"))
    log = output_root() / "o" / "decision_log.jsonl"
    assert log.exists() and {json.loads(l)["node"] for l in log.read_text().splitlines()} >= {"plan_tasks", "finalize"}
    meta = json.loads((output_root() / "o" / "report_meta.json").read_text())
    assert meta["run_id"] == "o" and meta["status"] == final["status"]


def test_run_config_carries_correlation_key():
    cfg = run_config("abc")
    assert cfg["run_name"] == "abc" and cfg["metadata"]["run_id"] == "abc" and cfg["configurable"]["thread_id"] == "abc"
    assert "recursion_limit" in cfg and "max_concurrency" in cfg


def test_legacy_graph_unchanged():
    """app.py(RAG 실습용) 그래프는 그대로 — 노드 목록에 orchestrator 노드가 없어야 한다."""
    from kv_eval.graph.main import build_graph
    nodes = set(build_graph().get_graph().nodes)
    assert "dispatch_collect" in nodes and "plan_tasks" not in nodes and "evaluate_report" not in nodes
