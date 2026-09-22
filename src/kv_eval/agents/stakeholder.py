"""관점 에이전트: stakeholder. 담당 3.
STK-1~4 채점. 근거마다 발언 주체 유형과 이해당사자 여부 표기 (설계서 D-8)."""
from __future__ import annotations

import json

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from ..config import ROOT, criterion, runtime
from ..graph.task_schema import Confidence, CriterionResult, Evidence, EvidenceBrief, Score, ScoreTask

AGENT = "stakeholder"
PROMPT_FILE = "prompts/score/stakeholder.md"


class _LLMCriterionScore(BaseModel):
    criterion_id: str
    score: Score
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="이 점수의 근거가 된 evidence_id 목록. 아래 근거 목록에 있는 evidence_id만 사용.",
    )
    rationale: str = Field(
        description=(
            "점수 근거 2~3문장(인용마다 발언 주체 유형·이해당사자 여부 표기) "
            "+ 바로 위 점수를 주지 않은 이유 1문장"
        )
    )
    confidence: Confidence
    intra_conflict: bool = False


class _LLMScoreBatch(BaseModel):
    items: list[_LLMCriterionScore]


def _criteria_block(rub: dict, criterion_ids: list[str]) -> str:
    blocks = []
    for cid in criterion_ids:
        c = criterion(rub, cid)
        rubric_lines = "\n".join(f"  {lvl}: {info['criterion']}" for lvl, info in c["rubric"].items())
        pitfalls = "\n".join(f"  - {p}" for p in c.get("pitfalls", []))
        block = f"### {c['id']} {c['name']}\n질문: {c['question']}\n점수 기준:\n{rubric_lines}"
        if pitfalls:
            block += f"\n흔한 실수:\n{pitfalls}"
        blocks.append(block)
    return "\n\n".join(blocks)


def _evidence_block(evidence: list[Evidence], criterion_ids: list[str]) -> str:
    rows = [e for e in evidence if e.criterion_id in criterion_ids]
    if not rows:
        return "(제공된 근거 없음 — 관련 항목은 모두 NA로 처리할 것)"
    lines = []
    for e in rows:
        line = (
            f"[{e.evidence_id}] criterion={e.criterion_id} stance={e.stance} grade={e.evidence_grade}\n"
            f"    source: {e.source_title} ({e.source_url or 'URL 없음'}) / publisher={e.publisher} "
            f"published_at={e.published_at or '미상'}\n"
            f"    claim: {e.claim}\n    excerpt: {e.excerpt}"
        )
        if e.conditions:
            line += f"\n    conditions: {e.conditions}"
        lines.append(line)
    return "\n".join(lines)


def _build_messages(task: ScoreTask, rub: dict) -> list[dict]:
    agent_cfg = rub["agents"][AGENT]
    tech_name = task.tech.get("name") or task.tech.get("tech_id")
    addon = (ROOT / PROMPT_FILE).read_text(encoding="utf-8")
    system = rub["judge"]["common_prompt"].format(
        agent_title=agent_cfg["title"],
        tech=tech_name,
        agent_addon=f"{agent_cfg['judge_addon']}\n\n{addon}",
        criteria_block=_criteria_block(rub, task.criterion_ids),
        schema=json.dumps(rub["judge"]["output_schema"], ensure_ascii=False, indent=2),
    )
    brief = task.tech_brief.get("summary") if task.tech_brief else None
    user = (
        f"## 근거 목록 (반드시 evidence_id로만 인용)\n{_evidence_block(task.evidence, task.criterion_ids)}\n\n"
        f"## 배경 참고 (근거로 인용 금지)\n{brief or '(없음)'}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _get_llm():
    """LLM 클라이언트 생성 지점. 테스트에서는 monkeypatch로 교체한다."""
    cfg = runtime()["llm"]
    model = cfg.get("model") or "gpt-4.1-mini"
    return ChatOpenAI(model=model, temperature=cfg.get("temperature", 0)).with_structured_output(
        _LLMScoreBatch
    )


def _resolve_evidence(task: ScoreTask, evidence_ids: list[str]) -> tuple[list[EvidenceBrief], bool]:
    """evidence_id → EvidenceBrief 조회. 존재하지 않는 id(할루시네이션 인용)는 버린다."""
    by_id = {e.evidence_id: e for e in task.evidence}
    briefs: list[EvidenceBrief] = []
    dropped = False
    for eid in evidence_ids:
        e = by_id.get(eid)
        if e is None:
            dropped = True
            continue
        briefs.append(
            EvidenceBrief(
                evidence_id=e.evidence_id,
                claim=e.claim,
                source=e.source_url or e.source_title,
                date=e.published_at,
                grade=e.evidence_grade,
                stance=e.stance,
            )
        )
    return briefs, dropped


def score(task: ScoreTask, rubrics: dict) -> list[CriterionResult]:
    """rubrics["judge"]["common_prompt"] + rubrics["agents"]["stakeholder"]["judge_addon"]
    + prompts/score/stakeholder.md 로 LLM 채점. task.evidence만 근거로 쓰고, tech_brief는 배경 참고용."""
    messages = _build_messages(task, rubrics)
    batch: _LLMScoreBatch = _get_llm().invoke(messages)

    tech_id = task.tech["tech_id"]
    results: list[CriterionResult] = []
    for item in batch.items:
        briefs, dropped = _resolve_evidence(task, item.evidence_ids)
        confidence, rationale = item.confidence, item.rationale
        if dropped:
            confidence = "low"
            rationale += " [자동 검증: 존재하지 않는 evidence_id 인용이 제거되어 확신도를 low로 하향]"
        results.append(
            CriterionResult(
                tech_id=tech_id,
                criterion_id=item.criterion_id,
                agent_type=AGENT,
                round=task.round,
                score=item.score,
                evidence=briefs,
                rationale=rationale,
                confidence=confidence,
                intra_conflict=item.intra_conflict,
            )
        )
    return results
