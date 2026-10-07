"""docs/main_graph_orchestrator.mmd 를 그래프 정의에서 생성한다 (그림과 코드가 어긋나지 않게).
  uv run python scripts/draw_orchestrator_graph.py
PNG는 mermaid CLI(mmdc -i docs/main_graph_orchestrator.mmd -o docs/main_graph_orchestrator.png)나 mermaid.live로 렌더링.
"""
from pathlib import Path

from kv_eval.orchestrator import build_orchestrator_graph

out = Path(__file__).resolve().parents[1] / "docs" / "main_graph_orchestrator.mmd"
out.write_text(build_orchestrator_graph().get_graph().draw_mermaid(), encoding="utf-8")
print(f"wrote {out}")
