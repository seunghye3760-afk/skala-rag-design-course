import pytest


@pytest.fixture(autouse=True)
def tmp_output(tmp_path, monkeypatch):
    monkeypatch.setenv("KV_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("KV_FAKE_EVIDENCE", "1")   # 테스트는 네트워크·LLM 없이 가짜 근거로 (collect_evidence)
    monkeypatch.setenv("KV_FAKE", "1")            # tech_research·score_task·synthesize도 가짜로
