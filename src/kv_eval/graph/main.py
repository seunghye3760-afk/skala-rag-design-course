"""Main Graph — Orchestrator-Workers 패턴.

tech_research → plan_tasks(Orchestrator) ─Send×N(계획이 결정)→ run_subtask(Worker) → evidence_join
→ 채점 fan-out → balance_check ─┬ 근거 문제 → plan_tasks (해당 셀만 재계획)
                                ├ 형식 오류만 → score_dispatch
                                └ 통과/한도 소진 → apply_rules → synthesize_report(Synthesizer)
→ quality_eval ─┬ 통과 / 한도 소진 → END
                ├ 근거 문제 셀 → plan_tasks (재계획)
                └ 서술 문제만 → synthesize_report (지적 사항 반영 재작성)
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .. import progress
from ..agents.score_task import score_task
from ..agents.tech_research import tech_research
from ..config import rubrics, technologies_config
from ..quality.evaluate import quality_eval, route_after_quality
from ..reporting.report import synthesize_report
from ..orchestrator.planner import fan_out_workers, plan_tasks
from ..orchestrator.workers import run_subtask
from ..rules.apply import apply_rules
from ..rules.balance import balance_check, route_after_balance
from .dispatch import evidence_join, fan_out_score, score_dispatch, score_join
from .state import MainState


def load_config(state: MainState) -> dict:
    progress.step("load_config", "기술·도메인 설정 로드")
    cfg = technologies_config()
    return {"technologies": cfg["technologies"], "domain": cfg["domain"], "retry_round": 0}


def load_rubrics(state: MainState) -> dict:
    progress.step("load_rubrics", "루브릭·규칙 로드")
    return {"rubrics": rubrics()}


def build_graph():
    g = StateGraph(MainState)
    for name, fn in [("load_config", load_config), ("load_rubrics", load_rubrics),
                     ("tech_research", tech_research), ("plan_tasks", plan_tasks),
                     ("run_subtask", run_subtask), ("evidence_join", evidence_join),
                     ("score_dispatch", score_dispatch), ("score_task", score_task),
                     ("score_join", score_join), ("balance_check", balance_check),
                     ("apply_rules", apply_rules), ("synthesize_report", synthesize_report),
                     ("quality_eval", quality_eval)]:
        g.add_node(name, fn)
    g.add_edge(START, "load_config")
    g.add_edge("load_config", "load_rubrics")
    g.add_edge("load_rubrics", "tech_research")
    g.add_edge("tech_research", "plan_tasks")
    g.add_conditional_edges("plan_tasks", fan_out_workers, ["run_subtask", "evidence_join"])
    g.add_edge("run_subtask", "evidence_join")
    g.add_edge("evidence_join", "score_dispatch")
    g.add_conditional_edges("score_dispatch", fan_out_score, ["score_task"])
    g.add_edge("score_task", "score_join")
    g.add_edge("score_join", "balance_check")
    g.add_conditional_edges("balance_check", route_after_balance,
                            ["plan_tasks", "score_dispatch", "apply_rules"])
    g.add_edge("apply_rules", "synthesize_report")
    g.add_edge("synthesize_report", "quality_eval")
    g.add_conditional_edges("quality_eval", route_after_quality,
                            ["plan_tasks", "synthesize_report", END])
    return g.compile()
