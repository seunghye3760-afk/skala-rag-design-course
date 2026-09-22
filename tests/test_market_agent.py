"""market.score()를 실LLM 호출 없이 검증 (evidence_id 조회, 할루시네이션 인용 방어)."""
from kv_eval.agents import market as market_mod
from kv_eval.graph.task_schema import Evidence, Locator, ScoreTask


class _FakeLLM:
    def __init__(self, batch):
        self._batch = batch

    def invoke(self, _messages):
        return self._batch


def _evidence(cid, eid, grade="A", stance="pro", tech_id="turboquant"):
    return Evidence(
        evidence_id=eid, tech_id=tech_id, criterion_id=cid,
        claim=f"claim for {eid}", source_title="Gartner Report 2026", source_url="https://example.com/gartner",
        publisher="Gartner", published_at="2026-01-15", accessed_at="2026-09-22",
        source_type="애널리스트 보고서", evidence_grade=grade, stance=stance,
        measurement_type="의견", excerpt="...", locator=Locator(url="https://example.com/gartner"),
    )


def _task(criterion_ids=("MKT-1", "MKT-2"), evidence=None):
    return ScoreTask(
        tech={"tech_id": "turboquant", "name": "TurboQuant"},
        agent_type="market",
        criterion_ids=list(criterion_ids),
        evidence=evidence or [_evidence("MKT-1", "ev_1")],
    )


def test_score_resolves_evidence_ids(monkeypatch):
    batch = market_mod._LLMScoreBatch(items=[
        market_mod._LLMCriterionScore(
            criterion_id="MKT-1", score=3, evidence_ids=["ev_1"],
            rationale="기관·연도 명시 부족해 3점, 4점은 아님", confidence="medium",
        ),
        market_mod._LLMCriterionScore(
            criterion_id="MKT-2", score="NA", evidence_ids=[],
            rationale="관련 근거 없음", confidence="low",
        ),
    ])
    monkeypatch.setattr(market_mod, "_get_llm", lambda: _FakeLLM(batch))

    results = market_mod.score(_task(), rubrics=_dummy_rubrics())

    mkt1 = next(r for r in results if r.criterion_id == "MKT-1")
    assert mkt1.score == 3
    assert mkt1.tech_id == "turboquant"
    assert mkt1.agent_type == "market"
    assert [e.evidence_id for e in mkt1.evidence] == ["ev_1"]
    assert mkt1.evidence[0].grade == "A"

    mkt2 = next(r for r in results if r.criterion_id == "MKT-2")
    assert mkt2.score == "NA"
    assert mkt2.evidence == []


def test_score_drops_hallucinated_evidence_id(monkeypatch):
    batch = market_mod._LLMScoreBatch(items=[
        market_mod._LLMCriterionScore(
            criterion_id="MKT-1", score=4, evidence_ids=["ev_1", "ev_not_real"],
            rationale="근거 충분해 4점, 독립기관 2곳 아니라 5점은 아님", confidence="high",
        ),
    ])
    monkeypatch.setattr(market_mod, "_get_llm", lambda: _FakeLLM(batch))

    results = market_mod.score(_task(criterion_ids=["MKT-1"]), rubrics=_dummy_rubrics())

    mkt1 = results[0]
    assert [e.evidence_id for e in mkt1.evidence] == ["ev_1"]
    assert mkt1.confidence == "low"
    assert "제거되어" in mkt1.rationale


def _dummy_rubrics():
    return {
        "agents": {"market": {"title": "시장성 평가 에이전트", "judge_addon": "addon"}},
        "criteria": [
            {"id": "MKT-1", "name": "목표 시장 규모", "question": "q1",
             "rubric": {"5": {"criterion": "..."}, "NA": {"criterion": "..."}}, "pitfalls": []},
            {"id": "MKT-2", "name": "상용화 채택", "question": "q2",
             "rubric": {"5": {"criterion": "..."}, "NA": {"criterion": "..."}}, "pitfalls": []},
        ],
        "judge": {
            "common_prompt": "{agent_title} {tech} {agent_addon} {criteria_block} {schema}",
            "output_schema": {"criterion_id": "MKT-1"},
        },
    }
