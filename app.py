"""한 번 실행으로 보고서 초안 생성.

  uv run python app.py                         # 전체 (18개 항목 × 기술 2개)
  uv run python app.py --criteria TRL-1,DOM-4  # 작게 돌려보기
"""
import argparse
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from langgraph.checkpoint.sqlite import SqliteSaver

from kv_eval.config import output_root
from kv_eval.graph.checkpoint import checkpoint_serializer
from kv_eval.graph.main import build_graph


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--criteria", help="쉼표로 구분한 항목 ID (예: TRL-1,MKT-2,DOM-4)")
    args = p.parse_args()
    run_id = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d-%H%M%S")
    init = {"run_id": run_id}
    if args.criteria:
        init["only_criteria"] = [c.strip() for c in args.criteria.split(",")]
    checkpoint_dir = output_root() / run_id
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(checkpoint_dir / "checkpoints.sqlite", check_same_thread=False) as conn:
        final = build_graph(SqliteSaver(conn, serde=checkpoint_serializer())).invoke(
            init, {"configurable": {"thread_id": run_id}, "tags": ["multi-agent", run_id]})
    print(f"run_id: {run_id}")
    print(f"재시도 라운드: {final.get('retry_round', 0)}  정보 공백: {len(final.get('info_gaps', []))}개")
    print(f"Supervisor 단계: {final.get('supervisor_step', 0)}  상태: {final.get('final_status')}")
    print(f"보고서: {final['report_path']}")
    pdf = Path(final["report_path"]).with_suffix(".pdf")   # report.py가 만들면 report.md 옆에 있다
    if pdf.exists():
        print(f"PDF: {pdf}")


if __name__ == "__main__":
    main()
