"""CLI가 보고서 gate 실패를 traceback 없이 설명하는지 검증한다."""
import sys

import app
from kv_eval.graph.task_schema import ReportQualityResult


class _Graph:
    def __init__(self, result):
        self.result = result

    def invoke(self, init):
        return self.result


def test_main_reports_failed_gate_without_report_path(monkeypatch, capsys):
    result = {
        "retry_round": 2,
        "info_gaps": [],
        "report_quality": ReportQualityResult(
            passed=False,
            page_count=12,
            issues=["PDF가 12페이지"],
        ),
        "report_failure_path": "/tmp/report.failed.md",
    }
    monkeypatch.setattr(app, "build_graph", lambda: _Graph(result))
    monkeypatch.setattr(sys, "argv", ["app.py"])

    exit_code = app.main()

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "PDF가 12페이지" in output
    assert "/tmp/report.failed.md" in output
    assert "보고서:" not in output
