"""보고서 조립 (설계서 E). SUMMARY가 맨 앞, REFERENCE(실제 인용한 자료만)가 맨 끝. 담당 4."""
from __future__ import annotations

import json

from ..agents.synthesis import synthesize
from ..config import output_root
from ..graph.state import MainState


def _dump(path, items):
    path.write_text(json.dumps([i.model_dump() if hasattr(i, "model_dump") else i for i in items],
                               ensure_ascii=False, indent=2), encoding="utf-8")


def synthesize_report(state: MainState) -> dict:
    upd = synthesize(state)
    run_dir = output_root() / state.get("run_id", "run")
    run_dir.mkdir(parents=True, exist_ok=True)
    _dump(run_dir / "evidence.json", state.get("evidence_pool", []))
    _dump(run_dir / "scores.json", state.get("final_results", []))
    _dump(run_dir / "search_log.json", state.get("search_log", []))
    _dump(run_dir / "info_gaps.json", state.get("info_gaps", []))

    cited = {b.evidence_id for r in state.get("final_results", []) for b in r.evidence}
    refs = [e for e in state.get("evidence_pool", []) if e.evidence_id in cited]
    names = {t["tech_id"]: t["name"] for t in state["technologies"]}
    L = ["# KV cache 최적화 기술 다관점 평가 보고서 — TurboQuant · CXL-PNM", "", "## SUMMARY", ""]
    for tr in state.get("trl_results", []):
        L.append(f"- {names[tr.tech_id]}: TRL {tr.trl_level} (공개 정보 기반 추정, 하한, 확신도 {tr.trl_confidence})"
                 f" / 기반 부품 성숙도 {tr.component_maturity}")
    L += ["", "## 4~5. 기술별 평가 결과 (합산·순위 없음)", "",
          "| 기술 | 항목 | 점수 | 원점수 | 확신도 | 상한 적용 | 근거 |", "|---|---|---|---|---|---|---|"]
    for r in sorted(state.get("final_results", []), key=lambda r: (r.tech_id, r.criterion_id)):
        L.append(f"| {names[r.tech_id]} | {r.criterion_id} | {r.score} | {r.raw_score} | {r.confidence} | "
                 f"{r.cap_applied or ''} | {', '.join(b.evidence_id for b in r.evidence)} |")
    L += ["", "## 관점 간 상충 후보와 정보 공백", ""]
    for c in upd["conflicts"]:
        L.append(f"- {names[c.tech_id]} {c.comparison_id} ({c.status}, 차이 {c.score_gap}): {c.interpretation}")
    L += ["", "## REFERENCE", "", "> 보고서 본문에서 실제로 인용한 자료만 수록", ""]
    for e in refs:
        L.append(f"- [{e.evidence_grade}] {e.source_title} — {e.publisher}, {e.published_at or 'n.d.'}, "
                 f"{e.source_url or e.locator.doc_id} (확인일 {e.accessed_at})")
    path = run_dir / "report.md"
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    # TODO(담당 4): report.md → report.pdf 변환 (한글 폰트 포함), 설계서 E 목차 전체 반영
    return {**upd, "report_path": str(path)}
