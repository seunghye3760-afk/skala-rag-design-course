"""균형 점검 (설계서 D-9 (2)). 코드로만 판정한다 (LLM 사용 금지)."""
from __future__ import annotations

from ..config import criteria_list, runtime
from ..graph.reducers import evidence_for, latest_results
from ..graph.state import MainState
from ..graph.task_schema import RetryTarget, cell_key


def find_issues(state: MainState) -> list[RetryTarget]:
    latest = latest_results(state.get("criterion_results", []))
    pool = state.get("evidence_pool", [])
    issues: list[RetryTarget] = []
    for t in state["technologies"]:
        for c in criteria_list(state["rubrics"], state.get("only_criteria")):
            tid, cid = t["tech_id"], c["id"]
            r = latest.get(cell_key(tid, cid))
            ev = evidence_for(pool, tid, cid)
            pro = [e for e in ev if e.stance == "pro"]
            con = [e for e in ev if e.stance == "con"]
            target = dict(tech_id=tid, criterion_id=cid)
            if not ev:
                issues.append(RetryTarget(**target, kind="research", reason="근거 공백: 관련 근거 0건",
                                          hint="다른 검색명·근거 유형으로 재검색"))
            elif len(ev) == 1:
                issues.append(RetryTarget(**target, kind="research", reason="근거 공백: 관련 근거 1건뿐"))
            elif not pro or not con:
                side = "비판" if not con else "긍정"
                issues.append(RetryTarget(**target, kind="research", reason=f"편향: {side} 근거 없음",
                                          hint=f"{side} 근거 부족"))
            elif r is None:
                issues.append(RetryTarget(**target, kind="rescore", reason="채점 결과 없음"))
            elif r.score == "NA":
                issues.append(RetryTarget(**target, kind="rescore", reason="형식 오류: 근거가 있는데 NA"))
            elif not r.evidence:
                issues.append(RetryTarget(**target, kind="rescore", reason="형식 오류: 근거 인용 없이 점수"))
            # TODO(담당 4): 설계서 D-9 (2)의 나머지 조건
            #  - 최고 등급이 D뿐 / 단일 출처 / 수치 claim에 조건 누락
            #  - claim·rationale의 수치가 excerpt에 없음 (문자열 대조)
            #  - 바로 위 점수를 주지 않은 이유 누락 / 상반된 B등급 이상 근거인데 intra_conflict=False
    return issues


def balance_check(state: MainState) -> dict:
    issues = find_issues(state)
    rnd = state.get("retry_round", 0)
    if issues and rnd < runtime()["retry"]["max_rounds"]:
        return {"balance_issues": issues, "retry_targets": issues, "retry_round": rnd + 1}
    return {"balance_issues": issues, "retry_targets": [], "info_gaps": issues}


def route_after_balance(state: MainState) -> str:
    targets = state.get("retry_targets") or []
    if any(x.kind == "research" for x in targets):
        return "dispatch_collect"
    if targets:
        return "score_dispatch"        # 형식 오류만 → 근거 수집 건너뛰고 재채점
    return "apply_rules"
