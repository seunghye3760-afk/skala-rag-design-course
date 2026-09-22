import pytest


@pytest.fixture(autouse=True)
def tmp_output(tmp_path, monkeypatch):
    monkeypatch.setenv("KV_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("KV_FAKE", "1")   # 실제 LLM·인덱스 없이 뼈대만 확인 (tech_research 등)
