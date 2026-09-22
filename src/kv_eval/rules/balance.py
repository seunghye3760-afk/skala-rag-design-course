"""균형 점검 (설계서 D-9 (2)). 코드로만 판정한다 (LLM 사용 금지).

판정 조건은 설계서 표 그대로 4가지 유형으로 묶는다.
- 근거 공백 (kind=research): 관련 근거 0건 / 1건뿐
- 편향 (kind=research): 긍정·비판 한쪽만 있음 / 최고 등급이 D뿐 / 단일 출처
- 조건 누락 (kind=research): 수치 claim에 모델·문맥 길이·장비 조건이 없음
- 채점 형식 오류 (kind=rescore): 근거가 있는데 NA / 근거 없이 점수 / claim·rationale의 수치가
  인용한 근거의 excerpt에 없음(문자열 대조) / 바로 위 점수를 주지 않은 이유 누락 /
  상반된 B등급 이상 근거가 있는데 intra_conflict=false

숫자 대조·다음 등급 언급은 문자열 규칙이라 완벽하지 않다(예: "3배" vs "300%"는 다른 문자열로
본다). 오탐이 나오면 항목만 다시 채점하므로(kind=rescore) 비용은 크지 않고, 놓치는 것보다
과하게 잡는 쪽이 낫다고 보고 이렇게 정했다.
"""
from __future__ import annotations

import json
import re

from ..config import criteria_list, runtime
from ..graph.reducers import evidence_for, latest_results
from ..graph.state import MainState
from ..graph.task_schema import CriterionResult, Evidence, RetryTarget, cell_key

_NUM_RE = re.compile(r"\d[\d,.]*")
_SCORE_MENTION_RE = re.compile(r"\d+\s*점")     # "3점"/"4점" 같은 점수 언급은 근거 수치가 아니다
_EVIDENCE_ID_RE = re.compile(r"[a-z_]+-(?:TRL|MKT|STK|DOM)-\d+-r\d+-\d+")   # 인용 id의 숫자는 수치가 아니다
# (끝에 \b를 두면 'r0-34에서'처럼 id 뒤에 한글이 붙을 때 경계가 성립하지 않아 매칭이 안 된다)


def _numbers(text: str) -> set[str]:
    text = _EVIDENCE_ID_RE.sub("", _SCORE_MENTION_RE.sub("", text))
    return {n.strip(".,") for n in _NUM_RE.findall(text) if n.strip(".,")}


def _covered(claimed: set[str], allowed: set[str]) -> bool:
    """포맷 변형('3,547' vs '3,547.4 million')을 허용하는 수치 대조.
    콤마를 지운 뒤 같거나, 2자리 이상이면 한쪽이 다른 쪽의 접두어여도 인정한다."""
    an = {a.replace(",", "") for a in allowed}
    for c in claimed:
        cn = c.replace(",", "")
        if cn in an:
            continue
        if len(cn) >= 2 and any(len(a) >= 2 and (a.startswith(cn) or cn.startswith(a)) for a in an):
            continue
        return False
    return True


def _worst_grade_only(ev: list[Evidence]) -> bool:
    """모든 근거의 등급이 최하(D)뿐인가."""
    return bool(ev) and all(e.evidence_grade == "D" for e in ev)


def _single_source(ev: list[Evidence]) -> bool:
    """근거가 2건 이상인데 출처(제목+발행 주체)가 전부 같은가."""
    return len(ev) > 1 and len({(e.source_title, e.publisher) for e in ev}) == 1


def _conditions_missing(ev: list[Evidence]) -> bool:
    """수치가 있는 claim인데 조건(모델·문맥 길이·장비 등)이 비어 있는 근거가 있는가."""
    return any(_NUM_RE.search(e.claim) and not (e.conditions or "").strip() for e in ev)


def _unsupported_numbers(r: CriterionResult, ev_by_id: dict[str, Evidence], crit: dict) -> bool:
    """rationale의 수치가 인용한 근거(claim·excerpt·날짜)에도, 루브릭 기준에도 없는가.

    - claim의 수치는 검사하지 않는다: claim은 수집 단계에서 조건(모델·문맥 길이·장비)을
      요약해 넣도록 설계돼 있어(설계서 D-4) 한두 문장인 excerpt에 그 수치가 다 들어있지 않다.
    - 루브릭 기준 수치(예: CAGR 20%, 3곳 이상)와 인용 근거의 날짜는 rationale이 정당하게
      언급한다('4점 기준 미달' 서술 등). 채점자가 새로 만든 수치만 잡는다."""
    claimed = _numbers(r.rationale)
    if not claimed:
        return False
    allowed = [json.dumps(crit.get("rubric", {}), ensure_ascii=False), crit.get("question", "")]
    for b in r.evidence:
        allowed += [b.claim, b.date or ""]
        e = ev_by_id.get(b.evidence_id)
        if e:
            allowed += [e.excerpt, e.published_at or ""]
    return not _covered(claimed, _numbers(" ".join(allowed)))


def _next_score_reason_missing(r: CriterionResult) -> bool:
    """점수가 만점(5) 미만인데 rationale에 바로 위 점수를 주지 않은 이유(다음 점수 숫자)가 없는가."""
    return isinstance(r.score, int) and r.score < 5 and str(r.score + 1) not in r.rationale


def _intra_conflict_missing(r: CriterionResult, ev_by_id: dict[str, Evidence]) -> bool:
    """인용한 근거 중 B등급 이상끼리 pro/con이 갈리는데 intra_conflict=false로 남아 있는가."""
    if r.intra_conflict:
        return False
    strong = [ev_by_id[b.evidence_id] for b in r.evidence
              if b.evidence_id in ev_by_id and ev_by_id[b.evidence_id].evidence_grade in ("A", "B")]
    return any(e.stance == "pro" for e in strong) and any(e.stance == "con" for e in strong)


def find_issues(state: MainState) -> list[RetryTarget]:
    latest = latest_results(state.get("criterion_results", []))
    pool = state.get("evidence_pool", [])
    issues: list[RetryTarget] = []
    for t in state["technologies"]:
        for c in criteria_list(state["rubrics"], state.get("only_criteria")):
            tid, cid = t["tech_id"], c["id"]
            r = latest.get(cell_key(tid, cid))
            ev = evidence_for(pool, tid, cid)
            ev_by_id = {e.evidence_id: e for e in ev}
            pro = [e for e in ev if e.stance == "pro"]
            con = [e for e in ev if e.stance == "con"]
            target = dict(tech_id=tid, criterion_id=cid)

            if not ev:
                issues.append(RetryTarget(**target, kind="research", reason="근거 공백: 관련 근거 0건",
                                          hint="다른 검색명·근거 유형으로 재검색"))
            elif len(ev) == 1:
                issues.append(RetryTarget(**target, kind="research", reason="근거 공백: 관련 근거 1건뿐"))
            elif not pro or not con or _worst_grade_only(ev) or _single_source(ev):
                reasons = []
                if not pro or not con:
                    reasons.append(f"{'비판' if not con else '긍정'} 근거 없음")
                if _worst_grade_only(ev):
                    reasons.append("최고 등급이 D뿐")
                if _single_source(ev):
                    reasons.append("단일 출처")
                hint = "비판 근거 부족" if not con else ("긍정 근거 부족" if not pro else "원문 확인으로 재분류 시도")
                issues.append(RetryTarget(**target, kind="research", reason=f"편향: {', '.join(reasons)}",
                                          hint=hint))
            elif _conditions_missing(ev):
                issues.append(RetryTarget(**target, kind="research",
                                          reason="조건 누락: 수치 claim에 모델·문맥 길이·장비 조건 없음",
                                          hint="원문 재확인 후 조건 보완"))
            elif r is None:
                issues.append(RetryTarget(**target, kind="rescore", reason="채점 결과 없음"))
            elif r.score == "NA":
                issues.append(RetryTarget(**target, kind="rescore", reason="형식 오류: 근거가 있는데 NA"))
            elif not r.evidence:
                issues.append(RetryTarget(**target, kind="rescore", reason="형식 오류: 근거 인용 없이 점수"))
            elif _unsupported_numbers(r, ev_by_id, c):
                issues.append(RetryTarget(**target, kind="rescore",
                                          reason="형식 오류: claim·rationale의 수치가 인용 근거 excerpt에 없음"))
            elif _next_score_reason_missing(r):
                issues.append(RetryTarget(**target, kind="rescore",
                                          reason="형식 오류: 바로 위 점수를 주지 않은 이유 누락"))
            elif _intra_conflict_missing(r, ev_by_id):
                issues.append(RetryTarget(**target, kind="rescore",
                                          reason="형식 오류: 상반된 B등급 이상 근거인데 intra_conflict=false"))
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
