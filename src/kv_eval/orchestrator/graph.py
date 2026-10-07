"""Orchestrator-Workers 그래프 조립 (과제 프롬프트 §5.1). 기존 graph/main.py는 RAG 실습용으로 그대로 둔다.

load_config → load_rubrics → tech_research → plan_tasks
  →(route_after_plan: pending 있으면 Send×N collect_worker / 없으면 score_dispatch)
  → collect_worker ×N → evidence_join(defer) → score_dispatch →(fan_out_score) score_worker ×M → score_join(defer)
  → balance_check →(route_after_balance: plan_tasks | score_dispatch | apply_rules)
  → apply_rules → synthesize_report → evaluate_report
  →(route_after_evaluate: 규칙 PASS → judge_node / 미달 → route_after_quality)
  judge_node →(route_after_quality: plan_tasks | score_dispatch | synthesize_report | finalize) → finalize → END

분기는 전부 add_conditional_edges + 상태 조건. 종료 상한은 코드 상수(retry.max_rounds, quality.max_rounds,
recursion_limit). 모델이 런타임에 정하는 지점: plan_tasks의 채널 선택(LLM 보조), judge_node의 판정 — 라우팅은
전부 순수 함수.
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from ..agents.tech_research import tech_research
from ..graph.main import load_config, load_rubrics
from ..rules.apply import apply_rules
from ..rules.balance import balance_check, route_after_balance
from .dispatch import (collect_worker, evidence_join, fan_out_score, route_after_plan, score_dispatch,
                       score_join, score_worker)
from .plan import plan_tasks
from .quality import (ROUTES, evaluate_report, finalize, judge_node, route_after_evaluate, route_after_quality,
                      synthesize_node)
from .state import OrchestratorState


def _route_balance(state: OrchestratorState) -> str:
    """rules/balance.py의 라우터를 그대로 쓰되, 재수집은 dispatch_collect가 아니라 plan_tasks로 보낸다."""
    nxt = route_after_balance(state)
    return "plan_tasks" if nxt == "dispatch_collect" else nxt


def _init_control(state: OrchestratorState) -> dict:
    return {"status": "RUNNING", "quality_round": 0, "step_count": 1, "task_plan": [], "node_status": {},
            "errors": [], "fanout_log": [], "excluded_gaps": []}


def build_orchestrator_graph(checkpointer=None):
    g = StateGraph(OrchestratorState)
    nodes = [("init", _init_control), ("load_config", load_config), ("load_rubrics", load_rubrics),
             ("tech_research", tech_research), ("plan_tasks", plan_tasks),
             ("collect_worker", collect_worker), ("score_dispatch", score_dispatch),
             ("score_worker", score_worker), ("balance_check", balance_check), ("apply_rules", apply_rules),
             ("synthesize_report", synthesize_node), ("evaluate_report", evaluate_report),
             ("judge_node", judge_node), ("finalize", finalize)]
    for name, fn in nodes:
        g.add_node(name, fn)
    g.add_node("evidence_join", evidence_join, defer=True)     # 모든 collect 브랜치 완료 후 1회
    g.add_node("score_join", score_join, defer=True)

    g.add_edge(START, "init")
    g.add_edge("init", "load_config")
    g.add_edge("load_config", "load_rubrics")
    g.add_edge("load_rubrics", "tech_research")
    g.add_edge("tech_research", "plan_tasks")
    g.add_conditional_edges("plan_tasks", route_after_plan, ["collect_worker", "score_dispatch"])
    g.add_edge("collect_worker", "evidence_join")
    g.add_edge("evidence_join", "score_dispatch")
    g.add_conditional_edges("score_dispatch", fan_out_score, ["score_worker"])
    g.add_edge("score_worker", "score_join")
    g.add_edge("score_join", "balance_check")
    g.add_conditional_edges("balance_check", _route_balance, ["plan_tasks", "score_dispatch", "apply_rules"])
    g.add_edge("apply_rules", "synthesize_report")
    g.add_edge("synthesize_report", "evaluate_report")
    g.add_conditional_edges("evaluate_report", route_after_evaluate, ["judge_node", *ROUTES])
    g.add_conditional_edges("judge_node", route_after_quality, ROUTES)
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())
