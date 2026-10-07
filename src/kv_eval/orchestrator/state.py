"""Orchestrator-Workers 상위 State (Layered State, 과제 프롬프트 §3).

- 계약 파일(graph/state.py, graph/task_schema.py)은 수정하지 않고 import만 한다.
  기존 노드(tech_research, balance_check, apply_rules, synthesize_report …)는 MainState 키를
  그대로 읽으므로, 이 State는 MainState의 페이로드 키를 전부 포함하고 그 위에 제어 메타를 얹는다.
- "State는 인터페이스이지 컨테이너가 아니다": 보고서 본문·원문·검색 캐시는 파일(outputs/, .cache/)에
  두고 State에는 구조화된 결과와 경로만 둔다.
- 병렬 worker가 쓰는 키는 전부 reducer를 건다 (동시 쓰기 유실 방지).

각 필드 주석의 [태그]는 README State Schema 7항목 중 어디에 해당하는지 표시한다:
  [제어] 제어 메타 / [페이로드] worker 결과 취합 / [관측성] 사유 요약 / [지속성] 크기 상한
  [상관] run_id 상관 키 / [재개] 실패 지점 식별 / [동시성] reducer / [종료] 루프 상한
"""
from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

from ..graph.task_schema import (CollectTask, ConflictCandidate, CriterionResult, Evidence, RetryTarget,
                                 ScoreTask, TRLResult)

Channel = Literal["paper", "web", "open_source"]
TaskStatus = Literal["pending", "done", "failed", "excluded"]
RunStatus = Literal["RUNNING", "SUCCESS", "PARTIAL", "FAILED"]


# ---------- 스키마 (계약 파일 밖, orchestrator 전용) ----------

class SubTask(BaseModel):
    """plan_tasks가 만드는 구조화된 서브태스크 1건 = 셀(기술 × 항목) 1개의 수집 작업."""
    task_id: str  # f"{tech_id}:{criterion_id}:r{round}" — node_status·decision_log 공통 키
    tech_id: str
    criterion_id: str
    channels: list[Channel]  # 계획 단계에서 정한 검색 채널 (paper = 논문 RAG, web = Tavily)
    round: int = 0
    status: TaskStatus = "pending"
    reason: str = ""  # [관측성] 왜 이 채널·이 셀인지 한 줄 요약 (본문은 decision_log.jsonl)
    last_error: str | None = None  # [재개] 실패 지점 식별


class CollectJob(BaseModel):
    """Send로 worker에 넘기는 값. 계약의 CollectTask에 channels를 넣을 수 없어 겉에 한 겹 싼다."""
    task_id: str
    task: CollectTask
    channels: list[Channel]
    run_id: str = ""  # decision_log 경로용 상관 키


class ScoreJob(BaseModel):
    task_id: str
    task: ScoreTask
    run_id: str = ""


class QualityVerdict(BaseModel):
    """evaluate_report(규칙) + judge_node(LLM)의 판정. 라우팅은 하지 않는다 (route_after_quality가 읽기만)."""
    round: int
    rule_pass: dict[str, bool]  # groundedness / neutrality / bias / coverage
    judge_scores: dict[str, int] | None = None  # 규칙 전부 PASS일 때만 채워짐 (1~5)
    passed: bool = False
    failed_items: list[str] = Field(default_factory=list)
    failed_cells: list[str] = Field(default_factory=list)  # "tech_id:criterion_id" — 재수집 대상
    feedback: str = ""  # Generator(synthesize_report)에 주입할 미달 사유
    judge_called: bool = False  # ---------- reducer ----------

def merge_tasks(left: list[SubTask] | None, right: list[SubTask] | None) -> list[SubTask]:
    """task_id 기준 최신 상태 덮어쓰기. 순서는 처음 등장한 순서를 유지한다 (병렬 worker 갱신 안전)."""
    merged: dict[str, SubTask] = {t.task_id: t for t in (left or [])}
    for t in right or []:
        merged[t.task_id] = t
    return list(merged.values())


def merge_dict(left: dict | None, right: dict | None) -> dict:
    """dict 병합 (오른쪽 우선). node_status처럼 worker마다 자기 키 하나만 쓰는 경우에 쓴다."""
    return {**(left or {}), **(right or {})}  # ---------- State ----------

class OrchestratorState(TypedDict, total=False):
    # ===== 제어 메타 (흐름 제어) =====
    run_id: str  # [상관] LangSmith metadata·decision_log·outputs/ 공통 키
    only_criteria: list[str]  # [제어] 작게 돌릴 때 (app_agent.py --criteria)
    task_plan: Annotated[list[SubTask], merge_tasks]  # [제어][동시성] 구조화된 서브태스크 목록
    collect_round: int  # [제어] 현재 수집 라운드 (SubTask.round, Evidence.round)
    score_cells: list[str]  # [제어] 이번 채점 대상 셀 (score_dispatch가 결정)
    score_round: int  # [제어] 현재 채점 라운드 (CriterionResult.round)
    retry_round: int  # [종료] balance_check 전역 카운터 ≤ retry.max_rounds
    retry_targets: list[RetryTarget]  # [제어] balance_check가 고른 재시도 대상
    balance_issues: list[RetryTarget]
    quality_round: int  # [종료] evaluate_report 횟수 ≤ quality.max_rounds
    quality_verdict: QualityVerdict | None  # [관측성] 품질 판정 + 미달 사유 요약
    synthesis_feedback: str  # [제어] 재생성 시 synthesis 프롬프트에 주입할 피드백
    step_count: Annotated[int, operator.add]  # [종료][관측성] 노드 실행 수 (비용 상한 확인용)
    status: RunStatus  # [제어] RUNNING → SUCCESS | PARTIAL | FAILED
    node_status: Annotated[dict[str, str], merge_dict]  # [재개][동시성] task_id → pending|done|failed|excluded
    errors: Annotated[list[dict], operator.add]  # [재개][관측성] {"task_id","node","type","message","ts"}
    last_error: str | None  # [재개] 마지막 실패 요약
    fanout_log: Annotated[list[dict], operator.add]  # [관측성] 라운드별 fan-out 수 (README 동적 처리 근거)
    excluded_gaps: Annotated[list[RetryTarget], operator.add]  # [동시성] fallback으로 제외된 셀 → info_gaps에 합침
    info_gaps: list[RetryTarget]  # [페이로드] balance_check가 한도 소진 후 기록

    # ===== 페이로드 (worker 결과 취합 — 취합 ≠ 종합) =====
    technologies: list[dict]
    domain: dict
    rubrics: dict
    tech_briefs: dict
    evidence_pool: Annotated[list[Evidence], operator.add]  # [지속성][동시성] raw 근거, 압축 금지·누적만 (round·dedup으로 상한)
    search_log: Annotated[list[dict], operator.add]  # [동시성]
    criterion_results: Annotated[list[CriterionResult], operator.add]  # [동시성]
    final_results: list[CriterionResult]
    trl_results: list[TRLResult]
    conflicts: list[ConflictCandidate]
    final_assessment: dict
    report_path: str  # [지속성] 보고서 본문은 파일, State에는 경로만
    report_pages: int | None  # [관측성] PDF 쪽수 (finalize가 기록)
