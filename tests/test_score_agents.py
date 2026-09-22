"""담당 2 관점 에이전트(trl·domain) 채점 테스트. LLM은 monkeypatch — 네트워크 없이 돈다."""
import pytest

import kv_eval.llm
from kv_eval.agents import domain, trl
from kv_eval.agents._judge import _CritScore, _ScoreBatch
from kv_eval.config import rubrics
from kv_eval.graph.task_schema import Evidence, Locator, ScoreTask

TECH = {"tech_id": "turboquant", "name": "TurboQuant", "group": "SW",
        "query_names": ["TurboQuant"], "category": "KV cache quantization"}


def _ev(eid, cid, grade="B", stance="pro"):
    return Evidence(evidence_id=eid, tech_id="turboquant", criterion_id=cid,
                    claim="Llama-70B 128K에서 동시 세션 1.8배", source_title="실측기",
                    publisher="ExampleLab", published_at="2026-02-03", accessed_at="2026-09-22",
                    source_type="공식 기술 블로그", evidence_grade=grade, stance=stance,
                    measurement_type="실측", conditions="Llama-70B, 128K",
                    excerpt="동시 세션 수가 1.8배 늘었다.", locator=Locator(url="https://a.com/1"))


class _FakeLLM:
    def __init__(self, batch):
        self.batch, self.prompts = batch, []

    def with_structured_output(self, schema):
        return self

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return self.batch


@pytest.fixture
def real_scoring(monkeypatch):
    monkeypatch.setenv("KV_FAKE_EVIDENCE", "0")   # conftest의 가짜 채점 플래그 해제


def test_domain_maps_llm_output_and_na_without_llm(real_scoring, monkeypatch):
    fake = _FakeLLM(_ScoreBatch(results=[_CritScore(
        criterion_id="DOM-3", score="4", evidence_ids=["e1", "ghost"],
        rationale="동시 세션 1.8배 실측. 5점은 처리량 동반 1.5배 근거가 없어 주지 않음.",
        confidence="medium", cap_applied=None, intra_conflict=False)]))
    monkeypatch.setattr(kv_eval.llm, "chat_model", lambda: fake)

    task = ScoreTask(tech=TECH, agent_type="domain", criterion_ids=["DOM-3", "DOM-4"],
                     evidence=[_ev("e1", "DOM-3"), _ev("e2", "DOM-3", grade="D", stance="con")])
    out = domain.score(task, rubrics())

    assert [r.criterion_id for r in out] == ["DOM-3", "DOM-4"]     # 요청 순서 유지
    r3, r4 = out
    assert r3.score == 4 and r3.agent_type == "domain" and r3.tech_id == "turboquant"
    assert [b.evidence_id for b in r3.evidence] == ["e1"]          # 목록 밖 id(ghost)는 버림
    assert r3.evidence[0].grade == "B" and r3.evidence[0].source == "실측기"
    assert r4.score == "NA" and "정보 공백" in r4.rationale        # 근거 0건은 LLM 없이 NA
    assert len(fake.prompts) == 1 and "### DOM-4" not in fake.prompts[0]   # 근거 0건 항목은 채점 요청 안 함


def test_prompt_contains_rubric_addon_and_evidence(real_scoring, monkeypatch):
    fake = _FakeLLM(_ScoreBatch(results=[]))
    monkeypatch.setattr(kv_eval.llm, "chat_model", lambda: fake)

    task = ScoreTask(tech=TECH, agent_type="domain", criterion_ids=["DOM-3"],
                     evidence=[_ev("e1", "DOM-3")])
    domain.score(task, rubrics())
    p = fake.prompts[0]
    assert "TurboQuant" in p and "도메인 평가 에이전트" in p        # {tech}·{agent_title} 치환
    assert "같은 GPU 수 기준" in p                                  # 루브릭 본문
    assert "검증 범위 밖" in p                                       # prompts/score/domain.md 포함
    assert "(e1) [B|pro]" in p and "동시 세션 수가 1.8배" in p       # 근거 목록·발췌


def test_missing_criterion_in_llm_response_marked_for_rescore(real_scoring, monkeypatch):
    monkeypatch.setattr(kv_eval.llm, "chat_model", lambda: _FakeLLM(_ScoreBatch(results=[])))
    task = ScoreTask(tech=TECH, agent_type="domain", criterion_ids=["DOM-3"],
                     evidence=[_ev("e1", "DOM-3")])
    out = domain.score(task, rubrics())
    assert out[0].score == "NA" and "재채점" in out[0].rationale


def test_trl_uses_ordinal_scale_addon(real_scoring, monkeypatch):
    fake = _FakeLLM(_ScoreBatch(results=[_CritScore(
        criterion_id="TRL-1", score="3", evidence_ids=["e1"],
        rationale="GPU 1대 표준 벤치마크 실측. 4점은 서빙 엔진 통합 측정 근거가 없어 주지 않음.",
        confidence="medium")]))
    monkeypatch.setattr(kv_eval.llm, "chat_model", lambda: fake)

    task = ScoreTask(tech=TECH, agent_type="trl", criterion_ids=["TRL-1"],
                     evidence=[_ev("e1", "TRL-1")])
    out = trl.score(task, rubrics())
    assert out[0].score == 3 and out[0].agent_type == "trl"
    assert "서열 척도" in fake.prompts[0]                            # trl judge_addon 포함
    assert "gate_trace" in fake.prompts[0]                           # prompts/score/trl.md 포함
