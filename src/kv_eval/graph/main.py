"""Main Graph (설계서 D-6, 그림 2). 노드 이름은 설계서와 같게 유지한다."""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..agents.score_task import score_task
from ..agents.tech_research import tech_research
from ..config import rubrics, technologies_config
from ..evidence.collect import collect_evidence
from ..reporting.report import synthesize_report
from ..rules.apply import apply_rules
from ..rules.balance import balance_check, route_after_balance
from .dispatch import (dispatch_collect, evidence_join, fan_out_collect, fan_out_score, score_dispatch,
                       score_join)
from .state import MainState


def load_config(state: MainState) -> dict:
    cfg = technologies_config()
    return {"technologies": cfg["technologies"], "domain": cfg["domain"], "retry_round": 0}


def load_rubrics(state: MainState) -> dict:
    return {"rubrics": rubrics()}


def build_graph():
    g = StateGraph(MainState)
    for name, fn in [("load_config", load_config), ("load_rubrics", load_rubrics),
                     ("tech_research", tech_research), ("dispatch_collect", dispatch_collect),
                     ("collect_evidence", collect_evidence), ("evidence_join", evidence_join),
                     ("score_dispatch", score_dispatch), ("score_task", score_task),
                     ("score_join", score_join), ("balance_check", balance_check),
                     ("apply_rules", apply_rules), ("synthesize_report", synthesize_report)]:
        g.add_node(name, fn)
    g.add_edge(START, "load_config")
    g.add_edge("load_config", "load_rubrics")
    g.add_edge("load_rubrics", "tech_research")
    g.add_edge("tech_research", "dispatch_collect")
    g.add_conditional_edges("dispatch_collect", fan_out_collect, ["collect_evidence"])
    g.add_edge("collect_evidence", "evidence_join")
    g.add_edge("evidence_join", "score_dispatch")
    g.add_conditional_edges("score_dispatch", fan_out_score, ["score_task"])
    g.add_edge("score_task", "score_join")
    g.add_edge("score_join", "balance_check")
    g.add_conditional_edges("balance_check", route_after_balance,
                            ["dispatch_collect", "score_dispatch", "apply_rules"])
    g.add_edge("apply_rules", "synthesize_report")
    g.add_edge("synthesize_report", END)
    return g.compile()
