"""균형 점검 (설계서 D-9 (2)) 판정 조건 9종. 담당 4."""
from kv_eval.graph.task_schema import CriterionResult, Evidence, EvidenceBrief, Locator
from kv_eval.rules.balance import find_issues


def _ev(criterion_id="DOM-4", grade="B", stance="pro", claim="claim", conditions="A100, 4k ctx",
       source_title="S", publisher="P", excerpt="excerpt", eid=None):
    return Evidence(evidence_id=eid or f"{grade}{stance}{source_title}", tech_id="turboquant",
                    criterion_id=criterion_id, claim=claim, source_title=source_title, publisher=publisher,
                    accessed_at="2026-09-22", source_type="논문", evidence_grade=grade, stance=stance,
                    measurement_type="실측", conditions=conditions, excerpt=excerpt, locator=Locator())


def _state(evidence, results=()):
    rub = {"criteria": [{"id": "DOM-4", "agent": "domain"}], "comparison_pairs": {"pairs": []}}
    return {"technologies": [{"tech_id": "turboquant"}], "rubrics": rub, "only_criteria": ["DOM-4"],
           "evidence_pool": list(evidence), "criterion_results": list(results)}


def _reasons(state):
    return {i.reason for i in find_issues(state)}


def test_no_evidence():
    issues = find_issues(_state([]))
    assert len(issues) == 1 and issues[0].kind == "research" and "0건" in issues[0].reason


def test_single_evidence():
    issues = find_issues(_state([_ev(stance="pro")]))
    assert "1건뿐" in issues[0].reason


def test_one_sided_bias():
    issues = find_issues(_state([_ev(stance="pro", source_title="A"), _ev(stance="pro", source_title="B")]))
    assert issues[0].kind == "research" and "비판 근거 없음" in issues[0].reason


def test_worst_grade_only():
    ev = [_ev(grade="D", stance="pro", source_title="A"), _ev(grade="D", stance="con", source_title="B")]
    reasons = _reasons(_state(ev))
    assert any("최고 등급이 D뿐" in r for r in reasons)


def test_single_source():
    ev = [_ev(stance="pro", source_title="같은책", publisher="같은출판사"),
         _ev(stance="con", source_title="같은책", publisher="같은출판사")]
    reasons = _reasons(_state(ev))
    assert any("단일 출처" in r for r in reasons)


def test_conditions_missing():
    ev = [_ev(stance="pro", source_title="A", claim="42% 개선", conditions=None),
         _ev(stance="con", source_title="B", conditions="A100")]
    reasons = _reasons(_state(ev))
    assert any("조건 누락" in r for r in reasons)


def _balanced_evidence():
    return [_ev(stance="pro", source_title="A", grade="B", conditions="A100"),
           _ev(stance="con", source_title="B", grade="B", conditions="A100")]


def _result(rationale, score=3, evidence=None, intra_conflict=False):
    ev = evidence or _balanced_evidence()
    briefs = [EvidenceBrief(evidence_id=e.evidence_id, claim=e.claim, source=e.source_title,
                            date=e.published_at, grade=e.evidence_grade, stance=e.stance) for e in ev]
    return CriterionResult(tech_id="turboquant", criterion_id="DOM-4", agent_type="domain", score=score,
                           raw_score=score, rationale=rationale, confidence="medium",
                           evidence=briefs, intra_conflict=intra_conflict)


def test_no_result_yet():
    issues = find_issues(_state(_balanced_evidence()))
    assert issues[0].kind == "rescore" and "채점 결과 없음" in issues[0].reason


def test_na_with_evidence():
    r = _result("근거 부족", score="NA")
    issues = find_issues(_state(_balanced_evidence(), [r]))
    assert "근거가 있는데 NA" in issues[0].reason


def test_score_without_evidence():
    r = CriterionResult(tech_id="turboquant", criterion_id="DOM-4", agent_type="domain", score=3,
                        raw_score=3, rationale="이유", confidence="low", evidence=[])
    issues = find_issues(_state(_balanced_evidence(), [r]))
    assert "근거 인용 없이 점수" in issues[0].reason


def test_unsupported_number_flagged():
    ev = _balanced_evidence()      # excerpt == "excerpt" (숫자 없음)
    r = _result("42% 개선했다고 판단해 3점. 4점을 주지 않은 이유는 조건 불명확.", evidence=ev)
    issues = find_issues(_state(ev, [r]))
    assert any("excerpt에 없음" in i.reason for i in issues)


def test_unsupported_number_passes_when_in_excerpt():
    ev = [_ev(stance="pro", source_title="A", grade="B", excerpt="측정 결과 42% 개선"),
         _ev(stance="con", source_title="B", grade="B", excerpt="비판 측 근거")]
    r = _result("42% 개선을 확인해 3점. 4점을 주지 않은 이유는 조건 불명확.", evidence=ev)
    issues = find_issues(_state(ev, [r]))
    assert not any("excerpt에 없음" in i.reason for i in issues)


def test_next_score_reason_missing():
    ev = _balanced_evidence()
    r = _result("그냥 3점으로 한다.", evidence=ev)      # "4"(다음 점수) 언급 없음
    issues = find_issues(_state(ev, [r]))
    assert any("바로 위 점수" in i.reason for i in issues)


def test_next_score_reason_present_passes():
    ev = _balanced_evidence()
    r = _result("근거가 이 정도라 3점. 4점을 주지 않은 이유는 독립 검증이 없어서.", evidence=ev)
    issues = find_issues(_state(ev, [r]))
    assert not any("바로 위 점수" in i.reason for i in issues)


def test_intra_conflict_missing_when_strong_evidence_opposes():
    ev = [_ev(stance="pro", source_title="A", grade="A", excerpt="1"),
         _ev(stance="con", source_title="B", grade="B", excerpt="1")]
    r = _result("3점. 4점을 주지 않은 이유: 상충.", evidence=ev, intra_conflict=False)
    issues = find_issues(_state(ev, [r]))
    assert any("intra_conflict" in i.reason for i in issues)


def test_intra_conflict_true_passes():
    ev = [_ev(stance="pro", source_title="A", grade="A", excerpt="1"),
         _ev(stance="con", source_title="B", grade="B", excerpt="1")]
    r = _result("3점. 4점을 주지 않은 이유: 상충.", evidence=ev, intra_conflict=True)
    issues = find_issues(_state(ev, [r]))
    assert not any("intra_conflict" in i.reason for i in issues)


def test_clean_result_has_no_issues():
    ev = [_ev(stance="pro", source_title="A", grade="B", excerpt="측정 결과 42% 개선"),
         _ev(stance="con", source_title="B", grade="C", excerpt="비판 측 근거")]
    r = _result("42% 개선을 확인해 3점. 4점을 주지 않은 이유는 조건 불명확.", evidence=ev)
    assert find_issues(_state(ev, [r])) == []
