"""orchestrator/state.py reducer 3종: 병렬 worker가 동시에 써도 유실·덮어쓰기 오류가 없어야 한다."""
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from kv_eval.orchestrator.state import SubTask, merge_dict, merge_tasks


def _t(i, status="pending"):
    return SubTask(task_id=f"turboquant:TRL-{i}:r0", tech_id="turboquant", criterion_id=f"TRL-{i}",
                   channels=["web"], status=status)


def test_merge_tasks_overwrites_by_id_and_keeps_order():
    left = [_t(1), _t(2), _t(3)]
    out = merge_tasks(left, [_t(2, "done")])
    assert [t.task_id for t in out] == [t.task_id for t in left]      # 순서 유지
    assert [t.status for t in out] == ["pending", "done", "pending"]  # id 기준 최신 덮어쓰기
    assert merge_tasks(None, [_t(9)])[0].task_id == "turboquant:TRL-9:r0"


def test_merge_dict_right_wins():
    assert merge_dict({"a": "pending", "b": "pending"}, {"a": "done"}) == {"a": "done", "b": "pending"}
    assert merge_dict(None, {"x": 1}) == {"x": 1}


def test_parallel_writes_are_not_lost():
    """Send로 띄운 worker 5개가 각자 자기 키만 반환해도 reducer가 전부 합친다 (동시 쓰기 유실 없음)."""

    class S(TypedDict, total=False):
        task_plan: Annotated[list[SubTask], merge_tasks]
        node_status: Annotated[dict[str, str], merge_dict]
        step_count: Annotated[int, operator.add]

    def plan(state):
        return {"task_plan": [_t(i) for i in range(5)], "node_status": {_t(i).task_id: "pending" for i in range(5)}}

    def fan(state):
        return [Send("worker", t) for t in state["task_plan"]]

    def worker(t: SubTask):
        return {"task_plan": [t.model_copy(update={"status": "done"})], "node_status": {t.task_id: "done"},
                "step_count": 1}

    g = StateGraph(S)
    g.add_node("plan", plan)
    g.add_node("worker", worker)
    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", fan, ["worker"])
    g.add_edge("worker", END)
    final = g.compile().invoke({"step_count": 0})
    assert len(final["task_plan"]) == 5 and all(t.status == "done" for t in final["task_plan"])
    assert set(final["node_status"].values()) == {"done"} and len(final["node_status"]) == 5
    assert final["step_count"] == 5
