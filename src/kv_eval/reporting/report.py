"""보고서 조립 (설계서 E 목차 초안). SUMMARY가 맨 앞, REFERENCE(실제 인용한 자료만)가 맨 끝.
담당 4.

report_path는 항상 report.md를 가리킨다 (tests/test_graph_runs.py가 이 경로를 텍스트로
읽어 "## " 제목 줄을 검사하므로 바꾸면 안 된다). report.pdf는 report.md 옆에 추가로
만들어질 뿐이고, 한글 폰트를 못 찾거나 PDF 조립이 실패해도 report.md는 그대로 만들어진다
(그래프가 마지막 노드에서 죽지 않게 한다 — reporting/pdf.py의 render_pdf 참고).
"""
from __future__ import annotations

import json

import re

from .. import progress
from ..agents.synthesis import synthesize
from ..config import output_root, technologies_config
from ..graph.state import MainState
from .pdf import render_pdf

_GRADE_KO = {"A": "독립 실측/공식 공시", "B": "당사자 실측/발표", "C": "시뮬레이션/추정", "D": "2차 자료"}


def _dump(path, items):
    path.write_text(json.dumps([i.model_dump() if hasattr(i, "model_dump") else i for i in items],
                               ensure_ascii=False, indent=2), encoding="utf-8")


def _names(state: MainState) -> dict:
    return {t["tech_id"]: t["name"] for t in state["technologies"]}


def _short(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _brief_val(state: MainState, tech_id: str, field: str, n: int = 140) -> str:
    """tech_briefs[tech][field]는 실제 실행에서 {"value","status",…} dict, 가짜 모드에서는 문자열."""
    v = (state.get("tech_briefs") or {}).get(tech_id, {}).get(field)
    if isinstance(v, dict):
        status, value = v.get("status") or "미보고", str(v.get("value") or "").strip()
        if not value:
            return f"({status})"
        for junk in ("제공된 청크에 따르면 ", "제공된 청크는 ", "제공된 청크에는 ", "청크는 ", "청크에서 ", "청크에 따르면 "):
            if value.startswith(junk):
                value = value[len(junk):]
        return _short(value, n) + ("" if status == "reported" else f" [{status}]")
    return _short(v, n) if v else "(미보고)"


# ---------- SUMMARY ----------

def _summary(state: MainState, names: dict, assessment: dict) -> list[str]:
    rub = state["rubrics"]
    L = ["## SUMMARY", "", f"핵심 질문: {rub['meta']['core_question']}", ""]
    for tr in state.get("trl_results", []):
        L.append(f"- {names[tr.tech_id]}: TRL {tr.trl_level} (공개 정보 기반 추정, 하한, 확신도 "
                 f"{tr.trl_confidence}) / 기반 부품 성숙도 {tr.component_maturity}")
        a = assessment.get(tr.tech_id)
        if a and a.get("요약"):
            L.append(f"  {a['요약']}")
    cand = [c for c in state.get("conflicts", []) if c.status == "candidate"]
    gap = [c for c in state.get("conflicts", []) if c.status == "info_gap"]
    L += ["", f"주요 상충 지점 후보 {len(cand)}건, 정보 공백으로 판단이 유보된 비교 {len(gap)}건 "
             "(4~5장 참고).", ""]
    return L


# ---------- 1. 개요 / 2. 대상 기술 개요 / 3. 평가 방법 ----------

def _ch1(state: MainState) -> list[str]:
    rub = state["rubrics"]
    dcfg = technologies_config()["domain"]
    return ["## 1. 개요", "",
           "### 1.1 배경: 장문 LLM 추론과 KV cache 병목", "",
           f"평가 대상: {dcfg['name']} ({dcfg['evaluator']}), 주요 워크로드: {dcfg['workload']}.", "",
           "### 1.2 평가 목적과 핵심 질문", "", rub["meta"]["core_question"], "",
           "### 1.3 평가 원칙", "", rub["meta"]["principle"], "",
           "### 1.4 분석 범위와 제외 범위", "",
           f"평가 시나리오: {' / '.join(dcfg['scenarios'])}. "
           f"우선 지표: {', '.join(dcfg['priority_metrics'])}.", ""]


_GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


def _tech_conditions(state: MainState, tech_id: str, cid: str) -> set[str]:
    pool = state.get("evidence_pool", [])
    return {(e.conditions or "").strip() for e in pool if e.tech_id == tech_id and e.criterion_id == cid
           and (e.conditions or "").strip()}


def _representative_condition(state: MainState, tech_id: str, cid: str) -> str:
    """최고 등급 근거의 조건 중 모델·문맥 길이·하드웨어 정보가 가장 많이 담긴 것 하나."""
    pool = [e for e in state.get("evidence_pool", []) if e.tech_id == tech_id and e.criterion_id == cid
            and (e.conditions or "").strip()]
    if not pool:
        return ""
    best = min(_GRADE_ORDER[e.evidence_grade] for e in pool)
    cands = [e.conditions.strip() for e in pool if _GRADE_ORDER[e.evidence_grade] == best]
    keys = ("B", "token", "GPU", "H100", "A100", "ctx", "context", "bit", "batch", "nm", "Xeon", "모델", "문맥")
    return max(cands, key=lambda c: (sum(k in c for k in keys), -abs(len(c) - 70)))


def _ch2(state: MainState, names: dict, assessment: dict | None = None) -> list[str]:
    tcfg = technologies_config()["technologies"]
    by_id = {t["tech_id"]: t for t in tcfg}
    L = ["## 2. 대상 기술 개요", "",
         "아래 내용은 기술 조사 단계(tech_research)가 논문에서 추출한 brief다. '(미보고)'는 논문에 없음, "
         "'[확인 불가]'는 검색된 본문만으로 단정하기 어려움을 뜻한다. 채점 근거로는 쓰지 않는다.", ""]
    brief_rows = [("해결하려는 병목", "q1_해결_병목"), ("효과가 나는 조건", "q2_효과_조건"),
                  ("품질·비용 평가 방식", "q3_품질_비용_평가"), ("평가하지 않은 조건", "q4_미평가_조건")]
    for tid in ("turboquant", "cxl_pnm"):
        t = by_id.get(tid, {})
        L += [f"### 2.{1 if tid == 'turboquant' else 2} {names.get(tid, tid)} — "
             f"{'데이터 표현의 압축' if tid == 'turboquant' else '저장·계산 구조의 재구성'}", "",
             f"분류: {t.get('group', '?')} / {t.get('category', '?')}", ""]
        L += [f"- **{label}**: {_brief_val(state, tid, field, 120)}" for label, field in brief_rows] + [""]
    L += ["### 2.3 기술 구조 비교", "",
         "| 구분 | TurboQuant | CXL-PNM |", "|---|---|---|"]
    for label, field in (("KV cache 구성", "kv_cache_구성"), ("처리 구조", "처리_구조"), ("적용 한계", "적용_한계")):
        L.append(f"| {label} | {_brief_val(state, 'turboquant', field, 160)} | {_brief_val(state, 'cxl_pnm', field, 160)} |")
    comp = (assessment or {}).get("_comparability") or {}
    verdicts: dict[str, tuple[str, str, str, str]] = {}
    for c in state["rubrics"]["criteria"]:
        cid = c["id"]
        a, b = _tech_conditions(state, "turboquant", cid), _tech_conditions(state, "cxl_pnm", cid)
        if not a and not b:
            continue
        row = comp.get(cid)
        if row:
            verdicts[cid] = (row["verdict"], row["turboquant"], row["cxl_pnm"], row["note"])
        else:                                   # 종합 결과에 없으면 기계 규칙 (한쪽 없음 → 정보 부족)
            verdicts[cid] = ("정보 부족" if not a or not b else "조건 차이",
                             _representative_condition(state, "turboquant", cid) or "근거 없음",
                             _representative_condition(state, "cxl_pnm", cid) or "근거 없음",
                             "종합 단계 판정 없음 — 조건 문자열 기준")
    diff = [cid for cid, v in verdicts.items() if v[0] == "조건 차이"]
    exceptions = [(cid, v) for cid, v in verdicts.items() if v[0] != "조건 차이"]
    L += ["", "### 2.4 실험 근거 비교 가능성 — 두 기술의 수치를 같은 표에 놓고 읽어도 되는가", "",
         f"종합 단계(LLM)가 항목별로 두 기술 근거의 실험 조건(모델 규모·문맥 길이·하드웨어·정밀도)을 대조한 결과, "
         f"{len(verdicts)}개 항목 중 {len(diff)}개는 **조건 차이**로 수치를 직접 비교할 수 없다"
         + (f" ({', '.join(diff)})" if diff and len(diff) <= 6 else "")
         + ". 이 보고서가 두 기술의 점수·수치를 나란히 놓지 않는 이유다. 아래는 예외(비교 가능·정보 부족)만 적는다. "
         "항목별 조건 요약과 판정 이유 전문은 부록 C에 있다.", ""]
    if exceptions:
        L += ["| 항목 | 판정 | TurboQuant 조건 | CXL-PNM 조건 | 이유 |", "|---|---|---|---|---|"]
        for cid, (verdict, ta, tb, note) in exceptions:
            L.append(f"| {cid} | {verdict} | {_short(ta, 70)} | {_short(tb, 70)} | {_short(note, 100)} |")
    else:
        L.append("(예외 없음 — 모든 항목이 조건 차이)")
    L.append("")
    return L


def _ch3(state: MainState) -> list[str]:
    rub = state["rubrics"]
    n = len(rub["criteria"])
    by_agent = {ag: ", ".join(c["id"] for c in rub["criteria"] if c["agent"] == ag)
                for ag in ("trl", "market", "stakeholder", "domain")}
    return ["## 3. 평가 방법", "",
           f"**3.1 도메인과 평가 주체** — {rub['meta']['domain']}", "",
           "**3.2 평가 시스템** — LangGraph Orchestrator-Workers (src/kv_eval/orchestrator): LLM 플래너가 항목별 "
           "검색 채널·쿼리·worker 수를 계획 → 근거 수집 worker(논문 FAISS+BM25 하이브리드 + 웹 검색 + 원문 확인) → "
           "관점별 채점 → 균형 점검(코드 규칙, 재시도 ≤ 2라운드) → 상한·TRL·상충 규칙 → 종합 → 보고서 품질 평가(규칙 + LLM Judge, ≤ 2회).", "",
           f"**3.3 4관점 {n}개 항목** — TRL {by_agent['trl']} / 시장성 {by_agent['market']} / "
           f"이해관계자 {by_agent['stakeholder']} / 도메인 {by_agent['domain']}.", "",
           "**3.4 근거 등급과 상한** — A(독립 실측·공식 공시) 5점까지, B(당사자 실측·발표) 4점까지, C(시뮬레이션·추정)·"
           "D(2차 자료) 3점까지. 관련 근거 0건일 때만 NA, 부정 근거가 C·D뿐이면 최저 2점 (rules/caps.py). 채점 LLM에게도 같은 규칙을 "
           "프롬프트로 주므로 코드 상한은 안전장치이며, 실행에서 상한이 한 번도 걸리지 않으면 4·5장 표는 원점수 열 대신 최고 등급 열을 보인다.", "",
           "**3.5 검색 규칙·균형 점검·가드레일** — 두 기술에 같은 쿼리 템플릿·max_results·기간(2025-01 이후). 균형 점검은 "
           "근거 공백·편향·조건 누락·채점 형식 오류 4종을 rules/balance.py 코드로 판정한다 (LLM 판정 아님).", "",
           "**3.6 TRL 산출과 상충 분석** — TRL은 rules/trl_gate.py 게이트 규칙(TRL-1~5 차원 점수 조합)으로, 상충 후보는 "
           "같은 기술 안의 비교 쌍 P1~P6 점수 차가 2점 이상일 때만 뽑고(rules/conflicts.py), 해석은 9장 종합에서 근거·조건을 대조해 채운다.", ""]


# ---------- 4~5. 기술별 평가 결과 ----------

def _evidence_profile(state: MainState, tech_id: str) -> list[str]:
    """채점에 인용된 근거의 구성: 수·등급·pro/con·출처 유형·최신성. 편향 통제의 근거를 독자가 직접 볼 수 있게."""
    pool = {e.evidence_id: e for e in state.get("evidence_pool", [])}
    cited = []
    seen = set()
    for r in state.get("final_results", []):
        if r.tech_id != tech_id:
            continue
        for b in r.evidence:
            if b.evidence_id in pool and b.evidence_id not in seen:
                seen.add(b.evidence_id)
                cited.append(pool[b.evidence_id])
    if not cited:
        return ["(인용된 근거 없음)", ""]
    grades = {g: sum(1 for e in cited if e.evidence_grade == g) for g in "ABCD"}
    pro = sum(1 for e in cited if e.stance == "pro")
    srcs = {}
    for e in cited:
        srcs[e.source_type] = srcs.get(e.source_type, 0) + 1
    top_src = ", ".join(f"{k} {v}" for k, v in sorted(srcs.items(), key=lambda x: -x[1])[:4])
    publishers = len({(e.source_title, e.publisher) for e in cited})
    recent = sum(1 for e in cited if (e.published_at or "") >= "2025-01-01")
    dated = sum(1 for e in cited if e.published_at)
    return [f"인용 근거 {len(cited)}건 — 등급 A {grades['A']} / B {grades['B']} / C {grades['C']} / D {grades['D']}, "
            f"긍정 {pro} / 비판 {len(cited) - pro}, 출처 {publishers}곳, 출처 유형: {top_src}. "
            f"게재일이 있는 {dated}건 중 2025-01 이후 {recent}건.", ""]


def _key_numbers(state: MainState, tech_id: str, limit: int = 5) -> list[str]:
    """A·B등급이면서 수치와 조건이 모두 있는 근거를 등급·관련 항목 순으로 뽑아 '수치 + 조건 + 출처'로 보여준다."""
    pool = {e.evidence_id: e for e in state.get("evidence_pool", [])}
    cited_ids = {b.evidence_id for r in state.get("final_results", []) if r.tech_id == tech_id for b in r.evidence}
    cands = [pool[i] for i in cited_ids if i in pool and pool[i].evidence_grade in "AB"
             and _NUM_IN_TEXT.search(pool[i].claim) and (pool[i].conditions or "").strip()]
    cands.sort(key=lambda e: (_GRADE_ORDER[e.evidence_grade], e.criterion_id, 0 if e.stance == "con" else 1))
    picked, seen_src, out = [], set(), []
    for e in cands:                                   # 같은 출처는 1건만 → 출처 다양성
        key = (e.source_title, e.publisher)
        if key in seen_src:
            continue
        seen_src.add(key)
        picked.append(e)
        if len(picked) >= limit:
            break
    if not picked:
        return ["(A·B등급 수치 근거 없음)", ""]
    out += ["| 항목 | 등급/방향 | 수치가 포함된 주장 | 조건 | 출처 |", "|---|---|---|---|---|"]
    for e in picked:
        out.append(f"| {e.criterion_id} | {e.evidence_grade}/{e.stance} | {_short(e.claim, 95)} | "
                   f"{_short(e.conditions, 60)} | {_short(e.publisher, 20)} ({(e.published_at or 'n.d.')[:7]}) |")
    return out + [""]


_NUM_IN_TEXT = re.compile(r"\d")


def _criterion_row(r, pub_by_id: dict[str, str] | None = None, show_caps: bool = True) -> str:
    """근거 열: 등급 분포(pro/con)와 발행 주체 2곳까지. evidence_id 전체 목록은 부록 A에.
    show_caps=False면 원점수·상한 적용 열 대신 최고 등급 열 (이번 실행에서 상한이 한 번도 안 걸렸을 때)."""
    grades = {}
    for b in r.evidence:
        grades[b.grade] = grades.get(b.grade, 0) + 1
    dist = " ".join(f"{g}{grades[g]}" for g in ("A", "B", "C", "D") if g in grades)
    pro = sum(1 for b in r.evidence if b.stance == "pro")
    con = len(r.evidence) - pro
    srcs = []
    for b in r.evidence:
        name = _short((pub_by_id or {}).get(b.evidence_id) or b.source, 28)
        if name not in srcs:
            srcs.append(name)
    src_txt = "; ".join(srcs[:2]) + (f" 외 {len(srcs) - 2}" if len(srcs) > 2 else "")
    ev = f"{len(r.evidence)}건 ({dist}, pro {pro}/con {con}) — {src_txt}" if r.evidence else "(근거 없음)"
    if not show_caps:
        best = min((b.grade for b in r.evidence), default="-")
        return f"| {r.criterion_id} | {r.score} | {best} | {r.confidence} | {ev} |"
    return f"| {r.criterion_id} | {r.score} | {r.raw_score} | {r.confidence} | {r.cap_applied or ''} | {ev} |"


def _tech_chapter(state: MainState, tech_id: str, names: dict, assessment: dict, num: int) -> list[str]:
    agent_titles = {"trl": "기술 성숙도", "market": "시장성 (MKT-1~4)",
                    "stakeholder": "이해관계자 (STK-1~4)", "domain": "도메인 적용 (DOM-1~5)"}
    results = [r for r in state.get("final_results", []) if r.tech_id == tech_id]
    by_agent: dict[str, list] = {}
    for r in results:
        by_agent.setdefault(r.agent_type, []).append(r)
    trl = next((tr for tr in state.get("trl_results", []) if tr.tech_id == tech_id), None)
    pub_by_id = {e.evidence_id: e.publisher for e in state.get("evidence_pool", [])}
    show_caps = any(r.cap_applied for r in state.get("final_results", []))   # 상한이 한 번도 안 걸리면 열 생략
    header = ("| 항목 | 점수 | 원점수 | 확신도 | 상한 적용 | 근거 |\n|---|---|---|---|---|---|" if show_caps
              else "| 항목 | 점수 | 최고 등급 | 확신도 | 근거 |\n|---|---|---|---|---|")

    L = [f"## {num}. {names[tech_id]} 평가 결과", "",
        f"### {num}.1 {agent_titles['trl']} — 기법 TRL / 기반 부품 성숙도", ""]
    if trl is None:
        L += ["(이번 실행 범위에 TRL 항목이 없어 산출하지 않음)", ""]
    if trl:
        met = [t for t in trl.gate_trace if "충족" in t and "미충족" not in t]
        unmet = [t for t in trl.gate_trace if "미충족" in t]
        first_unmet = unmet[0].replace("조건 미충족: ", " 미충족 (") + ")" if unmet else ""
        L += [f"TRL {trl.trl_level} (확신도 {trl.trl_confidence}, {trl.note}), "
             f"기반 부품 성숙도(TRL-5) {trl.component_maturity}", "",
             f"게이트: {met[-1].split(' 조건')[0] if met else 'TRL 1'}까지 충족"
             + (f", {first_unmet}" if first_unmet else "") + " — 전체 추적은 부록 A.", ""]
    for i, agent in enumerate(("market", "stakeholder", "domain"), start=2):
        L += [f"### {num}.{i} {agent_titles[agent]}", ""]
        rows = sorted(by_agent.get(agent, []), key=lambda r: r.criterion_id)
        if not rows:
            L += ["(이번 실행 범위에 포함되지 않은 관점 — `--criteria`로 항목을 좁혀 실행함)", ""]
            continue
        L += header.split("\n")
        L += [_criterion_row(r, pub_by_id, show_caps) for r in rows]
        L.append("")
    L += [f"### {num}.5 근거 프로필과 핵심 수치 근거 (A·B등급, 조건 포함)", ""]
    L += _evidence_profile(state, tech_id)
    L += _key_numbers(state, tech_id)
    L += [f"### {num}.6 관점 간 상충 지점과 정보 공백 (P1~P6)", ""]
    own = [c for c in state.get("conflicts", []) if c.tech_id == tech_id]
    if own:
        for c in own:
            L.append(f"- {c.comparison_id} ({c.status}, 점수 차 {c.score_gap}): {c.interpretation}")
    else:
        L.append("(상충 후보 없음 — 비교 쌍 P1~P6의 점수 차가 모두 2점 미만)" if not state.get("only_criteria")
                 else "(해당 없음 — `--criteria`로 좁혀 실행해 비교 쌍이 빠짐)")
    a = assessment.get(tech_id)
    if a:
        L += ["", f"**요약**: {a['요약']}", "", "**한계**:"] + [f"- {x}" for x in a["한계"]] + \
            ["", "**시사점**:"] + [f"- {x}" for x in a["시사점"]]
    L.append("")
    return L


# ---------- 6. 관점별 근거 대조 / 7. 조건부 적용 시사점 / 8. 한계 / 9. 종합 ----------

def _ch6(assessment: dict) -> list[str]:
    cross = assessment.get("_cross", {})
    return ["## 6. 관점별 근거 대조", "",
           "### 6.1 같은 관점에서 두 기술의 근거 유형·검증 수준은 어떻게 다른가", "",
           cross.get("관점별_근거_대조") or cross.get("근거_대조") or "(생성 안 됨)", "",
           "### 6.2 SW 접근과 HW 접근의 트레이드오프", "", cross.get("트레이드오프", "(생성 안 됨)"), "",
           "### 6.3 상호 보완 가능성 (검증되지 않은 가설)", "", cross.get("상호보완_가능성", "(생성 안 됨)"), ""]


def _ch7(assessment: dict) -> list[str]:
    sc = assessment.get("_scenario", {})
    return ["## 7. 조건부 적용 시사점", "",
           "### 7.1 시나리오 ① 기존 GPU 서버 유지 환경", "", sc.get("시나리오1", "(생성 안 됨)"), "",
           "### 7.2 시나리오 ② 서버 구조 변경 가능 환경", "", sc.get("시나리오2", "(생성 안 됨)"), "",
           "### 7.3 이해관계자별 요구·이익·부담과 충돌 지점", "", sc.get("이해관계자_충돌", "(생성 안 됨)"), "",
           "### 7.4 도입 촉진 요인과 장벽", "", sc.get("도입_요인_장벽", "(생성 안 됨)"), ""]


def _ch8(state: MainState, assessment: dict) -> list[str]:
    gaps = state.get("info_gaps", [])
    names = _names(state)
    L = ["## 8. 한계와 추가 검증 과제", "", "### 8.1 근거의 한계", ""]
    seen: set[str] = set()
    for tid in ("turboquant", "cxl_pnm"):
        items = [x for x in assessment.get(tid, {}).get("한계", []) if x not in seen]
        seen |= set(items)
        if items:
            L += [f"**{names.get(tid, tid)}**"] + [f"- {x}" for x in items] + [""]
    L += \
        ["### 8.2 정보 공백(NA) 항목과 해석 주의사항", "",
        f"{len(gaps)}건이 재시도 한도(2라운드) 소진 후에도 정보 공백으로 남았다. "
        "NA는 기술의 실패가 아니라 공개 근거로 확인되지 않았다는 뜻이다. 사유 전문은 info_gaps.json.", ""]
    by_tech: dict[str, list[str]] = {}
    for g in gaps:
        short = g.reason.split(":", 1)[0].strip()            # "편향: 비판 근거 없음" → "편향"
        detail = g.reason.split(":", 1)[1].strip() if ":" in g.reason else ""
        by_tech.setdefault(g.tech_id, []).append(f"{g.criterion_id}({_short(detail or short, 18)})")
    for tid, items in by_tech.items():
        L.append(f"- **{names.get(tid, tid)}** ({len(items)}건): {', '.join(items)}")
    fl = state.get("fanout_log") or []
    v = state.get("quality_verdict")
    if fl or v is not None:
        L += ["### 8.3 평가 과정 기록 (실행 추적)", ""]
        if fl:
            L.append("수집 worker fan-out: " + " → ".join(
                f"round {f.get('round')} {f.get('count')}개({f.get('mode', '')}, 셀 {f.get('cells', '?')})" for f in fl)
                + f". 균형 점검 재시도 {state.get('retry_round', 0)}라운드.")
        if v is not None:
            rp = ", ".join(f"{k} {'통과' if ok else '미달'}" for k, ok in v.rule_pass.items())
            js = (", ".join(f"{k} {s}" for k, s in v.judge_scores.items()) if v.judge_scores else "미호출")
            L.append(f"보고서 품질 평가 {v.round}회차: 규칙 {rp} / LLM Judge {js} → {'통과' if v.passed else '미달 기록'}.")
        L.append("")
    L += ["### 8.4 평가 시스템의 한계", "",
         "검색 범위는 코퍼스 매니페스트(최대 200쪽)와 웹 검색 max_results=5로 제한된다. "
         "임베딩 검색 성능은 eval/retrieval/model_selection.md의 Hit@K 실험 결과를 따른다. "
         "채점은 LLM 자동 채점이며 사람 검토 전 초안이다.", "",
         "### 8.5 추가 검증 과제", "",
         "- deliverables/RAG-Design_*.pdf D-12 사람 검토 체크리스트 수행 (부록 D)",
         "- 정보 공백(NA) 항목 원문 재확인",
         "- eval/retrieval 재현성 확인 후 임베딩 모델 재확정 여부 검토", ""]
    return L


def _ch9(assessment: dict) -> list[str]:
    return ["## 9. 종합", "", assessment.get("_overall", "(생성 안 됨)"), ""]


# ---------- 부록 / REFERENCE ----------

def _appendix(state: MainState) -> list[str]:
    L = ["## 부록", "", "### A. 항목별 채점 결과 상세", ""]
    for r in sorted(state.get("final_results", []), key=lambda r: (r.tech_id, r.criterion_id)):
        L += [f"**{r.tech_id} {r.criterion_id}** — 점수 {r.score} (원점수 {r.raw_score}, "
             f"확신도 {r.confidence}, cap_applied={r.cap_applied}, intra_conflict={r.intra_conflict})", "",
             r.rationale, ""]
        for b in r.evidence:
            L.append(f"  - [{b.grade}/{b.stance}] {b.evidence_id}: {b.claim} ({b.source}, {b.date})")
        L.append("")
    L += ["### B. 검색 로그와 재시도 이력 (셀 단위 집계)", "",
         "| 기술 | 항목 | 라운드 | 수집 worker 수 | 근거 수 합계 | 쿼리 재작성 |", "|---|---|---|---|---|---|"]
    agg: dict[tuple, dict] = {}
    for s_ in state.get("search_log", []):
        k = (s_.get("tech_id"), s_.get("criterion_id"))
        a = agg.setdefault(k, {"rounds": set(), "workers": 0, "results": 0, "rewritten": 0})
        a["rounds"].add(s_.get("round")); a["workers"] += 1
        a["results"] += int(s_.get("results") or 0); a["rewritten"] += int(bool(s_.get("rewritten")))
    for (tid, cid), a in sorted(agg.items(), key=lambda x: (x[0][0] or "", x[0][1] or "")):
        L.append(f"| {tid} | {cid} | {', '.join(str(r) for r in sorted(a['rounds'], key=lambda r: (r is None, r)))} | "
                 f"{a['workers']} | {a['results']} | {a['rewritten']} |")
    comp = (state.get("final_assessment") or {}).get("_comparability") or {}
    if comp:
        L += ["", "### C. 실험 조건 비교 판정 전문 (2.4)", "",
             "| 항목 | 판정 | TurboQuant 조건 | CXL-PNM 조건 | 이유 |", "|---|---|---|---|---|"]
        for cid, row in comp.items():
            L.append(f"| {cid} | {row['verdict']} | {row['turboquant']} | {row['cxl_pnm']} | {row['note']} |")
    L += ["", "### D. 평가 루브릭 전문", "", "`configs/rubrics.json` 참고 (이 보고서에는 요약만 포함).", "",
         "### E. 사람 검토 체크리스트와 검토 결과", "",
         "- [ ] 주요 수치가 실제 원문에 존재하는가", "- [ ] 출처의 발행 주체와 날짜가 맞는가",
         "- [ ] 실측·시뮬레이션·주장을 올바르게 구분했는가",
         "- [ ] TurboQuant와 CXL-PNM에 같은 검색 규칙을 적용했는가",
         "- [ ] 논문에 보고된 결과를 팀이 재현한 결과처럼 서술하지 않았는가",
         "- [ ] NA를 기술의 실패로 해석하지 않았는가",
         "- [ ] 두 기술의 점수를 합산하거나 순위화하지 않았는가",
         "- [ ] info_gaps에 기록된 항목의 처리가 타당한가", "", "(검토 결과는 검토 후 체크박스에 표시)", ""]
    return L


def _reference(state: MainState, compact: bool = False) -> list[str]:
    cited = {b.evidence_id for r in state.get("final_results", []) for b in r.evidence}
    refs = [e for e in state.get("evidence_pool", []) if e.evidence_id in cited]
    L = ["## REFERENCE", "", "> 보고서 본문에서 실제로 인용한 자료만 출처 단위로 수록(같은 출처의 근거 여러 건은 1줄, "
        "근거별 evidence_id는 부록 A). 검색했지만 인용하지 않은 자료는 부록 B 검색 로그에만 남긴다.", ""]
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    groups: dict[tuple, list] = {}
    for e in refs:
        groups.setdefault((e.source_url or e.locator.doc_id or e.source_title, e.source_title, e.publisher), []).append(e)
    rows = []
    for (loc, title, publisher), es in groups.items():
        best = min(es, key=lambda e: order[e.evidence_grade])
        if compact:
            rows.append((order[best.evidence_grade], title, f"- [{best.evidence_grade}] {_short(title, 48)} — {publisher}, "
                         f"{best.published_at or 'n.d.'}, {_short(loc, 56)}"))
        else:
            rows.append((order[best.evidence_grade], title, f"- [{best.evidence_grade}] {_short(title, 90)} — {publisher}, "
                         f"{best.published_at or 'n.d.'}, {loc} (확인일 {best.accessed_at}; 인용 근거 {len(es)}건)"))
    L += [r[2] for r in sorted(rows)]
    return L


def synthesize_report(state: MainState) -> dict:
    progress.step("synthesize_report", "종합 결과 정리 시작")
    upd = synthesize(state)
    run_dir = output_root() / state.get("run_id", "run")
    run_dir.mkdir(parents=True, exist_ok=True)
    _dump(run_dir / "evidence.json", state.get("evidence_pool", []))
    _dump(run_dir / "scores.json", state.get("final_results", []))
    _dump(run_dir / "search_log.json", state.get("search_log", []))
    _dump(run_dir / "info_gaps.json", state.get("info_gaps", []))
    (run_dir / "final_assessment.json").write_text(json.dumps(upd["final_assessment"], ensure_ascii=False, indent=2),
                                                   encoding="utf-8")
    _dump(run_dir / "trl_results.json", state.get("trl_results", []))
    _dump(run_dir / "conflicts.json", upd["conflicts"])
    progress.step("synthesize_report", "보고서 본문 조립 중")

    names = _names(state)
    assessment = upd["final_assessment"]
    state_for_render = {**state, "conflicts": upd["conflicts"]}

    L = [f"# {state['rubrics']['meta']['title']} — TurboQuant · CXL-PNM", ""]
    L += _summary(state_for_render, names, assessment)
    L += _ch1(state)
    L += _ch2(state, names, assessment)
    L += _ch3(state)
    L += _tech_chapter(state_for_render, "turboquant", names, assessment, 4)
    L += _tech_chapter(state_for_render, "cxl_pnm", names, assessment, 5)
    L += _ch6(assessment)
    L += _ch7(assessment)
    L += _ch8(state, assessment)
    L += _ch9(assessment)
    L += _appendix(state)
    L += _reference(state)

    text = "\n".join(L) + "\n"
    path = run_dir / "report.md"
    path.write_text(text, encoding="utf-8")
    progress.step("synthesize_report", f"report.md 생성 완료 ({path})")

    try:
        font = render_pdf(text, run_dir / "report.pdf")
        if font:
            progress.step("synthesize_report", f"report.pdf 생성 완료 (폰트: {font})")
        else:
            progress.step("synthesize_report", "한글 폰트를 찾지 못해 report.pdf를 건너뜀 (report.md만 생성). "
                          "KV_REPORT_FONT=<ttf 경로>로 폰트를 지정해 보세요.")
    except Exception as e:  # noqa: BLE001 — PDF 실패로 그래프 전체를 죽이지 않는다
        progress.step("synthesize_report", f"report.pdf 생성 실패, report.md만 유지: {type(e).__name__}: {e}")

    return {**upd, "report_path": str(path)}
