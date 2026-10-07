"""보고서 초안의 형식 검사 + LLM Judge 기반 품질평가."""
from __future__ import annotations

import json
import os

from .. import progress
from ..config import runtime
from ..graph.state import MainState
from ..graph.task_schema import QualityDimension, QualityEvaluation

_PERSPECTIVES = ("기술 성숙도", "시장성", "이해관계자", "도메인")
_RANKING_WORDS = ("총점", "1위", "2위", "더 우수", "최고의 선택", "추천한다")


def _rule_checks(state: MainState, draft: str) -> dict[str, bool]:
    known = {e.evidence_id for e in state.get("evidence_pool", [])}
    required = {b.evidence_id for r in state.get("final_results", []) for b in r.evidence}
    cited = {evidence_id for evidence_id in known if evidence_id in draft}
    return {
        "citations_resolve": bool(required) and required <= cited <= known,
        "four_perspectives": all(name in draft for name in _PERSPECTIVES),
        "no_ranking_language": not any(word in draft for word in _RANKING_WORDS),
        "both_technologies": all(t["name"] in draft for t in state.get("technologies", [])),
    }


def _from_checks(checks: dict[str, bool]) -> QualityEvaluation:
    def dim(ok: bool, good: str, bad: str) -> QualityDimension:
        return QualityDimension(passed=ok, score=5 if ok else 2, reason=good if ok else bad)
    grounded = dim(checks["citations_resolve"], "모든 근거 ID가 State의 Evidence로 추적됨",
                   "근거 ID가 없거나 State의 Evidence와 연결되지 않음")
    neutral = dim(checks["no_ranking_language"], "총점·순위·일방 추천 표현 없음",
                  "우열 또는 추천으로 읽힐 수 있는 표현 발견")
    bias = dim(checks["both_technologies"], "두 기술을 모두 독립적으로 다룸",
               "한 기술의 분석이 누락됨")
    coverage = dim(checks["four_perspectives"], "네 평가 관점이 모두 포함됨",
                   "기술 성숙도·시장성·이해관계자·도메인 중 일부 누락")
    dims = (grounded, neutral, bias, coverage)
    hints = [d.reason for d in dims if not d.passed]
    return QualityEvaluation(groundedness=grounded, neutrality=neutral, bias_control=bias,
                             perspective_coverage=coverage, passed=all(d.passed for d in dims),
                             revision_hints=hints, checks=checks)


def _llm_evaluate(state: MainState, draft: str, checks: dict[str, bool]) -> QualityEvaluation:
    from langchain_openai import ChatOpenAI

    rt = runtime()["llm"]
    model = ChatOpenAI(model=rt.get("model") or "gpt-4.1-mini", temperature=0)
    # QualityEvaluation.checks is a free-form mapping. OpenAI's strict
    # json_schema response format rejects such mappings, while tool/function
    # calling supports the same Pydantic model and remains structured.
    judge = model.with_structured_output(QualityEvaluation, method="function_calling")
    evidence_ids = [e.evidence_id for e in state.get("evidence_pool", [])]
    prompt = (
        "다음 기술평가 보고서를 Groundedness, 중립성, 편향 통제, 4개 관점 커버리지로 평가하라. "
        "각 항목은 1~5점과 통과 여부, 구체적 사유를 반환한다. 모든 항목이 통과해야 passed=true다. "
        "기술 우열·총점·추천을 허용하지 않는다.\n\n"
        f"코드 검사={json.dumps(checks, ensure_ascii=False)}\n"
        f"사용 가능한 evidence_id={evidence_ids}\n\n보고서:\n{draft}"
    )
    out = judge.invoke(prompt)
    out.checks = checks
    out.passed = out.passed and all(checks.values())
    return out


def evaluate_report(state: MainState) -> dict:
    draft = state.get("report_draft", "")
    checks = _rule_checks(state, draft)
    if os.getenv("KV_FAKE") == "1":
        result = _from_checks(checks)
    else:
        result = _llm_evaluate(state, draft, checks)
    progress.step("report_quality", "통과" if result.passed else "수정 필요")
    return {"quality_evaluation": result, "phase": "evaluated"}
