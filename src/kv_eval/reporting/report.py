"""보고서 초안, 품질 검사, PDF 검증, 최종 저장을 순서대로 조합한다."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import progress
from ..agents.synthesis import synthesize
from ..config import output_root, technologies_config
from ..graph.state import MainState
from ..rules.report_quality import (
    MAX_COMPRESSION_ROUNDS,
    MAX_REPORT_PAGES,
    compress_report,
    evaluate_report,
)
from .pdf import persist_preview, render_preview

_GRADE_KO = {"A": "독립 실측/공식 공시", "B": "당사자 실측/발표",
             "C": "시뮬레이션/추정", "D": "2차 자료"}


def _dump(path: Path, items) -> None:
    path.write_text(json.dumps(
        [item.model_dump() if hasattr(item, "model_dump") else item for item in items],
        ensure_ascii=False,
        indent=2,
    ), encoding="utf-8")


def _names(state: MainState) -> dict[str, str]:
    return {tech["tech_id"]: tech["name"] for tech in state["technologies"]}


def _cell(text) -> str:
    return " ".join(str(text or "").replace("|", "/").split())


def _one_sentence(text: str, limit: int = 170) -> str:
    text = _cell(text)
    match = re.search(r".+?[.!?。](?:\s|$)", text)
    sentence = match.group(0).strip() if match else text
    if len(sentence) <= limit:
        return sentence
    return sentence[:limit - 1].rstrip() + "…"


def _evidence_refs(ids: list[str]) -> str:
    return ", ".join(f"EVIDENCE:{evidence_id}" for evidence_id in ids) or "(정보 공백)"


def _conditions(state: MainState, tech_id: str, criterion_id: str) -> str:
    conditions = sorted({
        _cell(evidence.conditions)
        for evidence in state.get("evidence_pool", [])
        if evidence.tech_id == tech_id and evidence.criterion_id == criterion_id and evidence.conditions
    })
    return "; ".join(conditions) or "(공개 조건 없음)"


def _summary(state: MainState, names: dict[str, str], assessment: dict) -> list[str]:
    lines = ["## SUMMARY", "", f"핵심 질문: {state['rubrics']['meta']['core_question']}", ""]
    trl_by_tech = {result.tech_id: result for result in state.get("trl_results", [])}
    for tech_id in ("turboquant", "cxl_pnm"):
        trl = trl_by_tech.get(tech_id)
        prefix = f"TRL {trl.trl_level}, 확신도 {trl.trl_confidence}; " if trl else ""
        summary = assessment.get(tech_id, {}).get("요약", "(종합 결과 없음)")
        lines.append(f"- {names.get(tech_id, tech_id)}: {prefix}{summary}")
    lines += ["", assessment.get("_overall", "(종합 결과 없음)"), ""]
    return lines


def _scope_and_method(state: MainState) -> list[str]:
    domain = state.get("domain") or technologies_config()["domain"]
    grades = ", ".join(f"{grade}={meaning}" for grade, meaning in _GRADE_KO.items())
    return [
        "## 1. 범위와 방법", "",
        f"평가 범위: {domain['name']} / 주요 워크로드: {domain['workload']}.", "",
        f"적용 시나리오: {' / '.join(domain['scenarios'])}.", "",
        ("방법: 코드 기반 계획이 근거 수집과 관점별 채점을 분배하고, 근거 균형·조건·인용을 검사한 뒤 "
         "규칙 기반 상한·TRL 게이트와 종합 단계를 적용했다."), "",
        f"근거 등급: {grades}. 실험 조건이 다르면 직접 비교하지 않고 조건 차이로 남겼다.", "",
    ]


def _result_row(state: MainState, result) -> str:
    score = result.score if result.raw_score in (None, result.score) else f"{result.score} (원 {result.raw_score})"
    evidence = _evidence_refs([brief.evidence_id for brief in result.evidence])
    condition = _conditions(state, result.tech_id, result.criterion_id)
    rationale = _one_sentence(result.rationale)
    return (f"| {result.criterion_id} | {_cell(score)} / {result.confidence} | {evidence} | "
            f"{condition} | {rationale} |")


def _tech_section(
    state: MainState, tech_id: str, number: int, names: dict[str, str], assessment: dict
) -> list[str]:
    lines = [f"## {number}. {names.get(tech_id, tech_id)} 평가 결과", ""]
    trl = next((item for item in state.get("trl_results", []) if item.tech_id == tech_id), None)
    if trl:
        lines += [(f"TRL {trl.trl_level} (확신도 {trl.trl_confidence}, {trl.note}); "
                   f"기반 부품 성숙도 {trl.component_maturity}."), ""]
    lines += [
        "| 항목 | 점수/확신도 | 근거 ID | 실험·적용 조건 | 핵심 판단 |",
        "|---|---|---|---|---|",
    ]
    results = sorted(
        (result for result in state.get("final_results", []) if result.tech_id == tech_id),
        key=lambda result: result.criterion_id,
    )
    lines += [_result_row(state, result) for result in results]
    if not results:
        lines.append("| (결과 없음) | NA / low | (정보 공백) | (공개 조건 없음) | 공개 근거 부족 |")

    lines += ["", "### 상충 지점", ""]
    conflicts = [item for item in state.get("conflicts", []) if item.tech_id == tech_id]
    if conflicts:
        for conflict in conflicts:
            refs = _evidence_refs(conflict.evidence_refs)
            lines.append(f"- {conflict.comparison_id}: {conflict.interpretation or conflict.status}; {refs}")
    else:
        lines.append("- 확인된 상충 후보 없음")

    tech_assessment = assessment.get(tech_id, {})
    lines += ["", "### 한계 및 적용 시사점", ""]
    for limitation in tech_assessment.get("한계", []):
        lines.append(f"- 한계: {_one_sentence(limitation)}")
    for implication in tech_assessment.get("시사점", []):
        lines.append(f"- 시사점: {_one_sentence(implication)}")
    if not tech_assessment.get("한계"):
        lines.append("- 한계: 공개 자료 범위 안에서만 판단한 자동 평가 초안")
    if not tech_assessment.get("시사점"):
        lines.append("- 시사점: 실제 적용 전 동일 조건의 재현 검증 필요")
    lines.append("")
    return lines


def _comparison(state: MainState, assessment: dict) -> list[str]:
    cross = assessment.get("_cross", {})
    lines = [
        "## 4. 비교와 트레이드오프", "",
        "### 근거 수준 대조", "", cross.get("근거_대조", "(생성 안 됨)"), "",
        "### SW·HW 접근 트레이드오프", "", cross.get("트레이드오프", "(생성 안 됨)"), "",
        "### 상호 보완 가능성", "", cross.get("상호보완_가능성", "(생성 안 됨)"), "",
        "| 기술 | 상충 후보 | 해석 | 근거 ID |", "|---|---|---|---|",
    ]
    names = _names(state)
    for conflict in state.get("conflicts", []):
        lines.append(f"| {names.get(conflict.tech_id, conflict.tech_id)} | {conflict.comparison_id} | "
                     f"{_cell(conflict.interpretation or conflict.status)} | "
                     f"{_evidence_refs(conflict.evidence_refs)} |")
    if not state.get("conflicts"):
        lines.append("| - | 확인된 후보 없음 | 판단 유보 | (정보 공백) |")
    lines.append("")
    return lines


def _scenarios(assessment: dict) -> list[str]:
    scenario = assessment.get("_scenario", {})
    return [
        "## 5. 시나리오와 이해관계자", "",
        "### 기존 GPU 서버 유지", "", scenario.get("시나리오1", "(생성 안 됨)"), "",
        "### 서버 구조 변경 가능", "", scenario.get("시나리오2", "(생성 안 됨)"), "",
        "### 이해관계자 충돌", "", scenario.get("이해관계자_충돌", "(생성 안 됨)"), "",
        "### 도입 요인과 장벽", "", scenario.get("도입_요인_장벽", "(생성 안 됨)"), "",
    ]


def _limits_and_gaps(state: MainState, assessment: dict) -> list[str]:
    lines = ["## 6. 한계와 정보 공백", "", "### 정보 공백", ""]
    gaps = state.get("info_gaps", [])
    if gaps:
        lines += [f"- {gap.tech_id} {gap.criterion_id}: {_one_sentence(gap.reason)}" for gap in gaps]
    else:
        lines.append("- 재시도 한도 뒤 별도로 남은 정보 공백 없음")
    lines += ["", "### 평가 한계", ""]
    limitations = [item for tech_id in ("turboquant", "cxl_pnm")
                   for item in assessment.get(tech_id, {}).get("한계", [])]
    lines += [f"- {_one_sentence(item)}" for item in dict.fromkeys(limitations)]
    lines += [
        "- 공개 코퍼스와 웹 검색 범위에 한정된 자동 평가이며 사람의 원문 검토 전 초안이다.",
        "- 서로 다른 모델·문맥 길이·배치·하드웨어 조건의 결과는 직접 우열로 해석하지 않는다.",
        "", "### 적용 시사점", "",
    ]
    implications = [item for tech_id in ("turboquant", "cxl_pnm")
                    for item in assessment.get(tech_id, {}).get("시사점", [])]
    lines += [f"- {_one_sentence(item)}" for item in dict.fromkeys(implications)]
    lines.append("")
    return lines


def _reference(state: MainState) -> list[str]:
    cited = {brief.evidence_id for result in state.get("final_results", []) for brief in result.evidence}
    cited.update(ref for conflict in state.get("conflicts", []) for ref in conflict.evidence_refs)
    evidence_by_id = {evidence.evidence_id: evidence for evidence in state.get("evidence_pool", [])}
    lines = ["## REFERENCE", "", "> 본문에서 실제 인용한 출처만 수록한다.", ""]
    groups: dict[tuple, list] = {}
    missing = []
    for evidence_id in sorted(cited):
        evidence = evidence_by_id.get(evidence_id)
        if not evidence:
            missing.append(evidence_id)
            continue
        locator = evidence.source_url or evidence.locator.doc_id or "위치 정보 없음"
        key = (evidence.source_title, evidence.publisher, evidence.published_at, locator)
        groups.setdefault(key, []).append(evidence)

    for evidence_id in missing:
        lines.append(f"- EVIDENCE:{evidence_id} — 확인되지 않은 인용")
    for (title, publisher, published_at, locator), evidence_items in groups.items():
        shown = evidence_items[:3]
        ids = ", ".join(f"EVIDENCE:{item.evidence_id}" for item in shown)
        extra = " 및 나머지 인용(결과 표 참조)" if len(evidence_items) > len(shown) else ""
        grades = "/".join(dict.fromkeys(item.evidence_grade for item in evidence_items))
        lines.append(
            f"- {ids}{extra} [{grades}] {title} — {publisher}, {published_at or 'n.d.'}, {locator} "
            f"(확인일 {shown[0].accessed_at})"
        )
    if not cited:
        lines.append("- 실제 인용된 출처 없음 — 정보 공백으로 처리")
    return lines


def build_report(state: MainState) -> str:
    names = _names(state)
    assessment = state.get("final_assessment", {})
    lines = [f"# {state['rubrics']['meta']['title']} — TurboQuant · CXL-PNM", ""]
    lines += _summary(state, names, assessment)
    lines += _scope_and_method(state)
    lines += _tech_section(state, "turboquant", 2, names, assessment)
    lines += _tech_section(state, "cxl_pnm", 3, names, assessment)
    lines += _comparison(state, assessment)
    lines += _scenarios(assessment)
    lines += _limits_and_gaps(state, assessment)
    lines += _reference(state)
    return "\n".join(lines).strip() + "\n"


def synthesize_draft(state: MainState) -> dict:
    """종합 결과와 보고서 문자열만 만들며 파일은 저장하지 않는다."""
    progress.step("synthesize_draft", "종합 결과와 보고서 초안 생성")
    synthesis_update = synthesize(state)
    rendering_state = {**state, **synthesis_update}
    return {
        **synthesis_update,
        "report_draft": build_report(rendering_state),
        "report_retry_round": 0,
        "report_page_count": 0,
    }


def persist_report(state: MainState) -> dict:
    """10페이지 제한을 통과한 preview와 JSON 상세 결과만 최종 저장한다."""
    quality = state.get("report_quality")
    if (not quality or not quality.passed or not quality.page_count
            or quality.page_count > MAX_REPORT_PAGES):
        raise ValueError("품질 및 10페이지 제한을 통과한 보고서만 저장할 수 있음")

    run_dir = output_root() / state.get("run_id", "run")
    run_dir.mkdir(parents=True, exist_ok=True)
    md_path = run_dir / "report.md"
    temp_md = run_dir / ".report.md.tmp"
    temp_md.write_text(state["report_draft"], encoding="utf-8")
    persist_preview(state, run_dir / "report.pdf")
    temp_md.replace(md_path)
    _dump(run_dir / "evidence.json", state.get("evidence_pool", []))
    _dump(run_dir / "scores.json", state.get("final_results", []))
    _dump(run_dir / "search_log.json", state.get("search_log", []))
    _dump(run_dir / "info_gaps.json", state.get("info_gaps", []))
    progress.step("persist_report", f"최종 보고서 저장 완료: {md_path}")
    return {"report_path": str(md_path)}


def _report_updates(state: MainState) -> dict:
    keys = (
        "conflicts", "final_assessment", "report_draft", "report_quality",
        "report_retry_round", "report_page_count", "report_path",
    )
    return {key: state[key] for key in keys if key in state}


def finalize_report(state: MainState) -> dict:
    """품질·페이지 제한을 통과할 때만 최종 파일을 확정한다."""
    current = dict(state)
    current.update(evaluate_report(current))
    if not current["report_quality"].passed:
        return _report_updates(current)

    while True:
        current.update(render_preview(current))
        quality = current["report_quality"]
        if quality.passed:
            current.update(persist_report(current))
            return _report_updates(current)
        if quality.retry_kind != "compress":
            return _report_updates(current)
        if current.get("report_retry_round", 0) >= MAX_COMPRESSION_ROUNDS:
            current.update(compress_report(current))
            return _report_updates(current)
        current.update(compress_report(current))
        current.update(evaluate_report(current))
        if not current["report_quality"].passed:
            return _report_updates(current)


def synthesize_report(state: MainState) -> dict:
    """기존 그래프 노드 호환용: 분리된 보고서 단계를 순차 실행한다."""
    draft_update = synthesize_draft(state)
    final_update = finalize_report({**state, **draft_update})
    return {**draft_update, **final_update}
