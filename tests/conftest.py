import pytest


@pytest.fixture(autouse=True)
def tmp_output(tmp_path, monkeypatch):
    monkeypatch.setenv("KV_OUTPUT_DIR", str(tmp_path))


@pytest.fixture
def fake_market_and_stakeholder_scoring(monkeypatch):
    """market/stakeholder는 실 LLM 채점으로 구현됐다 (담당 3).

    그래프 스켈레톤 테스트(test_graph_runs, test_retry)는 API 키 없이 항상 통과해야
    하므로, 그 테스트들에서만 이 fixture를 받아 두 에이전트를 가짜 채점으로 되돌린다.
    실제 채점 로직 자체는 tests/test_market_agent.py, tests/test_stakeholder_agent.py에서
    mock LLM으로 직접 검증하므로 여기서 전역(autouse)으로 걸면 그 테스트들이 깨진다.
    TRL/도메인 에이전트가 실채점으로 바뀌면(담당 2) 같은 방식으로 확장하면 된다.
    """
    from kv_eval.agents import market, stakeholder
    from kv_eval.agents._fake import fake_scores

    monkeypatch.setattr(market, "score", lambda task, rubrics: fake_scores(task))
    monkeypatch.setattr(stakeholder, "score", lambda task, rubrics: fake_scores(task))
