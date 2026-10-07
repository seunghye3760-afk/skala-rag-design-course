"""State 설계 항목: 상관(trace_id·결정 로그), 종료 보장(step 상한), 재개/복구(체크포인트), 동시 처리(reducer)."""
import json
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from kv_eval.graph import guards
from kv_eval.graph import main as main_mod
from kv_eval.graph.main import build_graph
from kv_eval.obs import langgraph_config
from kv_eval.orchestrator import workers as workers_mod
from kv_eval.quality import evaluate
from kv_eval.quality.schema import QualityIssue


def test_decision_log_and_plan_files_carry_trace_id(tmp_path):
    final = build_graph().invoke({"run_id": "c", "trace_id": "T123", "only_criteria": ["MKT-2"]})
    run_dir = Path(final["report_path"]).parent
    recs = [json.loads(l) for l in (run_dir / "decisions.jsonl").read_text().splitlines()]
    assert {r["node"] for r in recs} >= {"plan_tasks", "balance_check", "quality_eval"}
    assert all(r["trace_id"] == "T123" and r["run_id"] == "c" for r in recs)
    assert (run_dir / "plans" / "r0.json").exists()
    assert final["node_status"]["quality_eval"] == "done q0"


def test_step_limit_forces_termination(monkeypatch):
    rt = guards.runtime()
    monkeypatch.setattr(guards, "runtime", lambda: {**rt, "orchestrator": {**rt["orchestrator"], "max_control_steps": 4}})
    always = [QualityIssue(axis="neutrality", source="rule", detail="d", quote="q")]
    monkeypatch.setattr(evaluate, "check_neutrality", lambda state: always)
    final = build_graph().invoke({"run_id": "s", "only_criteria": ["MKT-2"]})
    # plan(1) balance(2) quality(3, 재작성) quality(4) → 상한 도달로 give_up (quality.max_rounds=2보다 먼저)
    assert final["step_count"] == 4 and final["eval_result"].action == "give_up"
    assert "step 상한" in final["eval_result"].reason


def test_parallel_worker_errors_merge_into_last_error(monkeypatch):
    def broken_rag(task, channel=None, queries=None):
        raise RuntimeError(f"{channel} down")

    monkeypatch.setattr(workers_mod, "collect_evidence", broken_rag)
    final = build_graph().invoke({"run_id": "e", "only_criteria": ["DOM-4"]})
    assert "down" in final["last_error"]                    # 병렬 Worker 4개가 동시에 써도 충돌 없음
    assert final["report_path"]


def test_resume_from_checkpoint_after_crash(tmp_path, monkeypatch):
    real = main_mod.apply_rules
    calls = {"n": 0}

    def crash_once(state):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("process killed")
        return real(state)

    monkeypatch.setattr(main_mod, "apply_rules", crash_once)
    cfg = langgraph_config("rz", "T", 200)
    with SqliteSaver.from_conn_string(str(tmp_path / "cp.sqlite")) as saver:
        graph = build_graph(checkpointer=saver)
        with pytest.raises(ConnectionError):
            graph.invoke({"run_id": "rz", "trace_id": "T", "only_criteria": ["MKT-2"]}, cfg)
        snap = graph.get_state(cfg)
        assert snap.next == ("apply_rules",)                 # 중단 지점이 체크포인트에 남음
        done_before = len(snap.values["worker_outcomes"])

        final = graph.invoke(None, cfg)                      # 재개
    assert final["eval_result"].action == "pass"
    assert len(final["worker_outcomes"]) == done_before      # 수집 Worker는 다시 돌지 않음
