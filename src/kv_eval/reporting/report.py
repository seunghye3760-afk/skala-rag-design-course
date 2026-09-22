"""보고서 조립 (설계서 E 목차 초안). SUMMARY가 맨 앞, REFERENCE(실제 인용한 자료만)가 맨 끝.
담당 4.

report_path는 항상 report.md를 가리킨다 (tests/test_graph_runs.py가 이 경로를 텍스트로
읽어 "## " 제목 줄을 검사하므로 바꾸면 안 된다). report.pdf는 report.md 옆에 추가로
만들어질 뿐이고, 한글 폰트를 못 찾거나 PDF 조립이 실패해도 report.md는 그대로 만들어진다
(그래프가 마지막 노드에서 죽지 않게 한다 — reporting/pdf.py의 render_pdf 참고).
"""
from __future__ import annotations

import json

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


def _tech_conditions(state: MainState, tech_id: str, cid: str) -> set[str]:
    pool = state.get("evidence_pool", [])
    return {(e.conditions or "").strip() for e in pool if e.tech_id == tech_id and e.criterion_id == cid
           and (e.conditions or "").strip()}


def _ch2(state: MainState, names: dict) -> list[str]:
    tcfg = technologies_config()["technologies"]
    by_id = {t["tech_id"]: t for t in tcfg}
    L = ["## 2. 대상 기술 개요", ""]
    for tid in ("turboquant", "cxl_pnm"):
        t = by_id.get(tid, {})
        L += [f"### 2.{1 if tid == 'turboquant' else 2} {names.get(tid, tid)} — "
             f"{'데이터 표현의 압축' if tid == 'turboquant' else '저장·계산 구조의 재구성'}", "",
             f"분류: {t.get('group', '?')} / {t.get('category', '?')}", ""]
    L += ["### 2.3 기술 구조 비교표", "",
         "| 구분 | KV cache 접근 방식 | 해결 대상 병목 |", "|---|---|---|"]
    for tid in ("turboquant", "cxl_pnm"):
        t = by_id.get(tid, {})
        L.append(f"| {names.get(tid, tid)} | {t.get('category', '?')} | (agents/domain.py DOM-1~3 근거 참고) |")
    L += ["", "### 2.4 실험 근거 비교표 — 실험 조건이 다르면 '직접 비교 불가'", "",
         "코드로 기계적 판정: 두 기술 모두 근거가 있고 조건(conditions) 문자열이 같으면 "
         "'비교 가능', 근거는 있지만 조건이 다르면 '직접 비교 불가', 한쪽만 있으면 '정보 부족'.", "",
         "| 항목 | TurboQuant 조건 | CXL-PNM 조건 | 판정 |", "|---|---|---|---|"]
    for c in state["rubrics"]["criteria"]:
        cid = c["id"]
        a, b = _tech_conditions(state, "turboquant", cid), _tech_conditions(state, "cxl_pnm", cid)
        if not a and not b:
            continue
        verdict = "정보 부족" if not a or not b else ("비교 가능" if a & b else "직접 비교 불가")
        L.append(f"| {cid} | {'; '.join(a) or '(없음)'} | {'; '.join(b) or '(없음)'} | {verdict} |")
    L.append("")
    return L


def _ch3(state: MainState) -> list[str]:
    rub = state["rubrics"]
    n = len(rub["criteria"])
    return ["## 3. 평가 방법", "",
           "### 3.1 도메인과 평가 주체", "", rub["meta"]["domain"], "",
           "### 3.2 평가 시스템 구성", "",
           "LangGraph 기반 Agentic RAG (그림 2, src/kv_eval/graph/main.py): 근거 수집 → 채점 → "
           "균형 점검(재시도 최대 2라운드) → 규칙 처리 → 종합 → 보고서. "
           "검색은 논문 하이브리드 검색(bge-m3 dense + BM25) + 웹 검색을 병행한다.", "",
           f"### 3.3 4가지 평가 관점과 {n}개 평가 항목", "",
           "| 관점 | 항목 |", "|---|---|"] + [
        f"| {agent} | {', '.join(c['id'] for c in rub['criteria'] if c['agent'] == agent)} |"
        for agent in ("trl", "market", "stakeholder", "domain")
    ] + ["", "### 3.4 근거 등급·상한·하한·NA 규칙", "",
        "| 등급 | 의미 | 점수 상한 |", "|---|---|---|"] + [
        f"| {g} | {desc} | {cap} |" for g, desc, cap in
        [("A", _GRADE_KO["A"], 5), ("B", _GRADE_KO["B"], 4), ("C", _GRADE_KO["C"], 3), ("D", _GRADE_KO["D"], 3)]
    ] + ["", "관련 근거 0건일 때만 NA. 부정 근거가 C·D뿐이면 최저 2점 (rules/caps.py).", "",
        "### 3.5 검색 규칙, 균형 점검, 가드레일", "",
        "검색·재작성·재시도 한도는 설계서 D-10, 균형 점검 판정 조건은 rules/balance.py에 코드로 "
        "고정되어 있다 (근거 공백·편향·조건 누락·채점 형식 오류 4종, LLM 판정 아님).", "",
        "### 3.6 TRL 산출과 상충 분석 방법", "",
        "TRL은 rules/trl_gate.py의 게이트 규칙(TRL-1~5 차원 점수 조합)으로 산출하고, 상충 "
        "후보는 rules/conflicts.py가 같은 기술 안에서 P1~P6 비교 쌍의 점수 차가 2점 이상일 때만 "
        "뽑는다 (자동 판정 아님 — 해석은 9장 종합에서 근거·조건을 대조해 채운다).", ""]


# ---------- 4~5. 기술별 평가 결과 ----------

def _criterion_row(r) -> str:
    return (f"| {r.criterion_id} | {r.score} | {r.raw_score} | {r.confidence} | {r.cap_applied or ''} | "
           f"{', '.join(b.evidence_id for b in r.evidence)} |")


def _tech_chapter(state: MainState, tech_id: str, names: dict, assessment: dict, num: int) -> list[str]:
    agent_titles = {"trl": "기술 성숙도", "market": "시장성 (MKT-1~4)",
                    "stakeholder": "이해관계자 (STK-1~4)", "domain": "도메인 적용 (DOM-1~5)"}
    results = [r for r in state.get("final_results", []) if r.tech_id == tech_id]
    by_agent: dict[str, list] = {}
    for r in results:
        by_agent.setdefault(r.agent_type, []).append(r)
    trl = next((tr for tr in state.get("trl_results", []) if tr.tech_id == tech_id), None)

    L = [f"## {num}. {names[tech_id]} 평가 결과", "",
        f"### {num}.1 {agent_titles['trl']} — 기법 TRL / 기반 부품 성숙도", ""]
    if trl:
        L += [f"TRL {trl.trl_level} (확신도 {trl.trl_confidence}, {trl.note}), "
             f"기반 부품 성숙도(TRL-5) {trl.component_maturity}", "", "게이트 추적:"] + \
            [f"- {t}" for t in trl.gate_trace] + [""]
    for i, agent in enumerate(("market", "stakeholder", "domain"), start=2):
        L += [f"### {num}.{i} {agent_titles[agent]}", "",
             "| 항목 | 점수 | 원점수 | 확신도 | 상한 적용 | 근거 |", "|---|---|---|---|---|---|"]
        for r in sorted(by_agent.get(agent, []), key=lambda r: r.criterion_id):
            L.append(_criterion_row(r))
        L.append("")
    L += [f"### {num}.5 관점 간 상충 지점과 정보 공백 (P1~P6)", ""]
    own = [c for c in state.get("conflicts", []) if c.tech_id == tech_id]
    if own:
        for c in own:
            L.append(f"- {c.comparison_id} ({c.status}, 점수 차 {c.score_gap}): {c.interpretation}")
    else:
        L.append("(해당 없음 — 작게 돌린 실행이라 비교 쌍이 빠졌거나, 상충 후보가 없음)")
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
           cross.get("근거_대조", "(생성 안 됨)"), "",
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
    limits = [x for tid in ("turboquant", "cxl_pnm") for x in assessment.get(tid, {}).get("한계", [])]
    gaps = state.get("info_gaps", [])
    L = ["## 8. 한계와 추가 검증 과제", "",
        "### 8.1 근거의 한계", ""] + [f"- {x}" for x in limits] + \
        ["", "### 8.2 정보 공백(NA) 항목과 해석 주의사항", "",
        f"{len(gaps)}건이 재시도 한도(2라운드) 소진 후에도 정보 공백으로 남았다. "
        "NA는 기술의 실패가 아니라 공개 근거로 확인되지 않았다는 뜻이다.", ""]
    for g in gaps:
        L.append(f"- {g.tech_id} {g.criterion_id}: {g.reason}")
    L += ["", "### 8.3 평가 시스템의 한계", "",
         "검색 범위는 코퍼스 매니페스트(최대 200쪽)와 웹 검색 max_results=5로 제한된다. "
         "임베딩 검색 성능은 eval/retrieval/model_selection.md의 Hit@K 실험 결과를 따른다. "
         "채점은 LLM 자동 채점이며 사람 검토 전 초안이다.", "",
         "### 8.4 추가 검증 과제", "",
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
    L += ["### B. 검색 로그와 재시도 이력", "",
         "| round | 기술 | 항목 | 결과 수 | 재작성 | 검색일 |", "|---|---|---|---|---|---|"]
    for s in state.get("search_log", []):
        L.append(f"| {s.get('round')} | {s.get('tech_id')} | {s.get('criterion_id')} | "
                f"{s.get('results')} | {s.get('rewritten')} | {s.get('searched_at')} |")
    L += ["", "### C. 평가 루브릭 전문", "", "`configs/rubrics.json` 참고 (이 보고서에는 요약만 포함).", "",
         "### D. 사람 검토 체크리스트와 검토 결과", "",
         "- [ ] 주요 수치가 실제 원문에 존재하는가", "- [ ] 출처의 발행 주체와 날짜가 맞는가",
         "- [ ] 실측·시뮬레이션·주장을 올바르게 구분했는가",
         "- [ ] TurboQuant와 CXL-PNM에 같은 검색 규칙을 적용했는가",
         "- [ ] 논문에 보고된 결과를 팀이 재현한 결과처럼 서술하지 않았는가",
         "- [ ] NA를 기술의 실패로 해석하지 않았는가",
         "- [ ] 두 기술의 점수를 합산하거나 순위화하지 않았는가",
         "- [ ] info_gaps에 기록된 항목의 처리가 타당한가", "", "(검토 결과는 검토 후 체크박스에 표시)", ""]
    return L


def _reference(state: MainState) -> list[str]:
    cited = {b.evidence_id for r in state.get("final_results", []) for b in r.evidence}
    refs = [e for e in state.get("evidence_pool", []) if e.evidence_id in cited]
    L = ["## REFERENCE", "", "> 보고서 본문에서 실제로 인용한 자료만 수록. 검색했지만 인용하지 않은 "
        "자료는 부록 B 검색 로그에만 남긴다.", ""]
    for e in refs:
        L.append(f"- [{e.evidence_grade}] {e.source_title} — {e.publisher}, {e.published_at or 'n.d.'}, "
                f"{e.source_url or e.locator.doc_id} (확인일 {e.accessed_at})")
    return L


def synthesize_report(state: MainState) -> dict:
    upd = synthesize(state)
    run_dir = output_root() / state.get("run_id", "run")
    run_dir.mkdir(parents=True, exist_ok=True)
    _dump(run_dir / "evidence.json", state.get("evidence_pool", []))
    _dump(run_dir / "scores.json", state.get("final_results", []))
    _dump(run_dir / "search_log.json", state.get("search_log", []))
    _dump(run_dir / "info_gaps.json", state.get("info_gaps", []))

    names = _names(state)
    assessment = upd["final_assessment"]
    state_for_render = {**state, "conflicts": upd["conflicts"]}

    L = [f"# {state['rubrics']['meta']['title']} — TurboQuant · CXL-PNM", ""]
    L += _summary(state_for_render, names, assessment)
    L += _ch1(state)
    L += _ch2(state, names)
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

    try:
        font = render_pdf(text, run_dir / "report.pdf")
        if font:
            print(f"[report] report.pdf 생성 완료 (폰트: {font})")
        else:
            print("[report] 한글 폰트를 찾지 못해 report.pdf를 건너뜀 (report.md만 생성). "
                 "KV_REPORT_FONT=<ttf 경로>로 폰트를 지정해 보세요.")
    except Exception as e:  # noqa: BLE001 — PDF 실패로 그래프 전체를 죽이지 않는다
        print(f"[report] report.pdf 생성 실패, report.md만 유지: {type(e).__name__}: {e}")

    return {**upd, "report_path": str(path)}
