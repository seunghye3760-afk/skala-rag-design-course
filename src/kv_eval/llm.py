"""프로젝트 공용 LLM 생성 함수. 근거 추출·채점·종합 모두 이 함수 하나만 쓴다.

모델은 configs/runtime.yaml llm.model 한 곳에서만 정한다 (개별 모듈에서 모델명 하드코딩 금지).
"""
from __future__ import annotations

import os
from functools import lru_cache

from .config import ROOT, runtime


@lru_cache
def chat_model():
    """runtime.yaml 설정으로 ChatOpenAI 1개를 만들어 재사용한다.

    - KV_LLM_MODEL 환경변수(.env 포함)가 있으면 runtime.yaml보다 우선한다.
      개발·실험 때 싼 모델로 돌리는 용도 — yaml은 팀 확정값 그대로 커밋 유지.
    - API 키는 프로젝트 .env를 셸 환경변수보다 우선한다
      (셸에 남은 옛 OPENAI_API_KEY가 load_dotenv 기본 동작으로는 .env를 가리기 때문).
    - gpt-5 계열은 temperature를 지원하지 않으므로 temperature 없이 생성한다.
    """
    from dotenv import dotenv_values
    from langchain_openai import ChatOpenAI

    cfg = runtime()["llm"]
    model = os.getenv("KV_LLM_MODEL") or cfg["model"]
    if not model:
        raise ValueError("configs/runtime.yaml llm.model이 비어 있음")
    api_key = dotenv_values(ROOT / ".env").get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    kwargs: dict = {"model": model, "api_key": api_key}
    if not model.startswith("gpt-5"):
        kwargs["temperature"] = cfg.get("temperature", 0)
    return ChatOpenAI(**kwargs)
