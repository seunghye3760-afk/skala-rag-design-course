"""설정·경로 로더. 모든 모듈은 여기서 경로와 설정을 가져온다."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def output_root() -> Path:
    """실행 결과 폴더. 테스트에서는 KV_OUTPUT_DIR로 바꾼다."""
    return Path(os.getenv("KV_OUTPUT_DIR", ROOT / "outputs" / "runs"))


@lru_cache
def runtime() -> dict:
    return yaml.safe_load((ROOT / "configs" / "runtime.yaml").read_text(encoding="utf-8"))


@lru_cache
def technologies_config() -> dict:
    return yaml.safe_load((ROOT / "configs" / "technologies.yaml").read_text(encoding="utf-8"))


@lru_cache
def rubrics() -> dict:
    return json.loads((ROOT / "configs" / "rubrics.json").read_text(encoding="utf-8"))


def criteria_list(rub: dict, only: list[str] | None = None) -> list[dict]:
    items = rub["criteria"]
    return [c for c in items if not only or c["id"] in only]


def criterion(rub: dict, criterion_id: str) -> dict:
    return next(c for c in rub["criteria"] if c["id"] == criterion_id)


def agent_of(rub: dict, criterion_id: str) -> str:
    return criterion(rub, criterion_id)["agent"]


def uses_rag(crit: dict) -> bool:
    return any(s.startswith("RAG") for s in crit["evidence_sources"])
