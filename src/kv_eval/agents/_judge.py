"""관점 에이전트 공용 LLM 채점 (설계서 D-8). 담당 2.

LLM은 루브릭 문구에 맞는 점수·라벨을 고르고 근거는 evidence_id 인용으로만 쓴다.
EvidenceBrief는 인용된 id로 실제 Evidence에서 코드가 만든다 (없는 출처·수치 방지).
점수 상한·하한·TRL 게이트는 여기서 하지 않는다 — rules/ 코드가 확정 (apply_caps가 덮어씀).

KV_FAKE=1 이면 가짜 채점을 돌려준다 (그래프 뼈대 테스트용).
"""
from __future__ import annotations

import json
import os
from typing import Literal

from pydantic import BaseModel

from ..config import ROOT
from ..graph.task_schema import CriterionResult, Evidence, EvidenceBrief, ScoreTask
from ._fake import fake_scores


class _CritScore(BaseModel):
    criterion_id: str
    score: Literal["1", "2", "3", "4", "5", "NA"]
    evidence_ids: list[str]            # [근거 목록]의 evidence_id만 인용
    rationale: str                     # 점수 근거 2~3문장 + 바로 위 점수를 주지 않은 이유 1문장
    confidence: Literal["high", "medium", "low"]
    intra_conflict: bool = False
    # cap_applied는 여기 없다: 설계서 D-9(3)이 "코드가 상한 조정하고 cap_applied에 기록"한다고
    # 명시했으므로 rules/caps.py만 쓴다. LLM에게 물어보면 코드가 안 건드리는 경로(TRL 등)에서
    # LLM이 지어낸 문구가 검증 없이 새어 나갈 수 있다.


def llm_scores(task: ScoreTask, rubrics: dict, agent: str, prompt_file: str) -> list[CriterionResult]:
    if os.getenv("KV_FAKE") == "1":
        return fake_scores(task)

    by_cid: dict[str, list[Evidence]] = {}
    for e in task.evidence:
        by_cid.setdefault(e.criterion_id, []).append(e)

    # 항목당 LLM 1회 호출: 5개 항목 × 근거 수백 건을 한 번에 보내면
    # 긴 응답에서 항목이 누락되는 문제가 있어(전체 실행에서 NA 10건 확인) 항목 단위로 쪼갠다.
    results: dict[str, CriterionResult] = {}
    pool = {e.evidence_id: e for e in task.evidence}
    for cid in task.criterion_ids:
        evs = by_cid.get(cid)
        if not evs:                        # 근거 0건은 LLM 없이 규칙으로 NA (na_definition)
            results[cid] = _na(task, cid, "긍정·비판 쿼리를 모두 실행했으나 관련 근거 0건 — 공개 근거 없음(정보 공백)")
            continue

        from ..llm import chat_model

        prompt = _build_prompt(task, rubrics, agent, prompt_file, [cid], by_cid)
        r = chat_model().with_structured_output(_CritScore).invoke(prompt)
        briefs = [EvidenceBrief(evidence_id=i, claim=pool[i].claim, source=pool[i].source_title,
                                date=pool[i].published_at, grade=pool[i].evidence_grade,
                                stance=pool[i].stance)
                  for i in r.evidence_ids if i in pool]        # 목록 밖 id 인용은 버림
        results[cid] = CriterionResult(
            tech_id=task.tech["tech_id"], criterion_id=cid, agent_type=task.agent_type,
            round=task.round, score="NA" if r.score == "NA" else int(r.score),
            evidence=briefs, rationale=r.rationale, confidence=r.confidence,
            intra_conflict=r.intra_conflict)   # cap_applied는 기본값(None) — rules/caps.py만 채움
    return [results[cid] for cid in task.criterion_ids]


def _na(task: ScoreTask, cid: str, reason: str) -> CriterionResult:
    return CriterionResult(tech_id=task.tech["tech_id"], criterion_id=cid, agent_type=task.agent_type,
                           round=task.round, score="NA", rationale=reason, confidence="low")


def _build_prompt(task: ScoreTask, rubrics: dict, agent: str, prompt_file: str,
                  cids: list[str], by_cid: dict[str, list[Evidence]]) -> str:
    cfg = rubrics["agents"][agent]
    addon = cfg["judge_addon"] + (
        "\n- rationale에 쓰는 수치는 인용(evidence_ids)한 근거의 claim·발췌에 있는 것만 쓴다. "
        "인용하지 않은 근거의 수치를 언급하려면 그 근거를 evidence_ids에 추가한다. "
        "rationale 본문에 evidence_id 문자열을 쓰지 않는다.")
    pf = ROOT / prompt_file
    if pf.exists():
        addon += "\n\n" + pf.read_text(encoding="utf-8")

    crits = {c["id"]: c for c in rubrics["criteria"]}
    blocks = []
    for cid in cids:
        c = crits[cid]
        rub = "\n".join(f"  {level}: {c['rubric'][level]['criterion']}"
                        for level in ("5", "4", "3", "2", "1", "NA") if level in c["rubric"])
        pits = "\n".join(f"  - {p}" for p in c.get("pitfalls", []))
        evs = "\n".join(_ev_line(e) for e in by_cid[cid])
        blocks.append(f"### {cid} {c['name']}\n질문: {c['question']}\n루브릭:\n{rub}\n"
                      f"함정 (피할 것):\n{pits}\n[근거 목록 — evidence_id로만 인용]\n{evs}")

    prompt = rubrics["judge"]["common_prompt"]
    for key, value in {
        "{agent_title}": cfg["title"],
        "{tech}": task.tech["name"],
        "{agent_addon}": addon,
        "{criteria_block}": "\n\n".join(blocks),
        "{schema}": json.dumps(rubrics["judge"]["output_schema"], ensure_ascii=False, indent=2),
    }.items():
        prompt = prompt.replace(key, value)
    return prompt


def _ev_line(e: Evidence) -> str:
    return (f"- ({e.evidence_id}) [{e.evidence_grade}|{e.stance}] {e.claim}\n"
            f"  출처: {e.source_title} ({e.publisher}, {e.published_at or '날짜 미상'})"
            f" | 측정: {e.measurement_type} | 조건: {e.conditions or '-'}\n"
            f"  발췌: {e.excerpt[:200]}")
