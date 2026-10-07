"""한 번 실행으로 보고서 생성 (Orchestrator-Workers + 품질 평가 루프).

  uv run python app.py                         # 전체 (18개 항목 × 기술 2개)
  uv run python app.py --criteria TRL-1,DOM-4  # 작게 돌려보기
  uv run python app.py --resume 20261007-1530  # 중단된 실행을 마지막 체크포인트부터 재개

LangSmith 추적: .env에 LANGSMITH_TRACING=true, LANGSMITH_API_KEY, LANGSMITH_PROJECT를 넣으면
run metadata(trace_id·run_id)가 붙은 트레이스가 남는다. 결정 로그는 outputs/runs/<run_id>/decisions.jsonl.
"""
import argparse
import uuid
from datetime import datetime
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from kv_eval.config import output_root, runtime
from kv_eval.graph.main import build_graph
from kv_eval.obs import langgraph_config


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--criteria", help="쉼표로 구분한 항목 ID (예: TRL-1,MKT-2,DOM-4)")
    p.add_argument("--resume", metavar="RUN_ID", help="중단된 run_id를 체크포인트에서 재개")
    args = p.parse_args()

    output_root().mkdir(parents=True, exist_ok=True)
    db = output_root() / "checkpoints.sqlite"
    limit = runtime()["orchestrator"]["recursion_limit"]

    with SqliteSaver.from_conn_string(str(db)) as saver:
        graph = build_graph(checkpointer=saver)
        if args.resume:
            run_id = args.resume
            snap = graph.get_state({"configurable": {"thread_id": run_id}})
            if not snap.values:
                raise SystemExit(f"체크포인트 없음: {run_id}")
            trace_id = snap.values.get("trace_id", run_id)
            print(f"재개: {run_id} · 다음 노드 {list(snap.next)} · 상태 {snap.values.get('node_status')}"
                  f" · 마지막 에러 {snap.values.get('last_error')}")
            final = graph.invoke(None, langgraph_config(run_id, trace_id, limit))
        else:
            run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
            trace_id = uuid.uuid4().hex
            init = {"run_id": run_id, "trace_id": trace_id}
            if args.criteria:
                init["only_criteria"] = [c.strip() for c in args.criteria.split(",")]
            final = graph.invoke(init, langgraph_config(run_id, trace_id, limit))

    ev = final.get("eval_result")
    print(f"run_id: {run_id}  trace_id: {trace_id}")
    print(f"수집 라운드: {final.get('retry_round', 0)}  품질 평가: {final.get('quality_round', 0)}회  "
          f"조정 step: {final.get('step_count', 0)}  정보 공백: {len(final.get('info_gaps', []))}개")
    if ev:
        print(f"품질 판정: {ev.action} — {ev.reason}")
    print(f"보고서: {final['report_path']}")
    pdf = Path(final["report_path"]).with_suffix(".pdf")   # report.py가 만들면 report.md 옆에 있다
    if pdf.exists():
        print(f"PDF: {pdf}")


if __name__ == "__main__":
    main()
