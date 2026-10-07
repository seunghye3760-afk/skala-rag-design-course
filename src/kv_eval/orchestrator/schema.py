"""Orchestrator 계획 계약 (Agent 과제 — Orchestrator-Workers).

팀 공통 계약(graph/task_schema.py)은 건드리지 않고, 계획·Worker 관련 형식만 여기에 둔다.

- Worker 1개 = Send 1건 = 셀(기술 × 항목) × 채널.
  계획 노드가 셀마다 어떤 채널을 쓸지 정하므로 Worker 수는 실행 시점에 결정된다 (Dynamic Fan-out).
- 모든 쿼리는 pro/con 한 쌍(QueryPair) — 유리한 근거만 찾는 확증편향을 스키마에서 막는다.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..graph.task_schema import AgentType, TechId

Channel = Literal["rag", "web", "open_source"]
# rag: 논문 코퍼스(FAISS+BM25) / web: Tavily 일반 검색 / open_source: 저장소·이슈 트래커 한정 웹 검색
SubTaskStatus = Literal["done", "excluded"]


class QueryPair(BaseModel):
    pro: str                       # 긍정 근거 쿼리
    con: str                       # 비판 근거 쿼리 (항상 쌍으로)


class SubTask(BaseModel):
    """Worker 1개의 작업 명세. Orchestrator가 만들고 State.plan에 저장된다."""
    subtask_id: str                # "r0:turboquant:MKT-2:web"
    round: int
    tech_id: TechId
    criterion_id: str
    agent_type: AgentType          # 관점 커버리지 검사용
    channel: Channel
    queries: list[QueryPair] = Field(min_length=1, max_length=4)
    priority: Literal["high", "normal"] = "normal"
    hint: str | None = None        # 재계획 때 품질 미달 사유에서 온 힌트
    reason: str                    # 이 채널·쿼리를 고른 이유 (결정 사유)


class Plan(BaseModel):
    """Orchestrator 출력. 현재 라운드 계획만 State에 두고, 이전 계획은 외부 로그로 보낸다."""
    plan_id: str                   # "{run_id}:r{round}"
    round: int
    mode: Literal["initial", "replan"]
    subtasks: list[SubTask]
    rationale: str                 # 계획 전체 요약 사유
    guard_notes: list[str] = Field(default_factory=list)   # 코드 가드가 고친 내용

    def cells(self) -> list[tuple[str, str]]:
        """계획에 포함된 (tech_id, criterion_id) — 순서 유지, 중복 제거."""
        return list(dict.fromkeys((s.tech_id, s.criterion_id) for s in self.subtasks))


class WorkerInput(BaseModel):
    """Send 전달값. Worker는 이 입력만 보고 일한다 (다른 Worker와 통신하지 않음)."""
    subtask: SubTask
    tech: dict
    criterion: dict


class WorkerOutcome(BaseModel):
    """Worker 실행 결과의 제어 메타. 근거 본문은 evidence_pool로 따로 간다 (제어 vs 페이로드 분리)."""
    subtask_id: str
    round: int
    status: SubTaskStatus          # done | excluded(재시도 소진 → 제외하고 계속)
    attempts: int
    evidence_count: int
    error: str | None = None
