"""품질 평가 1안 — 규칙 검사 (결정적, LLM 없음).

| 축 | 규칙 |
|---|---|
| groundedness | 점수가 있는 항목은 근거 인용 ≥1 / 인용 id가 evidence_pool에 실제로 존재 / REFERENCE 비어 있지 않음 /
|              | 종합 서술(LLM)의 수치가 근거 claim·excerpt·조건 또는 TRL 값에 있음 |
| neutrality   | 종합 서술에 우열·추천·순위·합산 표현이 없음 (금지어 사전) |
| bias         | 기술별 인용 출처: 발행 주체 수 ≥ N, 단일 발행 주체 비중 ≤ 상한, 비판(con) 근거 비중 ≥ 하한 /
|              | 두 기술의 인용 근거 수 비율 ≥ 하한 (같은 강도로 조사했는가)
|              | (점수 있는 셀이 min_cells_for_distribution 미만이면 분포 판정 생략 — 표본 부족) |
| coverage     | 기술마다 4관점(TRL·시장성·이해관계자·도메인) 각각 점수 있는 항목 ≥1 / SUMMARY·REFERENCE 목차 존재 |

셀(기술 × 항목)로 특정되는 문제는 tech_id·criterion_id를 채워 재계획 대상이 되게 한다.
"""
from __future__ import annotations

import re
from collections import Counter

from ..graph.reducers import latest_results
from ..graph.state import MainState
from ..graph.task_schema import Evidence
from ..rules.balance import _covered, _numbers
from .schema import QualityIssue

PERSPECTIVES = ("trl", "market", "stakeholder", "domain")

# 우열·추천·순위·합산 표현. 근거 인용문이 아니라 종합 서술(LLM 생성)에만 적용한다.
_NEUTRALITY_RE = re.compile(
    r"(더|보다|가장)\s*(우수|우월|낫|유망|뛰어나|효과적)|우위에|열등|압도|승자|패자|"
    r"추천(한다|합니다|된다)|권장(한다|합니다|된다)|선택해야|도입해야|채택해야|"
    r"총점|합산\s*점수|순위|[1-9]\s*위\b|"
    r"\b(better than|superior|outperforms?|recommend(ed)?|the winner)\b", re.I)
# 항목 ID·시나리오 번호의 숫자는 수치 주장이 아니다
_ID_RE = re.compile(r"(TRL|MKT|STK|DOM)-\d+|\bP\d+\b|시나리오\s*\d|TRL\s*\d")
_SENT_SPLIT = re.compile(r"(?<=[.!?다])\s+")


def narrative_texts(state: MainState) -> list[str]:
    """LLM이 쓴 종합 서술 문장들 (final_assessment 전체 + 상충 해석)."""
    out: list[str] = []

    def walk(v):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(state.get("final_assessment", {}))
    out += [c.interpretation for c in state.get("conflicts", []) if c.interpretation]
    return [t for t in out if t and t.strip()]


def _cited(state: MainState) -> tuple[dict, dict[str, list[Evidence]]]:
    """(최신 채점 결과, 기술별 인용 근거)"""
    finals = {f"{r.tech_id}:{r.criterion_id}": r for r in state.get("final_results", [])} \
        or latest_results(state.get("criterion_results", []))
    pool = {e.evidence_id: e for e in state.get("evidence_pool", [])}
    by_tech: dict[str, list[Evidence]] = {t["tech_id"]: [] for t in state["technologies"]}
    for r in finals.values():
        by_tech[r.tech_id] += [pool[b.evidence_id] for b in r.evidence if b.evidence_id in pool]
    return finals, by_tech


def check_groundedness(state: MainState, report_text: str) -> list[QualityIssue]:
    finals, _ = _cited(state)
    pool = {e.evidence_id: e for e in state.get("evidence_pool", [])}
    issues: list[QualityIssue] = []
    for r in finals.values():
        cell = dict(tech_id=r.tech_id, criterion_id=r.criterion_id)
        if r.score != "NA" and not r.evidence:
            issues.append(QualityIssue(axis="groundedness", source="rule", **cell,
                                       detail="점수가 있는데 인용 근거 없음"))
        if missing := [b.evidence_id for b in r.evidence if b.evidence_id not in pool]:
            issues.append(QualityIssue(axis="groundedness", source="rule", **cell,
                                       detail=f"evidence_pool에 없는 근거 인용: {missing[:3]}"))

    ref = report_text.split("## REFERENCE", 1)[-1] if "## REFERENCE" in report_text else ""
    if any(r.evidence for r in finals.values()) and not re.search(r"^- ", ref, re.M):
        issues.append(QualityIssue(axis="groundedness", source="rule", detail="REFERENCE가 비어 있음"))

    allowed: set[str] = set()
    for e in pool.values():
        allowed |= _numbers(" ".join(filter(None, [e.claim, e.excerpt, e.conditions])))
    for tr in state.get("trl_results", []):
        allowed |= _numbers(f"{tr.trl_level} {tr.component_maturity}")
    for text in narrative_texts(state):
        for sent in _SENT_SPLIT.split(text):
            nums = {n for n in _numbers(_ID_RE.sub("", sent)) if len(n.replace(",", "")) >= 2 or "." in n}
            if nums and not _covered(nums, allowed):
                issues.append(QualityIssue(axis="groundedness", source="rule", quote=sent.strip()[:200],
                                           detail=f"근거에 없는 수치 {sorted(nums)[:5]}"))
    return issues


def check_neutrality(state: MainState) -> list[QualityIssue]:
    issues = []
    for text in narrative_texts(state):
        for sent in _SENT_SPLIT.split(text):
            if m := _NEUTRALITY_RE.search(sent):
                issues.append(QualityIssue(axis="neutrality", source="rule", quote=sent.strip()[:200],
                                           detail=f"우열·추천·순위 표현: '{m.group(0)}'"))
    return issues


def check_bias(state: MainState, cfg: dict) -> list[QualityIssue]:
    finals, by_tech = _cited(state)
    pool = {e.evidence_id: e for e in state.get("evidence_pool", [])}
    issues: list[QualityIssue] = []

    def weak_cells(tech_id: str, pred) -> list[QualityIssue]:
        """기술 단위 편향을 고칠 셀: 인용 근거가 pred를 만족하는 셀"""
        out = []
        for r in finals.values():
            ev = [pool[b.evidence_id] for b in r.evidence if b.evidence_id in pool]
            if r.tech_id == tech_id and r.score != "NA" and pred(ev):
                out.append((r.tech_id, r.criterion_id))
        return out

    scored = Counter(r.tech_id for r in finals.values() if r.score != "NA")
    for tid, ev in by_tech.items():
        if not ev or scored[tid] < cfg["min_cells_for_distribution"]:
            continue
        pubs = Counter(e.publisher for e in ev)
        top, top_n = pubs.most_common(1)[0]
        con_share = sum(e.stance == "con" for e in ev) / len(ev)
        problems = []
        if len(pubs) < cfg["min_publishers_per_tech"]:
            problems.append(f"발행 주체 {len(pubs)}곳뿐")
        if top_n / len(ev) > cfg["max_publisher_share"]:
            problems.append(f"'{top}' 비중 {top_n / len(ev):.0%}")
        if con_share < cfg["min_con_share"]:
            problems.append(f"비판 근거 비중 {con_share:.0%}")
        if not problems:
            continue
        cells = weak_cells(tid, lambda e: len({x.publisher for x in e}) <= 1 or all(x.stance == "pro" for x in e))
        detail = f"{tid} 인용 근거 편중: {', '.join(problems)}"
        issues += [QualityIssue(axis="bias", source="rule", tech_id=t, criterion_id=c, detail=detail)
                   for t, c in cells] or [QualityIssue(axis="bias", source="rule", detail=detail)]

    counts = {t: len(v) for t, v in by_tech.items()}
    if len(counts) == 2 and max(counts.values()) and min(scored.values(), default=0) >= cfg["min_cells_for_distribution"] and \
            min(counts.values()) / max(counts.values()) < cfg["min_cited_ratio_between_techs"]:
        low = min(counts, key=counts.get)
        cells = weak_cells(low, lambda e: len(e) <= 1)
        detail = f"기술 간 인용 근거 수 불균형 {counts}"
        issues += [QualityIssue(axis="bias", source="rule", tech_id=t, criterion_id=c, detail=detail)
                   for t, c in cells] or [QualityIssue(axis="bias", source="rule", detail=detail)]
    return issues


def check_coverage(state: MainState, report_text: str) -> list[QualityIssue]:
    finals, _ = _cited(state)
    issues: list[QualityIssue] = []
    agents_present = {r.agent_type for r in finals.values()}
    if not state.get("only_criteria") and (missing := set(PERSPECTIVES) - agents_present):
        issues.append(QualityIssue(axis="coverage", source="rule", detail=f"관점 누락: {sorted(missing)}"))
    for t in state["technologies"]:
        for p in PERSPECTIVES:
            if p not in agents_present:      # only_criteria로 관점을 뺀 실행은 검사 대상 아님
                continue
            cells = [r for r in finals.values() if r.tech_id == t["tech_id"] and r.agent_type == p]
            if cells and all(r.score == "NA" for r in cells):
                issues += [QualityIssue(axis="coverage", source="rule", tech_id=r.tech_id,
                                        criterion_id=r.criterion_id,
                                        detail=f"{t['tech_id']} {p} 관점 전 항목 NA")
                           for r in cells]
    for head in ("## SUMMARY", "## REFERENCE"):
        if head not in report_text:
            issues.append(QualityIssue(axis="coverage", source="rule", detail=f"필수 목차 누락: {head}"))
    return issues
