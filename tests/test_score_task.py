"""score_task 노드: KV_FAKE 우회 + 형식 오류 1회 재요청 (설계서 D-10). 담당 4."""
from kv_eval.agents import score_task as st_mod
from kv_eval.graph.task_schema import CriterionResult, ScoreTask


def _task():
    return ScoreTask(tech={"tech_id": "turboquant", "name": "TurboQuant"}, agent_type="market",
                     criterion_ids=["MKT-1", "MKT-2"], evidence=[], round=0)


def test_kv_fake_bypasses_real_agent(monkeypatch):
    monkeypatch.setenv("KV_FAKE", "1")
    calls = []
    monkeypatch.setattr(st_mod, "AGENTS", {"market": type("M", (), {
        "score": staticmethod(lambda task, rub: calls.append(1) or [])})()})
    out = st_mod.score_task(_task())
    assert calls == []                      # 진짜 에이전트는 호출되지 않는다
    assert len(out["criterion_results"]) == 2
    assert all(r.rationale == "[FAKE] 미구현" for r in out["criterion_results"])


def test_success_on_first_try(monkeypatch):
    monkeypatch.delenv("KV_FAKE", raising=False)
    task = _task()
    good = [CriterionResult(tech_id="turboquant", criterion_id=cid, agent_type="market", score=3,
                            rationale="ok", confidence="low") for cid in task.criterion_ids]
    monkeypatch.setattr(st_mod, "AGENTS", {"market": type("M", (), {
        "score": staticmethod(lambda t, rub, _r=[good]: _r.pop())})()})
    out = st_mod.score_task(task)
    assert out["criterion_results"] == good


def test_retries_once_then_succeeds(monkeypatch):
    monkeypatch.delenv("KV_FAKE", raising=False)
    task = _task()
    good = [CriterionResult(tech_id="turboquant", criterion_id=cid, agent_type="market", score=3,
                            rationale="ok", confidence="low") for cid in task.criterion_ids]
    calls = {"n": 0}

    def flaky(t, rub):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("JSON 스키마 불일치")
        return good

    monkeypatch.setattr(st_mod, "AGENTS", {"market": type("M", (), {"score": staticmethod(flaky)})()})
    out = st_mod.score_task(task)
    assert calls["n"] == 2 and out["criterion_results"] == good


def test_falls_back_to_na_after_second_failure(monkeypatch):
    monkeypatch.delenv("KV_FAKE", raising=False)
    task = _task()
    calls = {"n": 0}

    def always_fails(t, rub):
        calls["n"] += 1
        raise ValueError("계속 실패")

    monkeypatch.setattr(st_mod, "AGENTS", {"market": type("M", (), {"score": staticmethod(always_fails)})()})
    out = st_mod.score_task(task)
    assert calls["n"] == 2                  # 1회 재요청까지만, 무한 재시도 아님
    assert len(out["criterion_results"]) == 2
    assert all(r.score == "NA" and r.confidence == "low" for r in out["criterion_results"])
