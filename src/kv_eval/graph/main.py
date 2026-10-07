"""Orchestrator-Workers를 Supervisor가 제어하는 계층형 LangGraph."""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .. import progress
from ..agents.report_quality import evaluate_report
from ..agents.tech_research import tech_research
from ..config import rubrics, technologies_config
from ..reporting.report import finalize_report, synthesize_report
from ..rules.apply import apply_rules
from ..rules.balance import balance_check
from .dispatch import evidence_join, fan_out_collect, fan_out_score, score_join
from .orchestrator import plan_collect, plan_score
from .state import MainState
from .supervisor import route_supervisor, supervise
from .workers import collect_worker, score_worker


def load_config(state: MainState) -> dict:
    progress.step("load_config", "기술·도메인 설정 로드")
    cfg = technologies_config()
    return {"technologies": cfg["technologies"], "domain": cfg["domain"], "retry_round": 0,
            "supervisor_step": 0, "report_revision": 0, "phase": "initialized"}


def load_rubrics(state: MainState) -> dict:
    progress.step("load_rubrics", "루브릭·규칙 로드")
    return {"rubrics": rubrics()}


def apply_rules_node(state: MainState) -> dict:
    return {**apply_rules(state), "phase": "rules_applied"}


def build_graph(checkpointer=None):
    g = StateGraph(MainState)
    nodes = [
        ("load_config", load_config), ("load_rubrics", load_rubrics),
        ("tech_research", tech_research), ("plan_collect", plan_collect),
        ("collect_worker", collect_worker), ("evidence_join", evidence_join),
        ("plan_score", plan_score), ("score_worker", score_worker), ("score_join", score_join),
        ("balance_check", balance_check), ("supervisor", supervise),
        ("apply_rules", apply_rules_node), ("synthesize_report", synthesize_report),
        ("evaluate_report", evaluate_report), ("finalize_report", finalize_report),
    ]
    for name, fn in nodes:
        g.add_node(name, fn)

    g.add_edge(START, "load_config")
    g.add_edge("load_config", "load_rubrics")
    g.add_edge("load_rubrics", "tech_research")
    g.add_edge("tech_research", "plan_collect")
    g.add_conditional_edges("plan_collect", fan_out_collect, ["collect_worker"])
    g.add_edge("collect_worker", "evidence_join")
    g.add_edge("evidence_join", "plan_score")
    g.add_conditional_edges("plan_score", fan_out_score, ["score_worker"])
    g.add_edge("score_worker", "score_join")
    g.add_edge("score_join", "balance_check")
    g.add_edge("balance_check", "supervisor")
    g.add_edge("apply_rules", "supervisor")
    g.add_edge("synthesize_report", "supervisor")
    g.add_edge("evaluate_report", "supervisor")
    g.add_conditional_edges("supervisor", route_supervisor, {
        "plan_collect": "plan_collect", "plan_score": "plan_score",
        "apply_rules": "apply_rules", "synthesize": "synthesize_report",
        "evaluate": "evaluate_report", "revise": "synthesize_report",
        "finalize": "finalize_report",
    })
    g.add_edge("finalize_report", END)
    return g.compile(checkpointer=checkpointer)
