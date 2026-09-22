"""한 번 실행으로 보고서 초안 생성.

  uv run python app.py                         # 전체 (18개 항목 × 기술 2개)
  uv run python app.py --criteria TRL-1,DOM-4  # 작게 돌려보기
"""
import argparse
from datetime import datetime

from kv_eval.graph.main import build_graph


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--criteria", help="쉼표로 구분한 항목 ID (예: TRL-1,MKT-2,DOM-4)")
    args = p.parse_args()
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    init = {"run_id": run_id}
    if args.criteria:
        init["only_criteria"] = [c.strip() for c in args.criteria.split(",")]
    final = build_graph().invoke(init)
    print(f"run_id: {run_id}")
    print(f"재시도 라운드: {final.get('retry_round', 0)}  정보 공백: {len(final.get('info_gaps', []))}개")
    print(f"보고서: {final['report_path']}")


if __name__ == "__main__":
    main()
