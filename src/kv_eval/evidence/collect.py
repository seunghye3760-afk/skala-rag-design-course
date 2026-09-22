"""collect_evidence 노드: 항목 1 × 기술 1 근거 수집 (설계서 D-7). 담당 2.

지금은 가짜 근거(FAKE)를 돌려준다. 실제 구현 순서 (그림 3):
  쿼리 생성({tech}·{category} 치환) → 논문 검색(evidence_sources에 RAG 포함 시) → Tavily 긍정·비판 쿼리
  → 원문 확인·근거 추출(LLM 보조) → A~D 등급·pro/con 분류 → 중복 제거 → 관련성 판정
  → 0건이면 쿼리 재작성 1회 → 그래도 0건이면 빈 리스트 (NA 후보)
"""
from __future__ import annotations

from datetime import date

from ..graph.task_schema import CollectTask, Evidence, Locator


def collect_evidence(task: CollectTask) -> dict:
    tech_id, cid = task.tech["tech_id"], task.criterion["id"]
    queries = {k: v.format(tech=task.tech["query_names"][0], category=task.tech["category"])
               for k, v in task.criterion["queries"].items()}
    evidence = fake_evidence(task)        # TODO(담당 2): 실제 수집으로 교체
    log = [{"tech_id": tech_id, "criterion_id": cid, "round": task.round, "queries": queries,
            "results": len(evidence), "rewritten": False, "searched_at": date.today().isoformat()}]
    return {"evidence_pool": evidence, "search_log": log}


def fake_evidence(task: CollectTask) -> list[Evidence]:
    """뼈대 확인용 가짜 근거. 실제 구현 후 삭제."""
    tech_id, cid = task.tech["tech_id"], task.criterion["id"]
    out = []
    for stance in ("pro", "con"):
        eid = f"FAKE-{tech_id}-{cid}-{stance}-r{task.round}"
        out.append(Evidence(
            evidence_id=eid, tech_id=tech_id, criterion_id=cid, claim=f"[FAKE] {stance} 근거",
            source_title="FAKE SOURCE", source_url=None, publisher="FAKE", accessed_at=date.today().isoformat(),
            source_type="FAKE", evidence_grade="C", stance=stance, measurement_type="FAKE",
            excerpt="FAKE", locator=Locator(doc_id="FAKE"), round=task.round))
    return out
