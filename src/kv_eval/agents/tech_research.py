"""기술 조사 에이전트 (LLM + 논문 RAG). 담당 1.
논문에서 원리·수치·검증 조건을 공통 양식(설계서 B-3 tech_briefs)으로 추출한다.

노션 GUIDE A: 🔍 기술 조사 에이전트 (RAG O) — 원문에서 기술 개요·범위·한계 추출.
흐름 (20-RAG 실습 01-NaiveRAG + 02-RelevanceCheck + 04-QueryRewrite를 항목 단위로):

    for 기술 2 × (양식 5개 + 공통 조사 질문 4개):
        retrieve(paper_search) → extract(관련 청크 있음? + 값 + 인용 chunk_id)
          ├ 관련 있음 → 인용 chunk_id가 실제 검색 결과에 있는지 코드로 검증 → 저장
          └ 관련 없음 → 재검색 < 2회면 쿼리 재작성 후 재검색, 아니면 '미보고'

출력: tech_briefs[tech_id][항목] = {"value", "status", "chunk_ids", "locators"}
  status: "reported" | "미보고"(논문에 보고 없음·검색 범위에서 확인 못함) | "확인 불가"(자료만으로 판단 불가)
tech_briefs는 채점 때 배경 참고용이며 채점 근거로 쓰지 않는다 (설계서 D-3).
"""
from __future__ import annotations

import json
import os
from typing import Literal

from pydantic import BaseModel, Field

from ..config import ROOT, output_root, runtime
from ..graph.state import MainState
from ..graph.task_schema import Chunk

BRIEF_FIELDS = ["kv_cache_구성", "처리_구조", "실험_조건", "평가_결과", "적용_한계"]

# 양식 항목별 질문 (한국어 = dense용, 영문 용어 = BM25용). 설계서 B-3 공통 정리 양식.
FIELD_QUESTIONS: dict[str, str] = {
    "kv_cache_구성": "KV cache를 어떤 정밀도·형식으로, 어디에 저장하며 양자화 또는 토큰 선택은 어떻게 하는가? "
                    "KV cache quantization bit precision storage location token selection",
    "처리_구조": "attention 계산은 어느 장치에서 수행되고, 장치 간 작업 분배와 데이터 이동 경로는 어떻게 되는가? "
                "attention computation device offload data movement GPU memory architecture",
    "실험_조건": "어떤 모델·데이터셋·문맥 길이·배치 크기·하드웨어·비교 기준(baseline)으로 실험했는가? "
                "experimental setup model dataset context length batch size hardware baseline",
    "평가_결과": "메모리·압축률, 처리량, 지연시간, 모델 품질(정확도), 에너지 결과는 어떻게 보고되었는가? "
                "results memory compression ratio throughput latency accuracy energy",
    "적용_한계": "추가 비용, 효과가 제한되는 조건, 평가하지 않은 환경·조건은 무엇인가? "
                "limitations overhead cost assumptions not evaluated future work",
}
# 설계서 B-3 공통 조사 질문 4개
COMMON_QUESTIONS: dict[str, str] = {
    "q1_해결_병목": "이 기술은 어떤 KV cache 병목을 해결하려는가? KV cache memory bandwidth capacity bottleneck",
    "q2_효과_조건": "개선 효과를 얻기 위해 필요한 조건은 무엇인가? requirements conditions assumptions",
    "q3_품질_비용_평가": "모델 품질과 추가 처리 비용은 어떻게 평가했는가? accuracy evaluation overhead cost",
    "q4_미평가_조건": "어떤 환경이나 조건은 평가하지 않았는가? limitations not evaluated scope",
}
MAX_RETRIEVE = 2      # 실습 02-RelevanceCheck의 MAX_RETRIEVE_RETRY
TOP_K = 5


class BriefAnswer(BaseModel):
    relevant: bool = Field(description="제공된 청크에 질문에 답할 내용이 있는가")
    status: Literal["reported", "미보고", "확인 불가"]
    value: str = Field(description="청크에 적힌 내용만으로 쓴 한국어 요약. 수치는 조건과 함께. 없으면 빈 문자열")
    chunk_ids: list[str] = Field(default_factory=list, description="value의 근거가 된 chunk_id")


class RewrittenQuery(BaseModel):
    query: str


# ---------------------------------------------------------------- 노드
def tech_research(state: MainState) -> dict:
    """prompts/tech_research.md + tools.paper_search.search 로 tech_briefs를 채운다.
    보고되지 않은 항목은 '미보고', 판단 불가는 '확인 불가'."""
    if os.getenv("KV_FAKE") == "1":          # 테스트·뼈대 확인용 (tests/conftest.py에서 설정)
        return {"tech_briefs": {t["tech_id"]: {f: "(FAKE) 미구현" for f in BRIEF_FIELDS}
                                for t in state["technologies"]}}

    extract, rewrite = _chains()
    briefs: dict[str, dict] = {}
    for tech in state["technologies"]:
        items = {**{f: FIELD_QUESTIONS[f] for f in BRIEF_FIELDS}, **COMMON_QUESTIONS}
        briefs[tech["tech_id"]] = {key: _answer(tech, q, extract, rewrite) for key, q in items.items()}

    _save(state.get("run_id", "manual"), briefs)
    return {"tech_briefs": briefs}


def _answer(tech: dict, question: str, extract, rewrite) -> dict:
    from ..tools.paper_search import format_chunks, search

    query = f"{tech['name']} {question}"
    for attempt in range(MAX_RETRIEVE):
        chunks = search(query, k=TOP_K, doc_ids=[tech["tech_id"]])
        ans: BriefAnswer = extract.invoke({"tech": tech["name"], "question": question,
                                           "context": format_chunks(chunks)})
        if ans.relevant and ans.status != "미보고":
            return _validated(ans, chunks)
        if attempt + 1 < MAX_RETRIEVE:
            query = rewrite.invoke({"tech": tech["name"], "question": question, "query": query}).query
    return {"value": "", "status": "미보고", "chunk_ids": [], "locators": [],
            "note": f"재검색 {MAX_RETRIEVE}회 후에도 관련 청크를 찾지 못함 (설정한 검색 범위 기준)"}


def _validated(ans: BriefAnswer, chunks: list[Chunk]) -> dict:
    """LLM이 인용한 chunk_id가 실제 검색 결과에 있는지 코드로 확인한다."""
    by_id = {c.chunk_id: c for c in chunks}
    cited = [cid for cid in dict.fromkeys(ans.chunk_ids) if cid in by_id]
    if ans.status == "reported" and not cited:
        return {"value": ans.value, "status": "확인 불가", "chunk_ids": [], "locators": [],
                "note": "인용한 chunk_id가 검색 결과에 없음"}
    return {"value": ans.value, "status": ans.status, "chunk_ids": cited,
            "locators": [by_id[c].locator.model_dump(exclude_none=True) for c in cited]}


# ---------------------------------------------------------------- LLM
def _prompts() -> dict[str, str]:
    """prompts/tech_research.md 를 '## 이름' 단위로 나눠 읽는다."""
    text = (ROOT / "prompts" / "tech_research.md").read_text(encoding="utf-8")
    parts: dict[str, str] = {}
    for block in text.split("\n## ")[1:]:
        name, _, body = block.partition("\n")
        parts[name.strip()] = body.strip()
    return parts


def _chains():
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_openai import ChatOpenAI

    cfg = runtime()["llm"]
    if not cfg.get("model"):
        raise ValueError("configs/runtime.yaml llm.model 이 비어 있습니다 (팀에서 모델명 확정 필요)")
    llm = ChatOpenAI(model=cfg["model"], temperature=cfg.get("temperature", 0))
    p = _prompts()
    extract = (ChatPromptTemplate.from_messages([("system", p["extract_system"]), ("human", p["extract_user"])])
               | llm.with_structured_output(BriefAnswer))
    rewrite = (ChatPromptTemplate.from_messages([("system", p["rewrite_system"]), ("human", p["rewrite_user"])])
               | llm.with_structured_output(RewrittenQuery))
    return extract, rewrite


def _save(run_id: str, briefs: dict) -> None:
    out = output_root() / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "tech_briefs.json").write_text(json.dumps(briefs, ensure_ascii=False, indent=2), encoding="utf-8")
