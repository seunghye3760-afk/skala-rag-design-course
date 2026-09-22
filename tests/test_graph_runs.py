"""뼈대가 끝까지 도는지 확인 (가짜 데이터). 항상 통과해야 함."""
from pathlib import Path

from kv_eval.graph.main import build_graph


def test_graph_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("KV_OUTPUT_DIR", str(tmp_path))
    final = build_graph().invoke({"run_id": "t", "only_criteria": ["TRL-1", "MKT-2", "DOM-4"]})
    assert len(final["final_results"]) == 6
    heads = [l for l in Path(final["report_path"]).read_text(encoding="utf-8").splitlines() if l.startswith("## ")]
    assert heads[0] == "## SUMMARY" and heads[-1] == "## REFERENCE"
