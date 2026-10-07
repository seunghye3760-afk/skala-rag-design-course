"""보고서 품질 평가 노드 (과제 프롬프트 §4). Hybrid = 규칙(결정적) → LLM Judge.

Deterministic Boundary (수업 Appx. B):
  evaluate_report  — 규칙 4항목(groundedness / neutrality / bias / coverage)을 코드로 판정
  judge_node       — 규칙 전부 PASS일 때만 LLM이 4항목 1~5점 + 코멘트 (판정만, 라우팅 안 함)
  route_after_quality — 순수 함수. quality_verdict·quality_round만 읽고 다음 노드 이름을 돌려준다
  finalize         — 미달 항목을 SUMMARY 끝에 기록하고 report_meta.json을 쓴 뒤 END

종료는 코드 상수로만: quality.max_rounds(runtime.yaml), recursion_limit. 모델 판단에 종료를 맡기지 않는다.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

import yaml
from langgraph.graph import END
from pydantic import BaseModel, Field

from .. import progress
from ..config import ROOT, criteria_list, output_root, runtime
from ..graph.task_schema import cell_key
from ..reporting import report as report_mod
from ..rules.balance import _covered, _numbers
from .observability import decision
from .state import OrchestratorState, QualityVerdict

PROMPT_FILE = "prompts/quality_judge.md"
ITEMS = ("groundedness", "neutrality", "bias", "coverage")
AGENT_SECTION = {"trl": "기술 성숙도", "market": "시장성", "stakeholder": "이해관계자", "domain": "도메인 적용"}
_ID_RE = re.compile(r"(?:FAKE-)?(?:turboquant|cxl_pnm)-(?:TRL|MKT|STK|DOM)-\d+-[A-Za-z0-9-]+")


@lru_cache
def quality_config() -> dict:
    return yaml.safe_load((ROOT / "configs" / "quality.yaml").read_text(encoding="utf-8"))


def max_quality_rounds() -> int:
    return int(runtime().get("quality", {}).get("max_rounds", 2))


# ---------- 보고서 텍스트 분해 ----------

def _report_text(state: OrchestratorState) -> str:
    p = state.get("report_path")
    return Path(p).read_text(encoding="utf-8") if p and Path(p).exists() else ""


def _sections(text: str) -> dict[str, str]:
    """'## ' 제목별 본문. 키는 제목 전체 (예: '## SUMMARY', '## REFERENCE')."""
    out: dict[str, str] = {}
    cur = "_head"
    for line in text.splitlines():
        if line.startswith("## "):
            cur = line.strip()
            out[cur] = ""
        else:
            out[cur] = out.get(cur, "") + line + "\n"
    return out


def _body_without(secs: dict[str, str], skip_prefixes=("## 부록", "## REFERENCE")) -> str:
    return "\n".join(v for k, v in secs.items() if not k.startswith(skip_prefixes))


# ---------- 규칙 4항목 ----------

def check_groundedness(state: OrchestratorState, secs: dict[str, str]) -> tuple[bool, list[str], list[str]]:
    """(통과, 사유, 미달 셀). 본문 인용 id ⊂ evidence_pool, 인용 근거가 REFERENCE에 있고,
    종합 서술(final_assessment)의 수치가 인용 근거 claim/excerpt·루브릭에 있는가."""
    pool = {e.evidence_id: e for e in state.get("evidence_pool", [])}
    body = _body_without(secs)
    cited = set(_ID_RE.findall(body)) | {b.evidence_id for r in state.get("final_results", []) for b in r.evidence}
    reasons, cells = [], set()
    unknown = sorted(i for i in cited if i not in pool)
    if unknown:
        reasons.append(f"evidence_pool에 없는 인용 id {len(unknown)}건: {unknown[:3]}")
    ref = secs.get("## REFERENCE", "")

    def _in_ref(e) -> bool:        # REFERENCE는 출처 단위(제목 90자 절단 + 위치) — report._reference와 같은 기준
        title = " ".join(e.source_title.split())
        short = title if len(title) <= 90 else title[:89].rstrip()
        loc = e.source_url or e.locator.doc_id or ""
        return short in ref or (bool(loc) and loc in ref)

    missing_ref = [i for i in cited if i in pool and not _in_ref(pool[i])]
    if missing_ref:
        reasons.append(f"REFERENCE에 없는 인용 근거 {len(missing_ref)}건")
        cells |= {cell_key(pool[i].tech_id, pool[i].criterion_id) for i in missing_ref}
    # 종합 서술의 수치 대조 — LLM이 새로 만든 수치만 잡는다 (balance.py와 같은 문자열 규칙)
    allowed: list[str] = [json.dumps([c.get("rubric", {}) for c in state["rubrics"]["criteria"]], ensure_ascii=False)]
    for i in cited:
        if i in pool:
            allowed += [pool[i].claim, pool[i].excerpt, pool[i].conditions or "", pool[i].published_at or ""]
    for r in state.get("final_results", []):
        allowed.append(r.rationale)
    allowed_nums = _numbers(" ".join(allowed))
    a = state.get("final_assessment") or {}
    texts = []
    for v in a.values():
        if isinstance(v, dict):
            texts += [str(x) for x in v.values()]
        else:
            texts.append(str(v))
    claimed = _numbers(" ".join(texts)) - {"1", "2", "3", "4", "5", "9"}    # 점수·TRL 단계·시나리오 번호는 수치가 아니다
    if not _covered(claimed, allowed_nums):
        bad = sorted(c for c in claimed if not _covered({c}, allowed_nums))
        reasons.append(f"종합 서술의 수치가 인용 근거에 없음: {bad[:5]}")
    return not reasons, reasons, sorted(cells)


def check_neutrality(state: OrchestratorState, secs: dict[str, str]) -> tuple[bool, list[str]]:
    body = _body_without(secs)
    hits = {p: body.count(p) for p in quality_config()["forbidden_phrases"] if p in body}
    reasons = [f"금지 표현 {hits}"] if hits else []
    a = state.get("final_assessment") or {}
    if any(k in str(a.keys()) for k in ("총점", "total", "합산")):
        reasons.append("final_assessment에 합산 점수 키 존재")
    return not reasons, reasons


def check_bias(state: OrchestratorState) -> tuple[bool, list[str], list[str]]:
    cfg = quality_config()["bias"]
    pool = {e.evidence_id: e for e in state.get("evidence_pool", [])}
    finals = state.get("final_results", [])
    reasons, cells = [], set()
    for t in state["technologies"]:
        tid = t["tech_id"]
        srcs = {(pool[b.evidence_id].source_title, pool[b.evidence_id].publisher)
                for r in finals if r.tech_id == tid for b in r.evidence if b.evidence_id in pool}
        if finals and len(srcs) < cfg["min_sources_per_tech"]:
            reasons.append(f"{tid}: 인용 출처 {len(srcs)}곳 < {cfg['min_sources_per_tech']}")
    both = single = scored = 0
    for r in finals:
        if r.score == "NA" or not r.evidence:
            continue
        scored += 1
        stances = {b.stance for b in r.evidence}
        srcs = {(pool[b.evidence_id].source_title, pool[b.evidence_id].publisher) for b in r.evidence if b.evidence_id in pool}
        if {"pro", "con"} <= stances:
            both += 1
        else:
            cells.add(cell_key(r.tech_id, r.criterion_id))
        if len(srcs) == 1 and len(r.evidence) > 1:
            single += 1
            cells.add(cell_key(r.tech_id, r.criterion_id))
    if scored:
        if both / scored < cfg["min_pro_con_ratio"]:
            reasons.append(f"pro·con 양쪽 근거 항목 비율 {both}/{scored} < {cfg['min_pro_con_ratio']}")
        if single / scored > cfg["max_single_source_ratio"]:
            reasons.append(f"단일 출처 항목 비율 {single}/{scored} > {cfg['max_single_source_ratio']}")
    return not reasons, reasons, (sorted(cells) if reasons else [])


def check_coverage(state: OrchestratorState, secs: dict[str, str]) -> tuple[bool, list[str], list[str]]:
    rub = state["rubrics"]
    agents = {c["agent"] for c in criteria_list(rub, state.get("only_criteria"))}
    finals = state.get("final_results", [])
    reasons, cells = [], []
    for t in state["technologies"]:
        tid = t["tech_id"]
        for ag in sorted(agents):
            ok = [r for r in finals if r.tech_id == tid and r.agent_type == ag and r.score != "NA"]
            if not ok:
                reasons.append(f"{tid}/{ag}: NA 아닌 결과 없음")
                cells += [cell_key(tid, c["id"]) for c in criteria_list(rub, state.get("only_criteria")) if c["agent"] == ag]
    body = _body_without(secs)
    for ag in sorted(agents):
        if AGENT_SECTION[ag] not in body:
            reasons.append(f"보고서에 '{AGENT_SECTION[ag]}' 절 없음")
    return not reasons, reasons, cells


_INTEGRITY_RE = re.compile(r"[\]\}]{4,}|\}\}\s*[A-Za-z]")   # 괄호 스팸, 또는 '}}' 뒤에 바로 영문 (깨진 구조화 출력)


def check_integrity(state: OrchestratorState, secs: dict[str, str]) -> tuple[bool, list[str]]:
    """LLM 서술에 구조화 출력 잔재(괄호 스팸·'}}' 뒤 헛소리)가 섞이지 않았는가. 미달 → 재생성."""
    body = _body_without(secs)
    hits = _INTEGRITY_RE.findall(body)
    reasons = [f"서술에 구조화 출력 잔재 {len(hits)}곳 (괄호 스팸 또는 '}}' 뒤 영문) — 종합 LLM 출력 손상"] if hits else []
    a = state.get("final_assessment") or {}
    bad = [k for k, v in a.items() if isinstance(v, str) and _INTEGRITY_RE.search(v)]
    if bad:
        reasons.append(f"final_assessment 손상 필드: {bad}")
    return not reasons, reasons


def evaluate_report(state: OrchestratorState) -> dict:
    rnd = state.get("quality_round", 0) + 1
    secs = _sections(_report_text(state))
    i_ok, i_why = check_integrity(state, secs)
    g_ok, g_why, g_cells = check_groundedness(state, secs)
    n_ok, n_why = check_neutrality(state, secs)
    b_ok, b_why, b_cells = check_bias(state)
    c_ok, c_why, c_cells = check_coverage(state, secs)
    rule_pass = {"groundedness": g_ok, "neutrality": n_ok, "bias": b_ok, "coverage": c_ok, "integrity": i_ok}
    failed = [k for k, v in rule_pass.items() if not v]
    feedback = "\n".join(f"- {k}: {'; '.join(w)}" for k, w in
                         (("groundedness", g_why), ("neutrality", n_why), ("bias", b_why), ("coverage", c_why),
                          ("integrity", i_why)) if w)
    verdict = QualityVerdict(round=rnd, rule_pass=rule_pass, passed=False, failed_items=failed,
                             failed_cells=sorted(set(g_cells + b_cells + c_cells)), feedback=feedback)
    decision(state.get("run_id", "run"), "evaluate_report", "rules_pass" if not failed else "rules_fail",
             f"round {rnd}: {rule_pass}" + (f" / {feedback}" if feedback else ""), round=rnd)
    return {"quality_verdict": verdict, "quality_round": rnd, "step_count": 1}


# ---------- LLM Judge ----------

class _JudgeOut(BaseModel):
    groundedness: int = Field(ge=1, le=5)
    neutrality: int = Field(ge=1, le=5)
    bias: int = Field(ge=1, le=5)
    coverage: int = Field(ge=1, le=5)
    comments: str = Field(description="항목별 근거 1~2문장씩. 미달 항목은 어떤 문장이 문제인지 지목")


def _judge_input(state: OrchestratorState, secs: dict[str, str]) -> str:
    body = _body_without(secs)[:24000]
    pool = {e.evidence_id: e for e in state.get("evidence_pool", [])}
    cited = [pool[b.evidence_id] for r in state.get("final_results", []) for b in r.evidence if b.evidence_id in pool]
    seen, lines = set(), []
    for e in cited:
        if e.evidence_id in seen or len(lines) >= 40:
            continue
        seen.add(e.evidence_id)
        lines.append(f"- {e.evidence_id} [{e.evidence_grade}/{e.stance}] {e.source_title}: {e.excerpt[:200]}")
    rub = state["rubrics"]
    scoped = criteria_list(rub, state.get("only_criteria"))
    agents = sorted({c["agent"] for c in scoped})
    scope = (f"[이번 실행 범위] 평가 항목 {[c['id'] for c in scoped]} / 관점 {agents}. "
             f"범위 밖 관점·항목이 비어 있는 것은 설계된 축소 실행이므로 coverage·bias 감점 사유로 삼지 않는다."
             if state.get("only_criteria") else "[이번 실행 범위] 전체 항목 · 4관점")
    derived = ["[채점·규칙 산출값 — 본문의 점수·TRL·상충 수는 여기서 나온 것이며 발췌에 없어도 추적된 것으로 본다]"]
    for r in sorted(state.get("final_results", []), key=lambda r: (r.tech_id, r.criterion_id)):
        derived.append(f"- {r.tech_id} {r.criterion_id}: {r.score}점 (확신도 {r.confidence}, 인용 {len(r.evidence)}건)")
    for t in state.get("trl_results", []):
        derived.append(f"- {t.tech_id}: TRL {t.trl_level} (확신도 {t.trl_confidence}), 기반 부품 성숙도 {t.component_maturity}")
    conf = state.get("conflicts", [])
    derived.append(f"- 상충 후보 {sum(1 for c in conf if c.status == 'candidate')}건, 정보 공백 비교 "
                   f"{sum(1 for c in conf if c.status == 'info_gap')}건, info_gaps {len(state.get('info_gaps', []))}건")
    return (f"{scope}\n\n" + "\n".join(derived) + f"\n\n[보고서 본문]\n{body}\n\n[인용 근거 원문 발췌]\n"
            + "\n".join(lines))


def judge_node(state: OrchestratorState) -> dict:
    """규칙 PASS 후 LLM 판정. 판정만 하고 다음 노드는 정하지 않는다."""
    v = state["quality_verdict"]
    thr = int(runtime().get("quality", {}).get("judge_threshold", 4))
    if os.getenv("KV_FAKE") == "1":
        scores, comments = {k: 5 for k in ITEMS}, "(FAKE) 규칙 결과 그대로"
    else:
        from ..llm import chat_model

        secs = _sections(_report_text(state))
        system = (ROOT / PROMPT_FILE).read_text(encoding="utf-8")
        out: _JudgeOut = chat_model().with_structured_output(_JudgeOut).invoke(
            [{"role": "system", "content": system}, {"role": "user", "content": _judge_input(state, secs)}])
        scores, comments = {k: getattr(out, k) for k in ITEMS}, out.comments
    failed = [k for k in ITEMS if scores[k] < thr]
    passed = not failed
    feedback = "" if passed else f"- LLM Judge 미달 {failed} (임계 {thr}): {comments}"
    decision(state.get("run_id", "run"), "judge_node", "judge_pass" if passed else "judge_fail",
             f"round {v.round}: {scores}", round=v.round, scores=scores)
    return {"quality_verdict": v.model_copy(update={"judge_scores": scores, "judge_called": True, "passed": passed,
                                                    "failed_items": failed, "feedback": feedback}),
            "step_count": 1}


# ---------- 게이트 (순수 함수) ----------

def route_after_evaluate(state: OrchestratorState) -> str:
    v = state["quality_verdict"]
    if not v.failed_items and not v.judge_called:
        return "judge_node"
    return route_after_quality(state)


def route_after_quality(state: OrchestratorState) -> str:
    """quality_verdict·quality_round만 읽는다. 상태를 바꾸지 않는다."""
    v = state["quality_verdict"]
    if v.passed:
        return "finalize"
    if state.get("quality_round", 0) >= max_quality_rounds():
        return "finalize"                                   # 한도 도달 → 미달 기록 후 정상 종료
    if v.failed_cells and ({"groundedness", "bias", "coverage"} & set(v.failed_items)):
        if set(v.failed_items) <= {"coverage"}:
            return "score_dispatch"                         # 채점 결과만 비었음 → 재채점
        return "plan_tasks"                                 # 근거 문제 → 재수집 계획(replan)
    return "synthesize_report"                              # 중립성·서술 수치 → 피드백 주입 후 재생성


# ---------- 재생성 래퍼 / 종료 ----------

def synthesize_node(state: OrchestratorState) -> dict:
    """reporting.report.synthesize_report 래퍼: 제외된 셀을 info_gaps에 합치고, 품질 피드백을 프롬프트에 넘긴다."""
    gaps = list(state.get("info_gaps") or [])
    seen = {(g.tech_id, g.criterion_id) for g in gaps}
    for g in state.get("excluded_gaps") or []:
        if (g.tech_id, g.criterion_id) not in seen:
            gaps.append(g)
            seen.add((g.tech_id, g.criterion_id))
    v = state.get("quality_verdict")
    fb = v.feedback if (v and not v.passed and v.feedback) else ""
    if fb:
        decision(state.get("run_id", "run"), "synthesize_report", "regenerate_with_feedback", fb[:300])
    out = report_mod.synthesize_report({**state, "info_gaps": gaps, "synthesis_feedback": fb})
    return {**out, "info_gaps": gaps, "synthesis_feedback": fb, "step_count": 1}


def _pdf_pages(path: Path) -> int | None:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(path)).pages) if path.exists() else None
    except Exception:  # noqa: BLE001
        return None


def _enforce_page_limit(state: OrchestratorState, path: Path, text: str, pages: int | None) -> int | None:
    """PDF가 quality.max_pages를 넘으면 부록(근거 전수 표)을 appendix.md로 분리하고 본문만 다시 렌더링한다.
    SUMMARY·REFERENCE는 유지된다 (부록 절만 빠진다)."""
    limit = int(runtime().get("quality", {}).get("max_pages", 10))
    if pages is None or pages <= limit or "\n## 부록" not in text:
        return pages
    head, rest = text.split("\n## 부록", 1)
    ref_idx = rest.find("\n## REFERENCE")
    appendix, reference = ("## 부록" + rest[:ref_idx], rest[ref_idx:]) if ref_idx >= 0 else ("## 부록" + rest, "")
    (path.parent / "appendix.md").write_text(appendix + "\n", encoding="utf-8")
    slim = head + "\n## 부록\n\n부록(A 항목별 채점 상세 · B 검색 로그 · C 실험 조건 비교 판정 전문 · D 루브릭 · E 사람 검토 체크리스트)은 같은 폴더의 appendix.md 참고.\n" + reference
    path.write_text(slim, encoding="utf-8")
    try:
        report_mod.render_pdf(slim, path.with_suffix(".pdf"))
    except Exception as e:  # noqa: BLE001
        progress.step("finalize", f"PDF 재생성 실패: {type(e).__name__}: {e}")
    new_pages = _pdf_pages(path.with_suffix(".pdf"))
    decision(state.get("run_id", "run"), "finalize", "split_appendix",
             f"PDF {pages}쪽 > {limit}쪽 → 부록을 appendix.md로 분리, 본문 {new_pages}쪽")
    if new_pages is not None and new_pages > limit:
        slim = _compact_body(state, slim)
        path.write_text(slim, encoding="utf-8")
        try:
            report_mod.render_pdf(slim, path.with_suffix(".pdf"))
        except Exception as e:  # noqa: BLE001
            progress.step("finalize", f"PDF 재생성 실패: {type(e).__name__}: {e}")
        before, new_pages = new_pages, _pdf_pages(path.with_suffix(".pdf"))
        decision(state.get("run_id", "run"), "finalize", "compact_body",
                 f"본문 {before}쪽 > {limit}쪽 → REFERENCE 압축 표기 → {new_pages}쪽")
    if new_pages is not None and new_pages > limit:                    # 3단계: 핵심 수치 표를 3행으로
        slim = _trim_key_numbers(slim, keep=3)
        path.write_text(slim, encoding="utf-8")
        try:
            report_mod.render_pdf(slim, path.with_suffix(".pdf"))
        except Exception as e:  # noqa: BLE001
            progress.step("finalize", f"PDF 재생성 실패: {type(e).__name__}: {e}")
        before, new_pages = new_pages, _pdf_pages(path.with_suffix(".pdf"))
        decision(state.get("run_id", "run"), "finalize", "trim_key_numbers",
                 f"본문 {before}쪽 > {limit}쪽 → 4·5장 핵심 수치 표를 3행으로 → {new_pages}쪽")
    if new_pages is not None and new_pages > limit:                    # 4단계: 핵심 수치 표 제거 + 2장 brief 2줄
        slim = _trim_key_numbers(slim, keep=0)
        slim = _trim_brief_bullets(slim, keep=2)
        path.write_text(slim, encoding="utf-8")
        try:
            report_mod.render_pdf(slim, path.with_suffix(".pdf"))
        except Exception as e:  # noqa: BLE001
            progress.step("finalize", f"PDF 재생성 실패: {type(e).__name__}: {e}")
        before, new_pages = new_pages, _pdf_pages(path.with_suffix(".pdf"))
        decision(state.get("run_id", "run"), "finalize", "drop_key_numbers",
                 f"본문 {before}쪽 > {limit}쪽 → 핵심 수치 표 제거(부록·evidence.json 참고)·2장 brief 2줄 → {new_pages}쪽")
    return new_pages


def _trim_brief_bullets(text: str, keep: int = 2) -> str:
    """2.1/2.2의 brief 불릿(해결 병목·효과 조건·품질/비용·미평가 조건) 중 앞 keep개만 남긴다."""
    out, count, in_sec = [], 0, False
    for line in text.splitlines():
        if line.startswith("### 2.1") or line.startswith("### 2.2"):
            in_sec, count = True, 0
        elif line.startswith("### ") or line.startswith("## "):
            in_sec = False
        if in_sec and line.startswith("- **"):
            count += 1
            if count > keep:
                continue
        out.append(line)
    return "\n".join(out) + "\n"


def _trim_key_numbers(text: str, keep: int = 3) -> str:
    """'수치가 포함된 주장' 표(4.5/5.5)의 데이터 행을 keep개만 남긴다. 전체는 appendix.md·evidence.json에."""
    out, in_tbl, kept = [], False, 0
    for line in text.splitlines():
        if line.startswith("| 항목 | 등급/방향 | 수치가 포함된 주장 |"):
            in_tbl, kept = True, 0
            if keep <= 0:
                out.append("(핵심 수치 근거 표는 쪽수 제한으로 생략 — 부록 A·evidence.json 참고)")
                continue
            out.append(line)
            continue
        if in_tbl:
            if line.startswith("|---"):
                if keep > 0:
                    out.append(line)
                continue
            if line.startswith("| "):
                kept += 1
                if kept <= keep:
                    out.append(line)
                continue
            in_tbl = False
        out.append(line)
    return "\n".join(out) + "\n"


def _compact_body(state: OrchestratorState, text: str) -> str:
    """2단계 압축: REFERENCE를 간략 표기(제목 48자·URL 56자·확인일 생략)로 다시 쓴다."""
    head, _, _ = text.partition("\n## REFERENCE")

    ref = "\n".join(report_mod._reference(state, compact=True)) + "\n"
    return head.rstrip("\n") + "\n" + ref


def finalize(state: OrchestratorState) -> dict:
    v = state.get("quality_verdict")
    path = Path(state["report_path"])
    text = path.read_text(encoding="utf-8")
    if v and not v.passed:
        note = ["", "### 품질 평가 미달 항목", "",
                (f"품질 평가 {v.round}회 후에도 아래 항목이 기준에 미달해 그대로 기록한다 (quality.max_rounds="
                 f"{max_quality_rounds()}). 미달은 보고서의 신뢰 한계를 뜻하며 기술 평가 결과가 아니다."), ""]
        note += [f"- {k}" for k in v.failed_items]
        if v.feedback:
            note.append("")
            for line in v.feedback.splitlines():
                note.append(line if len(line) <= 400 else line[:399].rstrip() + "…")
        marker = "\n## 1. 개요"
        text = text.replace(marker, "\n".join(note) + "\n" + marker, 1)
        path.write_text(text, encoding="utf-8")
        try:
            report_mod.render_pdf(text, path.with_suffix(".pdf"))
        except Exception as e:  # noqa: BLE001
            progress.step("finalize", f"PDF 재생성 실패: {type(e).__name__}: {e}")
    pages = _pdf_pages(path.with_suffix(".pdf"))
    pages = _enforce_page_limit(state, path, text, pages)
    excluded = [t.task_id for t in state.get("task_plan") or [] if t.status == "excluded"]
    partial = bool(excluded or state.get("info_gaps") or state.get("errors") or not (v and v.passed))
    status = "PARTIAL" if partial else "SUCCESS"
    meta = {"run_id": state.get("run_id"), "status": status, "quality_round": state.get("quality_round"),
            "retry_round": state.get("retry_round"), "quality_verdict": v.model_dump() if v else None,
            "fanout_log": state.get("fanout_log", []), "excluded": excluded, "errors": state.get("errors", []),
            "step_count": state.get("step_count", 0), "report_pages": pages, "report_path": str(path)}
    (output_root() / state.get("run_id", "run") / "report_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    decision(state.get("run_id", "run"), "finalize", status,
             f"quality_round {state.get('quality_round')} / retry_round {state.get('retry_round')} / "
             f"excluded {len(excluded)} / PDF {pages}쪽")
    return {"status": status, "report_pages": pages, "step_count": 1}


ROUTES = ["plan_tasks", "score_dispatch", "synthesize_report", "finalize"]
__all__ = ["END", "ROUTES", "evaluate_report", "finalize", "judge_node", "route_after_evaluate",
           "route_after_quality", "synthesize_node"]
