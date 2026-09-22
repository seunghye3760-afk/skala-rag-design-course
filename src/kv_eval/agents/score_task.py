"""score_task 노드: 네 관점을 같은 노드로 호출 (agent_type만 다름). 담당 4.

- KV_FAKE=1 이면 관점 에이전트를 부르지 않고 _fake.fake_scores로 바로 응답한다.
  (agents/market.py·stakeholder.py는 실제 ChatOpenAI를 생성하므로, API 키 없는 환경에서
  test_graph_runs·test_retry가 항상 통과하려면 여기서 막아야 한다. trl.py·domain.py가
  구현된 뒤에도 같은 스위치를 그대로 쓸 수 있다.)
- 그 외에는 AGENTS[task.agent_type].score(task, rubrics())를 부르고, 예외(JSON 스키마
  불일치·구조화 출력 파싱 실패 등)가 나면 같은 입력으로 한 번만 다시 부른다 (설계서 D-10
  "채점 출력 형식 오류 → JSON 스키마 불일치 시 즉시 1회 재요청"). 재요청도 실패하면 항목별로
  NA/low confidence로 폴백한다 — 여기서 재검색을 걸지 않고, balance_check가 "근거는 있는데
  NA"로 다시 잡아 재채점 대상에 넣는다 (rules/balance.py).

각 관점 에이전트 모듈(trl.py·market.py·stakeholder.py·domain.py)이 서로 다른 사람이 만든
코드라 어떤 예외 타입을 던질지 보장할 수 없다(pydantic ValidationError, langchain의 출력
파싱 예외, 그 외 네트워크 오류 등). 그래서 재요청 여부 판단은 Exception 전체를 잡는다 —
버그를 숨기는 게 아니라, 무엇이 나든 "같은 요청 1회 재시도 → 그래도 안 되면 NA" 규칙을
동일하게 적용하기 위해서다. 재요청도 실패하면 원래 예외를 print로 남긴다.
"""
from __future__ import annotations

import os
import traceback

from ..graph.task_schema import CriterionResult, ScoreTask
from ..config import rubrics
from . import domain, market, stakeholder, trl
from ._fake import fake_scores

AGENTS = {"trl": trl, "market": market, "stakeholder": stakeholder, "domain": domain}


def _fallback(task: ScoreTask, error: Exception) -> list[CriterionResult]:
    print(f"[score_task] {task.agent_type}/{task.tech['tech_id']} 재요청도 실패, NA로 처리: "
          f"{type(error).__name__}: {error}")
    return [
        CriterionResult(
            tech_id=task.tech["tech_id"], criterion_id=cid, agent_type=task.agent_type, round=task.round,
            score="NA", confidence="low",
            rationale=f"[자동 처리] 채점 출력 형식 오류로 1회 재요청했지만 실패해 정보 공백(NA)으로 처리 "
                      f"({type(error).__name__}). 균형 점검에서 재채점 대상으로 다시 잡힌다.",
        )
        for cid in task.criterion_ids
    ]


def _force_intra_conflict(results: list[CriterionResult]) -> list[CriterionResult]:
    """인용 근거의 A·B등급이 pro/con으로 갈리면 intra_conflict는 참이어야 한다 (설계서 C-4).

    인용 목록만으로 기계 판정 가능한 속성인데 채점 LLM이 자주 빠뜨려 균형 점검이
    재채점 라운드를 소모하므로, 규칙대로 코드가 확정한다 (네 관점 공통 지점)."""
    out = []
    for r in results:
        strong = [b for b in r.evidence if b.grade in ("A", "B")]
        if (not r.intra_conflict and any(b.stance == "pro" for b in strong)
                and any(b.stance == "con" for b in strong)):
            r = r.model_copy(update={"intra_conflict": True})
        out.append(r)
    return out


def score_task(task: ScoreTask) -> dict:
    if os.getenv("KV_FAKE") == "1":
        return {"criterion_results": fake_scores(task)}

    rub = rubrics()
    agent = AGENTS[task.agent_type]
    try:
        results = agent.score(task, rub)
    except Exception:  # noqa: BLE001 — 설계서 D-10: 형식 오류 시 즉시 1회 재요청 (에이전트별 예외 타입 불명)
        print(f"[score_task] {task.agent_type}/{task.tech['tech_id']} 1차 채점 실패, 1회 재요청:\n"
              f"{traceback.format_exc(limit=2)}")
        try:
            results = agent.score(task, rub)
        except Exception as e2:  # noqa: BLE001
            results = _fallback(task, e2)
    return {"criterion_results": _force_intra_conflict(results)}
