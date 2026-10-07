"""Orchestrator-Workers 실행 스크립트 (Agent 과제). app.py는 RAG 실습용으로 그대로 둔다.

  uv run python app_agent.py                         # 전체 (18개 항목 × 기술 2개)
  uv run python app_agent.py --criteria TRL-1,DOM-4  # 작게 돌려보기
  uv run python app_agent.py --thread <run_id>       # 같은 thread_id로 재개 (체크포인터가 영속일 때만 의미 있음)

LangSmith: .env 에 LANGSMITH_TRACING=true, LANGSMITH_API_KEY, LANGSMITH_PROJECT 를 넣으면 run_name=run_id 로 기록된다.
"""
import argparse
import json
import sys
import traceback
from datetime import datetime

from kv_eval.config import output_root
from kv_eval.orchestrator import build_orchestrator_graph, run_config


def _checkpointer(kind: str):
    if kind == "sqlite":
        try:
            import sqlite3

            from langgraph.checkpoint.sqlite import SqliteSaver  # 선택 의존성 (langgraph-checkpoint-sqlite)
            db = output_root() / "checkpoints.sqlite"
            db.parent.mkdir(parents=True, exist_ok=True)
            return SqliteSaver(sqlite3.connect(str(db), check_same_thread=False))
        except ImportError:
            print("langgraph-checkpoint-sqlite 미설치 → MemorySaver 사용 (재개는 같은 프로세스 안에서만)")
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--criteria", help="쉼표로 구분한 항목 ID (예: TRL-1,MKT-2,DOM-4)")
    p.add_argument("--thread", help="재개할 thread_id (= 이전 run_id)")
    p.add_argument("--checkpointer", choices=["memory", "sqlite"], default="memory")
    args = p.parse_args()
    run_id = args.thread or datetime.now().strftime("%Y%m%d-%H%M%S")
    init = {"run_id": run_id}
    if args.criteria:
        init["only_criteria"] = [c.strip() for c in args.criteria.split(",")]
    graph = build_orchestrator_graph(_checkpointer(args.checkpointer))
    cfg = run_config(run_id, thread_id=args.thread)
    try:
        final = graph.invoke(init, config=cfg)
    except Exception as e:  # noqa: BLE001 — FAILED 상태를 파일로 남기고 비정상 종료 코드
        run_dir = output_root() / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "report_meta.json").write_text(json.dumps(
            {"run_id": run_id, "status": "FAILED", "error": f"{type(e).__name__}: {e}",
             "trace": traceback.format_exc(limit=5)}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"FAILED: {type(e).__name__}: {e}")
        return 1

    print("=" * 60)
    print(f"run_id       : {run_id}")
    for f in final.get("fanout_log", []):
        print(f"fan-out      : {f['phase']} round {f['round']} [{f.get('mode', '')}] → {f['count']}개 {f.get('channels', '')}")
    v = final.get("quality_verdict")
    print(f"retry_round  : {final.get('retry_round', 0)}   quality_round: {final.get('quality_round', 0)}")
    if v:
        print(f"quality      : rules {v.rule_pass} judge {v.judge_scores} passed={v.passed} failed={v.failed_items}")
    excluded = [t.task_id for t in final.get("task_plan", []) if t.status == "excluded"]
    print(f"status       : {final.get('status')}   excluded {len(excluded)}   errors {len(final.get('errors', []))}   "
          f"info_gaps {len(final.get('info_gaps', []))}   steps {final.get('step_count')}")
    print(f"report       : {final['report_path']}  (PDF {final.get('report_pages')}쪽)")
    print(f"decision_log : {output_root() / run_id / 'decision_log.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
