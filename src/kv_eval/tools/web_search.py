"""Tavily 웹 검색. 담당 2."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path

from ..config import ROOT, runtime


def cache_file(kind: str, key: str) -> Path:
    """`.cache/<kind>/` 아래 캐시 파일 경로. 테스트는 KV_CACHE_DIR로 위치를 바꾼다."""
    root = Path(os.getenv("KV_CACHE_DIR", str(ROOT / runtime()["cache_dir"])))
    d = root / kind
    d.mkdir(parents=True, exist_ok=True)
    return d / (hashlib.sha256(key.encode("utf-8")).hexdigest()[:24] + ".json")


def _call_tavily(query: str, max_results: int, period_from: str) -> list[dict]:
    from tavily import TavilyClient   # 무거운·외부 라이브러리는 함수 안에서 import

    client = TavilyClient()           # TAVILY_API_KEY는 .env에서 로드됨 (config.load_dotenv)
    resp = client.search(query=query, max_results=max_results, search_depth="advanced",
                         start_date=period_from)
    return resp.get("results", [])


def search(query: str, max_results: int = 5) -> list[dict]:
    """웹 검색 → [{"url", "title", "content", "published_date"}].

    - 검색 기간은 configs/runtime.yaml web_search.period_from (두 기술에 동일 적용)
    - 쿼리·검색일 기준으로 .cache/web/ 에 캐시 (재실행 때 API 재호출 안 함)
    - content는 후보 선별용 요약일 뿐이다. claim은 open_source.fetch 원문으로만 작성
    """
    period_from = runtime()["web_search"]["period_from"]
    f = cache_file("web", f"{date.today().isoformat()}|{period_from}|{max_results}|{query}")
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    results = [{"url": r.get("url"), "title": r.get("title"), "content": r.get("content"),
                "published_date": r.get("published_date")} for r in _call_tavily(query, max_results, period_from)]
    f.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return results
