"""stakeholder.score()를 실LLM 호출 없이 검증 (evidence_id 조회, 할루시네이션 인용 방어)."""
from kv_eval.agents import stakeholder as stakeholder_mod
from kv_eval.graph.task_schema import Evidence, Locator, ScoreTask


class _FakeLLM:
    def __init__(self, batch):
        self._batch = batch

    def invoke(self, _messages):
        return self._batch


def _evidence(cid, eid, grade="B", stance="pro", tech_id="cxl_pnm"):
    return Evidence(
        evidence_id=eid, tech_id=tech_id, criterion_id=cid,
        claim=f"claim for {eid}", source_title="Competitor Blog", source_url="https://example.com/competitor",
        publisher="Competitor Inc", published_at="2026-02-01", accessed_at="2026-09-22",
        source_type="보도자료", evidence_grade=grade, stance=stance,
        measurement_type="의견", excerpt="...", locator=Locator(url="https://example.com/competitor"),
    )


def _task(criterion_ids=("STK-1", "STK-3"), evidence=None):
    return ScoreTask(
        tech={"tech_id": "cxl_pnm", "name": "CXL-PNM"},
        agent_type="stakeholder",
        criterion_ids=list(criterion_ids),
        evidence=evidence or [_evidence("STK-1", "ev_1")],
    )


def _dummy_rubrics():
    return {
        "agents": {"stakeholder": {"title": "이해관계자 평가 에이전트", "judge_addon": "addon"}},
        "criteria": [
            {"id": "STK-1", "name": "경쟁 진영 인식", "question": "q1",
             "rubric": {"5": {"criterion": "..."}, "NA": {"criterion": "..."}}, "pitfalls": []},
            {"id": "STK-3", "name": "채택 장벽 인식", "question": "q3",
             "rubric": {"5": {"criterion": "..."}, "NA": {"criterion": "..."}}, "pitfalls": []},
        ],
        "judge": {
            "common_prompt": "{agent_title} {tech} {agent_addon} {criteria_block} {schema}",
            "output_schema": {"criterion_id": "STK-1"},
        },
    }


def test_score_resolves_evidence_ids(monkeypatch):
    batch = stakeholder_mod._LLMScoreBatch(items=[
        stakeholder_mod._LLMCriterionScore(
            criterion_id="STK-1", score=2, evidence_ids=["ev_1"],
            rationale="[경쟁사, 이해당사자] 1차 근거 없어 2점, 1점은 아님", confidence="medium",
        ),
        stakeholder_mod._LLMCriterionScore(
            criterion_id="STK-3", score="NA", evidence_ids=[],
            rationale="관련 근거 없음", confidence="low",
        ),
    ])
    monkeypatch.setattr(stakeholder_mod, "_get_llm", lambda: _FakeLLM(batch))

    results = stakeholder_mod.score(_task(), rubrics=_dummy_rubrics())

    stk1 = next(r for r in results if r.criterion_id == "STK-1")
    assert stk1.score == 2
    assert stk1.tech_id == "cxl_pnm"
    assert stk1.agent_type == "stakeholder"
    assert [e.evidence_id for e in stk1.evidence] == ["ev_1"]

    stk3 = next(r for r in results if r.criterion_id == "STK-3")
    assert stk3.score == "NA"


def test_score_drops_hallucinated_evidence_id(monkeypatch):
    batch = stakeholder_mod._LLMScoreBatch(items=[
        stakeholder_mod._LLMCriterionScore(
            criterion_id="STK-1", score=3, evidence_ids=["ev_1", "ev_ghost"],
            rationale="중립적 언급이라 3점", confidence="medium",
        ),
    ])
    monkeypatch.setattr(stakeholder_mod, "_get_llm", lambda: _FakeLLM(batch))

    results = stakeholder_mod.score(_task(criterion_ids=["STK-1"]), rubrics=_dummy_rubrics())

    stk1 = results[0]
    assert [e.evidence_id for e in stk1.evidence] == ["ev_1"]
    assert stk1.confidence == "low"
    assert "제거되어" in stk1.rationale
