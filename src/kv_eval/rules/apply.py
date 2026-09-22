"""apply_rules 노드: 상한·하한 → TRL 산출 → 상충 후보 추출 (설계서 D-9 (3))."""
from __future__ import annotations

from .. import progress
from ..graph.reducers import evidence_for, latest_results
from ..graph.state import MainState
from ..graph.task_schema import cell_key
from .caps import apply_caps
from .conflicts import conflict_candidates
from .trl_gate import compute_trl


def apply_rules(state: MainState) -> dict:
    progress.step("apply_rules", "상한·하한 적용, TRL 산출, 상충 후보 추출 시작")
    pool = state.get("evidence_pool", [])
    latest = latest_results(state.get("criterion_results", []))
    finals = {k: apply_caps(r, evidence_for(pool, r.tech_id, r.criterion_id)) for k, r in latest.items()}
    trl_ids = state["rubrics"]["agents"]["trl"]["criteria"]
    trl_results, conflicts = [], []
    for t in state["technologies"]:
        tid = t["tech_id"]
        scores = {cid: finals[cell_key(tid, cid)].score for cid in trl_ids if cell_key(tid, cid) in finals}
        if scores:
            ev = [e for cid in scores for e in evidence_for(pool, tid, cid)]
            trl_results.append(compute_trl(tid, scores, ev))
        conflicts += conflict_candidates(tid, finals, state["rubrics"]["comparison_pairs"]["pairs"])
    progress.step("apply_rules", f"완료 — 최종 결과 {len(finals)}건, TRL {len(trl_results)}건, "
                  f"상충 후보 {len(conflicts)}건")
    return {"final_results": list(finals.values()), "trl_results": trl_results, "conflicts": conflicts}
