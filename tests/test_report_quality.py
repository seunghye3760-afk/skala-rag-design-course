"""보고서 품질 검사, 페이지 제한, 최종 저장 gate 테스트."""

from kv_eval.graph.task_schema import (
    CriterionResult,
    Evidence,
    EvidenceBrief,
    Locator,
    ReportQualityResult,
)
from kv_eval.reporting import pdf as pdf_mod
from kv_eval.reporting import report as report_mod
from kv_eval.rules.report_quality import compress_report, evaluate_report


def _evidence():
    return Evidence(
        evidence_id="e1",
        tech_id="turboquant",
        criterion_id="DOM-3",
        claim="처리량 개선",
        source_title="실험 보고서",
        publisher="ExampleLab",
        accessed_at="2026-10-07",
        source_type="독립 벤치마크",
        evidence_grade="A",
        stance="pro",
        measurement_type="실측",
        conditions="동일 GPU",
        excerpt="처리량이 개선됐다.",
        locator=Locator(url="https://example.com/e1"),
    )


def _result():
    return CriterionResult(
        tech_id="turboquant",
        criterion_id="DOM-3",
        agent_type="domain",
        score=3,
        rationale="조건부 개선으로 판단한다.",
        confidence="medium",
        evidence=[EvidenceBrief(
            evidence_id="e1",
            claim="처리량 개선",
            source="실험 보고서",
            grade="A",
            stance="pro",
        )],
    )


def _valid_draft():
    return """# 평가 보고서

## SUMMARY
조건에 따라 결과를 해석한다.

## 1. 범위와 방법
근거를 수집하고 조건을 대조했다.

## 2. TurboQuant 평가 결과
EVIDENCE:e1을 인용한다.

## 3. CXL-PNM 평가 결과
공개 근거는 정보 공백으로 남긴다.

## 4. 비교와 트레이드오프
서로 다른 조건을 직접 비교하지 않는다.

## 5. 시나리오와 이해관계자
적용 환경과 부담을 함께 검토한다.

## 6. 한계와 정보 공백
공개 자료 범위라는 한계와 정보 공백을 명시한다.

## REFERENCE
- EVIDENCE:e1 실험 보고서
"""


def _state(draft=None):
    return {
        "run_id": "quality",
        "report_draft": draft or _valid_draft(),
        "report_retry_round": 0,
        "report_page_count": 0,
        "evidence_pool": [_evidence()],
        "final_results": [_result()],
        "info_gaps": [],
    }


def _preview_result(page_count):
    passed = page_count <= 10
    return {
        "report_page_count": page_count,
        "report_quality": ReportQualityResult(
            passed=passed,
            page_count=page_count,
            issues=[] if passed else [f"PDF가 {page_count}페이지"],
            retry_kind=None if passed else "compress",
        ),
    }


def test_ten_pages_passes_and_persists(monkeypatch):
    persisted = []
    monkeypatch.setattr(report_mod, "render_preview", lambda state: _preview_result(10))
    monkeypatch.setattr(report_mod, "persist_report",
                        lambda state: persisted.append(state["report_page_count"]) or {"report_path": "report.md"})

    out = report_mod.finalize_report(_state())

    assert out["report_quality"].passed is True
    assert out["report_page_count"] == 10
    assert out["report_retry_round"] == 0
    assert persisted == [10]


def test_eleven_pages_is_compressed_then_passes(monkeypatch):
    counts = iter((11, 9))
    persisted = []
    monkeypatch.setattr(report_mod, "render_preview", lambda state: _preview_result(next(counts)))
    monkeypatch.setattr(report_mod, "persist_report",
                        lambda state: persisted.append(state["report_page_count"]) or {"report_path": "report.md"})

    out = report_mod.finalize_report(_state())

    assert out["report_quality"].passed is True
    assert out["report_page_count"] == 9
    assert out["report_retry_round"] == 1
    assert persisted == [9]


def test_quality_failure_is_rewritten_then_passes(monkeypatch):
    persisted = []
    invalid = _valid_draft().replace("## 6. 한계와 정보 공백", "### 검토 메모")

    def rewrite(state):
        return {
            "report_draft": _valid_draft(),
            "report_retry_round": state["report_retry_round"] + 1,
            "report_page_count": 0,
        }

    monkeypatch.setattr(report_mod, "rewrite_report", rewrite)
    monkeypatch.setattr(report_mod, "render_preview", lambda state: _preview_result(9))
    monkeypatch.setattr(report_mod, "persist_report",
                        lambda state: persisted.append(True) or {"report_path": "report.md"})

    out = report_mod.finalize_report(_state(invalid))

    assert out["report_quality"].passed is True
    assert out["report_retry_round"] == 1
    assert persisted == [True]


def test_quality_rewrite_loop_stops_after_shared_retry_limit(monkeypatch):
    persisted = []
    invalid = _valid_draft().replace("## 6. 한계와 정보 공백", "### 검토 메모")

    def rewrite(state):
        return {"report_retry_round": state["report_retry_round"] + 1}

    monkeypatch.setattr(report_mod, "rewrite_report", rewrite)
    monkeypatch.setattr(report_mod, "persist_report",
                        lambda state: persisted.append(True) or {"report_path": "report.md"})

    out = report_mod.finalize_report(_state(invalid))

    assert out["report_quality"].passed is False
    assert out["report_quality"].retry_kind is None
    assert out["report_retry_round"] == 2
    assert any("재시도 한도" in issue for issue in out["report_quality"].issues)
    assert persisted == [] and "report_path" not in out


def test_report_fails_after_maximum_compression_rounds(monkeypatch):
    persisted = []
    monkeypatch.setattr(report_mod, "render_preview", lambda state: _preview_result(11))
    monkeypatch.setattr(report_mod, "persist_report",
                        lambda state: persisted.append(True) or {"report_path": "report.md"})

    out = report_mod.finalize_report(_state())

    assert out["report_quality"].passed is False
    assert out["report_quality"].retry_kind is None
    assert out["report_retry_round"] == 2
    assert any("압축 최대 횟수" in issue for issue in out["report_quality"].issues)
    assert persisted == [] and "report_path" not in out


def test_invalid_evidence_id_fails_quality_check():
    state = _state(_valid_draft().replace("EVIDENCE:e1", "EVIDENCE:missing"))

    quality = evaluate_report(state)["report_quality"]

    assert quality.passed is False
    assert any("유효하지 않은 Evidence ID" in issue for issue in quality.issues)


def test_missing_required_section_fails_quality_check():
    state = _state(_valid_draft().replace("## 6. 한계와 정보 공백", "### 검토 메모"))

    quality = evaluate_report(state)["report_quality"]

    assert quality.passed is False
    assert any("필수 섹션 누락" in issue for issue in quality.issues)


def test_assertive_superiority_expression_is_forbidden():
    state = _state(_valid_draft() + "\nTurboQuant가 더 우수하다.\n")

    quality = evaluate_report(state)["report_quality"]

    assert quality.passed is False
    assert "금지된 표현: 단정적 우열" in quality.issues


def test_unsupported_number_fails_quality_check():
    state = _state(_valid_draft() + "\n처리량이 99% 개선됐다.\n")

    quality = evaluate_report(state)["report_quality"]

    assert quality.passed is False
    assert any("근거에서 확인되지 않은 수치: 99" in issue for issue in quality.issues)


def test_compression_preserves_required_decision_context():
    protected = "조건 차이 / 상충 지점 / 정보 공백 / 한계 / 적용 시사점 / EVIDENCE:e1"
    table = ("| DOM-3 | 3 / medium | EVIDENCE:e1 | 동일 GPU 조건 | "
             "핵심 판단이다. 반복되는 상세 설명은 뒤에 이어진다. |")
    state = _state(_valid_draft() + f"\n{protected}\n{table}\n")

    state.update(compress_report(state))
    state.update(compress_report(state))

    for term in ("조건 차이", "상충 지점", "정보 공백", "한계", "적용 시사점", "EVIDENCE:e1"):
        assert term in state["report_draft"]
    assert "핵심 판단이다." in state["report_draft"]
    assert "반복되는 상세 설명" not in state["report_draft"]


def test_preview_is_not_final_report_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("KV_OUTPUT_DIR", str(tmp_path))
    state = _state()
    state.update(evaluate_report(state))

    out = pdf_mod.render_preview(state)

    preview = pdf_mod.preview_path(state)
    assert out["report_quality"].passed is True
    assert out["report_page_count"] == pdf_mod.count_pages(preview)
    assert preview.exists()
    assert not (tmp_path / "quality" / "report.pdf").exists()


def test_synthesize_draft_does_not_write_report_files(tmp_path, monkeypatch):
    monkeypatch.setenv("KV_OUTPUT_DIR", str(tmp_path))
    state = {
        "run_id": "draft-only",
        "technologies": [
            {"tech_id": "turboquant", "name": "TurboQuant"},
            {"tech_id": "cxl_pnm", "name": "CXL-PNM"},
        ],
        "domain": {"name": "LLM Serving", "workload": "inference", "scenarios": ["production"]},
        "rubrics": {
            "meta": {"title": "평가", "core_question": "적용 가능한가?"},
            "criteria": [{"id": "DOM-3", "name": "처리량"}],
            "comparison_pairs": {"pairs": []},
        },
        "evidence_pool": [_evidence()],
        "final_results": [_result()],
        "trl_results": [],
        "conflicts": [],
        "info_gaps": [],
    }

    out = report_mod.synthesize_draft(state)

    assert out["report_draft"].startswith("# 평가")
    assert "검색 로그" not in out["report_draft"] and "루브릭 전문" not in out["report_draft"]
    assert not (tmp_path / "draft-only" / "report.md").exists()
    assert not (tmp_path / "draft-only" / "report.pdf").exists()
