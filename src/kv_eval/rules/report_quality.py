"""보고서 본문 품질 검사와 유한 압축 규칙."""
from __future__ import annotations

import json
import re

from ..graph.state import MainState
from ..graph.task_schema import ReportQualityResult

MAX_REPORT_PAGES = 10
TARGET_REPORT_PAGES = (8, 10)
MAX_COMPRESSION_ROUNDS = 2

_REQUIRED_SECTIONS = (
    "SUMMARY",
    "범위와 방법",
    "TurboQuant 평가 결과",
    "CXL-PNM 평가 결과",
    "비교와 트레이드오프",
    "시나리오와 이해관계자",
    "한계와 정보 공백",
    "REFERENCE",
)
_EVIDENCE_REF_RE = re.compile(r"EVIDENCE:([A-Za-z0-9_.:-]+)")
_NUMBER_RE = re.compile(r"(?<![A-Za-z])-?\d[\d,.]*")
_CRITERION_RE = re.compile(r"\b(?:TRL|MKT|STK|DOM)-\d+\b|\bP\d+\b")
_FORBIDDEN = {
    "점수 합산": re.compile(r"총점|합산(?:\s*점수)?|점수\s*합계"),
    "순위": re.compile(r"(?:^|\s)[1-9]\s*위(?:\s|$)|순위|랭킹"),
    "단정적 우열": re.compile(
        r"(?:더|가장|압도적으로)\s*(?:우수|낫|유망|좋|뛰어)|"
        r"(?:승자|우월하다|우위에 있다|우위를 (?:보인다|가진다|차지한다)|점수가? 더 높)"
    ),
}
_PROTECTED_COMPRESSION_TERMS = (
    "EVIDENCE:", "조건", "상충", "정보 공백", "한계", "시사점", "REFERENCE",
)


def _jsonable(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _numbers(text: str) -> set[str]:
    cleaned = _EVIDENCE_REF_RE.sub("", text)
    cleaned = _CRITERION_RE.sub("", cleaned)
    cleaned = "\n".join(line for line in cleaned.splitlines()
                        if not line.startswith("#") and not set(line.strip()) <= {"|", "-", ":", " "})
    return {number.replace(",", "").strip(".") for number in _NUMBER_RE.findall(cleaned)}


def _allowed_numbers(state: MainState) -> set[str]:
    trusted_keys = (
        "technologies", "domain", "rubrics", "evidence_pool", "final_results",
        "trl_results", "conflicts", "info_gaps",
    )
    source = {key: state[key] for key in trusted_keys if key in state}
    serialized = json.dumps(_jsonable(source), ensure_ascii=False, default=str)
    return _numbers(serialized) | {str(number) for number in range(11)}


def evaluate_report(state: MainState) -> dict:
    """코드로 판정 가능한 보고서 계약을 검사한다."""
    draft = state.get("report_draft", "")
    issues: list[str] = []
    headings = {line.lstrip("# ").strip() for line in draft.splitlines() if line.startswith("## ")}
    for section in _REQUIRED_SECTIONS:
        if not any(section in heading for heading in headings):
            issues.append(f"필수 섹션 누락: {section}")

    known_ids = {e.evidence_id for e in state.get("evidence_pool", [])}
    cited_ids = set(_EVIDENCE_REF_RE.findall(draft))
    result_ids = {brief.evidence_id for result in state.get("final_results", [])
                  for brief in result.evidence}
    invalid_ids = sorted((cited_ids | result_ids) - known_ids)
    if invalid_ids:
        issues.append(f"유효하지 않은 Evidence ID: {', '.join(invalid_ids)}")

    unsupported = sorted(_numbers(draft) - _allowed_numbers(state))
    if unsupported:
        issues.append(f"근거에서 확인되지 않은 수치: {', '.join(unsupported)}")

    for label, pattern in _FORBIDDEN.items():
        if pattern.search(draft):
            issues.append(f"금지된 표현: {label}")

    if "한계" not in draft or "정보 공백" not in draft:
        issues.append("한계와 정보 공백을 모두 명시해야 함")
    for gap in state.get("info_gaps", []):
        if gap.tech_id not in draft or gap.criterion_id not in draft:
            issues.append(f"정보 공백 누락: {gap.tech_id} {gap.criterion_id}")

    result = ReportQualityResult(
        passed=not issues,
        page_count=state.get("report_page_count") or None,
        issues=issues,
        retry_kind="rewrite" if issues else None,
    )
    return {"report_quality": result}


def _first_sentence(text: str, limit: int) -> str:
    text = " ".join(text.split())
    match = re.search(r".+?[.!?。](?:\s|$)", text)
    sentence = match.group(0).strip() if match else text
    if len(sentence) <= limit:
        return sentence
    clipped = sentence[:limit - 1].rstrip()
    if clipped and not sentence[len(clipped):].startswith((" ", "\t", "\n")):
        boundary = clipped.rsplit(" ", 1)[0].rstrip()
        if boundary:
            clipped = boundary
    return clipped.rstrip(".,;:") + "…"


def _compress_markdown(draft: str, level: int) -> str:
    limit = 150 if level == 1 else 100
    seen: set[str] = set()
    lines: list[str] = []
    for raw in draft.splitlines():
        line = raw.rstrip()
        if line.startswith("|") and line.endswith("|") and "---" not in line:
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if cells and cells[0] != "항목" and len(cells) >= 5:
                cells[-1] = _first_sentence(cells[-1], limit)
                line = "| " + " | ".join(cells) + " |"
        elif (level == 2 and len(line) > limit and not line.startswith(("#", "-", ">"))
              and not any(term in line for term in _PROTECTED_COMPRESSION_TERMS)):
            line = _first_sentence(line, limit)

        normalized = " ".join(line.split())
        is_repeatable = bool(normalized and not line.startswith(("#", "|"))
                             and not any(term in line for term in _PROTECTED_COMPRESSION_TERMS))
        if is_repeatable and normalized in seen:
            continue
        if is_repeatable:
            seen.add(normalized)
        lines.append(line)

    compacted: list[str] = []
    for line in lines:
        if not line and compacted and not compacted[-1]:
            continue
        compacted.append(line)
    return "\n".join(compacted).strip() + "\n"


def compress_report(state: MainState) -> dict:
    """본문을 우선 축약한다. 글꼴·여백은 변경하지 않으며 최대 두 번만 실행한다."""
    current = state.get("report_retry_round", 0)
    if current >= MAX_COMPRESSION_ROUNDS:
        quality = state.get("report_quality")
        issues = list(quality.issues if quality else [])
        issues.append(f"압축 최대 횟수({MAX_COMPRESSION_ROUNDS}) 초과")
        return {"report_quality": ReportQualityResult(
            passed=False,
            page_count=state.get("report_page_count") or None,
            issues=issues,
            retry_kind=None,
        )}
    next_round = current + 1
    return {
        "report_draft": _compress_markdown(state["report_draft"], next_round),
        "report_retry_round": next_round,
    }
