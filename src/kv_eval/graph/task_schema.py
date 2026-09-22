"""팀 공통 계약(데이터 형식). 설계서 ver1.4 D-2~D-5 기준.

⚠ 이 파일은 4명이 모두 기대는 계약이다. 수정은 `contract` 라벨 PR로만 하고,
  통합 담당의 승인을 받는다. 필드를 지우거나 이름을 바꾸면 다른 사람 코드가 깨진다.

흐름: rag/ → Chunk(locator 포함) → evidence/ → Evidence → agents/ → CriterionResult
      → rules/ (상한·TRL·상충 후보) → reporting/
"""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field

TechId = Literal["turboquant", "cxl_pnm"]
AgentType = Literal["trl", "market", "stakeholder", "domain"]
Grade = Literal["A", "B", "C", "D"]
Stance = Literal["pro", "con"]
Confidence = Literal["high", "medium", "low"]
Score = Union[int, Literal["NA"]]


def cell_key(tech_id: str, criterion_id: str) -> str:
    """근거 누적·최신 결과 선택의 단위. 예: 'turboquant:DOM-4'"""
    return f"{tech_id}:{criterion_id}"


class Locator(BaseModel):
    """원문 위치. 논문은 doc_id·page·section, 웹은 url·web_section을 채운다."""
    doc_id: str | None = None          # data/corpus_manifest.csv의 doc_id
    version: str | None = None
    page: int | None = None
    section: str | None = None
    figure_table: str | None = None    # 예: "Table 3", "Fig. 5"
    chunk_id: str | None = None
    url: str | None = None
    web_section: str | None = None


class Chunk(BaseModel):
    """검색 담당(rag/) → 근거 담당(evidence/) 전달 단위."""
    chunk_id: str
    text: str
    locator: Locator
    score: float | None = None


class Evidence(BaseModel):
    """설계서 D-4. 원문을 열어 확인한 근거만 만든다(검색 요약으로 claim 작성 금지)."""
    evidence_id: str
    tech_id: TechId
    criterion_id: str
    claim: str                         # 조건 포함 주장 요약
    source_title: str
    source_url: str | None = None
    publisher: str
    published_at: str | None = None    # YYYY-MM-DD
    accessed_at: str                   # 확인일 YYYY-MM-DD
    source_type: str                   # 논문, 공식 문서, 저장소, 보도자료, 독립 벤치마크 …
    evidence_grade: Grade              # 설계서 C-4 근거 등급
    stance: Stance
    measurement_type: str              # 실측, 시뮬레이션, 비용 모델, 의견 …
    conditions: str | None = None      # 모델, 문맥 길이, 배치, 하드웨어, 설정
    excerpt: str                       # 판단에 사용한 원문 문장
    locator: Locator
    relevant: bool = True
    duplicate_group: str | None = None
    round: int = 0


class EvidenceBrief(BaseModel):
    """채점 결과에 인용하는 근거 요약 (설계서 D-5)."""
    evidence_id: str
    claim: str
    source: str
    date: str | None = None
    grade: Grade
    stance: Stance


class CriterionResult(BaseModel):
    """설계서 D-5 항목별 채점 결과. score_task(LLM)가 만들고 apply_rules(코드)가 확정한다."""
    tech_id: TechId
    criterion_id: str
    agent_type: AgentType
    round: int = 0
    score: Score                       # LLM 점수 → apply_rules 후 확정 점수
    raw_score: Score | None = None     # apply_rules가 LLM 원점수를 옮겨 적음
    evidence: list[EvidenceBrief] = Field(default_factory=list)
    rationale: str                     # 점수 근거 + 바로 위 점수를 주지 않은 이유
    confidence: Confidence
    cap_applied: str | None = None
    intra_conflict: bool = False


class RetryTarget(BaseModel):
    """균형 점검이 고른 재시도 대상. kind로 재검색/재채점만을 구분한다."""
    tech_id: TechId
    criterion_id: str
    kind: Literal["research", "rescore"]
    reason: str
    hint: str | None = None            # 예: "비판 근거 부족"


class CollectTask(BaseModel):
    """설계서 D-3 수집 작업 (Send 전달값). 항목 1 × 기술 1."""
    tech: dict
    criterion: dict
    round: int = 0
    rewrite_hint: str | None = None
    query_rewrite_count: int = 0


class ScoreTask(BaseModel):
    """설계서 D-3 채점 작업 (Send 전달값). 관점 1 × 기술 1."""
    tech: dict
    agent_type: AgentType
    criterion_ids: list[str]
    evidence: list[Evidence]           # 누적 근거 (모든 round, 중복 제거)
    tech_brief: dict = Field(default_factory=dict)   # 배경 참고용, 근거로 쓰지 않음
    round: int = 0


class TRLResult(BaseModel):
    tech_id: TechId
    trl_level: int
    trl_confidence: Confidence
    gate_trace: list[str]
    component_maturity: str            # "TRL-5 점수/5"
    note: str = "공개 정보 기반 추정, 하한"


class ConflictCandidate(BaseModel):
    """점수 차 2 이상 = 상충 '후보'. 해석은 종합 단계에서 근거·조건을 대조해 채운다."""
    tech_id: TechId
    comparison_id: str
    status: Literal["candidate", "info_gap"]
    score_gap: int | None = None
    evidence_refs: list[str]
    interpretation: str | None = None  # 관점 간 상충 / 조건 차이 / 근거 부족
