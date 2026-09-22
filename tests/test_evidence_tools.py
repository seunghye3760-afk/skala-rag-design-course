"""담당 2 도구·등급 단위 테스트. 전부 네트워크 없이 돈다 (외부 호출은 monkeypatch)."""
import json

import pytest

from kv_eval.evidence.grading import grade
from kv_eval.tools import open_source, web_search


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("KV_CACHE_DIR", str(tmp_path / "cache"))


# ---------- grading ----------

@pytest.mark.parametrize("source_type,measurement_type,is_independent,expected", [
    ("독립 벤치마크", "실측", True, "A"),          # 제3자 실측
    ("논문", "재현 실험", True, "A"),               # 제3자 재현 논문
    ("사업보고서", "실적 공시", False, "A"),        # 공식 공시는 당사자여도 A
    ("논문", "실측", False, "B"),                   # 개발사 논문의 실하드웨어 실험
    ("공식 기술 블로그", "실측", False, "B"),       # 당사자 공식 발표
    ("보도자료", "발표", False, "B"),
    ("논문", "시뮬레이션", True, "C"),              # 추정 기반은 주체 무관 C
    ("논문", "사이클 시뮬레이션", False, "C"),
    ("리포트", "TCO 계산 모델", True, "C"),
    ("리포트", "의견", True, "C"),                  # 독립 애널리스트 의견
    ("인터뷰", "발언", False, "B"),                 # 이해당사자 발언은 최대 B
    ("뉴스 기사", "실측", True, "D"),               # 2차 자료는 내용 무관 D
    ("커뮤니티", "사용기", True, "D"),
    ("요약 블로그", "의견", True, "D"),             # 비공식 블로그는 D
    ("알 수 없음", "조건 없는 수치", True, "D"),    # 판정 불가는 보수적으로 D
])
def test_grade(source_type, measurement_type, is_independent, expected):
    assert grade(source_type, measurement_type, is_independent) == expected


@pytest.mark.parametrize("source_type,measurement_type,is_independent,cited,expected", [
    # 2차 자료(기사)가 논문을 구체적으로 인용 → 원출처 등급으로 재분류 (설계서 C-4 "재분류")
    ("뉴스 기사", "실측", True, "논문", "A"),          # 논문+독립 실측 → A
    ("뉴스 기사", "실측", False, "논문", "B"),         # 논문+당사자 실측 → B
    ("커뮤니티", "발표", False, "공시", "A"),          # 공시 인용은 당사자여도 A
    ("뉴스 기사", "실측", True, None, "D"),            # 인용 없으면 그대로 D
])
def test_grade_regrades_when_secondary_cites_primary(source_type, measurement_type,
                                                      is_independent, cited, expected):
    assert grade(source_type, measurement_type, is_independent, cited) == expected


def test_grade_covers_collect_enum_vocabulary():
    """collect._Item의 enum 어휘가 grading 키워드와 동기화됐는지 가드.
    깨지면 두 파일 중 한쪽만 수정된 것 — 매핑을 함께 갱신할 것."""
    import typing

    from kv_eval.evidence.collect import _Item

    src_values = typing.get_args(_Item.model_fields["source_type"].annotation)
    mt_values = typing.get_args(_Item.model_fields["measurement_type"].annotation)

    # 의도적으로 D인 2차 자료 출처. 그 외 출처는 측정 유형이 정해지면 D로 새면 안 된다.
    secondary = {"기사", "블로그", "커뮤니티"}
    for src in src_values:
        for mt in mt_values:
            for indep in (True, False):
                g = grade(src, mt, indep)
                assert g in "ABCD"
                if src not in secondary:
                    assert g != "D", f"D 폴백으로 샘: grade({src!r}, {mt!r}, {indep})"


# ---------- web_search ----------

def test_search_normalizes_and_caches(monkeypatch):
    calls = []

    def fake_tavily(query, max_results, period_from):
        calls.append((query, max_results, period_from))
        return [{"url": "https://a.com/x", "title": "T", "content": "요약",
                 "published_date": "2026-05-01", "score": 0.9, "raw_content": "무시"}]

    monkeypatch.setattr(web_search, "_call_tavily", fake_tavily)
    r1 = web_search.search("turboquant kv cache")
    assert r1 == [{"url": "https://a.com/x", "title": "T", "content": "요약",
                   "published_date": "2026-05-01"}]
    assert calls == [("turboquant kv cache", 5, "2025-01-01")]   # runtime.yaml 기간 적용

    # 같은 쿼리 재호출은 캐시 사용 → API 안 나감
    monkeypatch.setattr(web_search, "_call_tavily",
                        lambda *a: pytest.fail("캐시가 있으면 API를 호출하면 안 됨"))
    assert web_search.search("turboquant kv cache") == r1

    # 쿼리·max_results가 다르면 별도 캐시
    monkeypatch.setattr(web_search, "_call_tavily", fake_tavily)
    web_search.search("turboquant kv cache", max_results=3)
    assert calls[-1] == ("turboquant kv cache", 3, "2025-01-01")


def test_search_cache_file_is_json(monkeypatch, tmp_path):
    monkeypatch.setattr(web_search, "_call_tavily", lambda *a: [])
    web_search.search("empty query")
    files = list((tmp_path / "cache" / "web").glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8")) == []


# ---------- open_source ----------

HTML = """<html><head>
<title>TurboQuant 4-bit KV cache 실측기</title>
<meta property="og:site_name" content="ExampleLab"/>
<meta property="article:published_time" content="2026-02-03"/>
</head><body><article>
<p>TurboQuant는 KV cache를 4비트로 양자화하는 기법이다. 우리는 Llama-70B, 128K 문맥,
배치 32 조건에서 vLLM에 통합해 직접 측정했다. 측정 결과 동시 세션 수가 1.8배 늘었다.</p>
<p>다만 3비트 설정에서는 요약 태스크 정확도가 2.1%p 하락했다. 추론 태스크에서는
하락 폭이 더 컸다. 설정별로 결과가 갈리므로 운영 적용 전에 태스크별 검증이 필요하다.</p>
<p>전력은 랙 단위로 측정하지 못했고 장치 단위 수치만 확보했다. 이 부분은 추가 측정이
필요하다는 점을 명확히 밝혀 둔다.</p>
</article></body></html>"""


def test_fetch_extracts_and_caches(monkeypatch):
    monkeypatch.setattr(open_source, "_download", lambda url: HTML)
    out = open_source.fetch("https://examplelab.io/turboquant-review")

    assert out["url"] == "https://examplelab.io/turboquant-review"
    assert out["title"] == "TurboQuant 4-bit KV cache 실측기"
    assert "동시 세션 수가 1.8배" in out["text"]      # 검색 요약이 아닌 원문 본문
    assert out["publisher"] == "ExampleLab"
    assert out["published_at"] == "2026-02-03"

    # 캐시 후에는 다운로드 없이 같은 결과
    monkeypatch.setattr(open_source, "_download",
                        lambda url: pytest.fail("캐시가 있으면 다운로드하면 안 됨"))
    assert open_source.fetch("https://examplelab.io/turboquant-review") == out


# ---------- collect_evidence (실제 경로, 외부 호출은 전부 monkeypatch) ----------

from kv_eval.evidence import collect as collect_mod
from kv_eval.evidence.collect import _Item, collect_evidence
from kv_eval.graph.task_schema import CollectTask


def _task(evidence_sources=("웹: 독립 벤치마크",)):
    return CollectTask(
        tech={"tech_id": "turboquant", "name": "TurboQuant", "group": "SW",
              "query_names": ["TurboQuant"], "category": "KV cache quantization"},
        criterion={"id": "DOM-3", "name": "처리량·동시 세션", "agent": "domain",
                   "question": "같은 GPU 수 기준으로 처리량이 늘고 SLO를 지키는가?",
                   "evidence_sources": list(evidence_sources),
                   "queries": {"pro": "{tech} throughput improvement",
                               "con": "{tech} throughput slowdown"}},
        round=0)


def _item(**over):
    base = dict(claim="Llama-70B 128K에서 동시 세션 1.8배", excerpt="동시 세션 수가 1.8배 늘었다.",
                stance="pro", source_type="독립 벤치마크", measurement_type="실측",
                is_independent=True, conditions="Llama-70B, 128K, batch 32", relevant=True)
    return _Item(**{**base, **over})


@pytest.fixture
def real_collect(monkeypatch):
    monkeypatch.setenv("KV_FAKE", "0")
    monkeypatch.setattr(web_search, "search",
                        lambda q, max_results=5: [{"url": "https://a.com/1", "title": "T",
                                                   "content": "c", "published_date": "2026-05-01"}])
    monkeypatch.setattr(open_source, "fetch",
                        lambda url: {"url": url, "title": "실측기", "text": "본문",
                                     "publisher": "ExampleLab", "published_at": "2026-02-03"})


def test_collect_builds_graded_evidence(real_collect, monkeypatch):
    monkeypatch.setattr(collect_mod, "_extract",
                        lambda task, text, hint_source: [
                            _item(),                                          # A: 독립 실측
                            _item(stance="con", claim="3비트에서 정확도 하락",
                                  source_type="커뮤니티", measurement_type="의견"),  # D: 2차 자료
                            _item(relevant=False, claim="무관한 내용"),        # 관련성 탈락
                        ])
    out = collect_evidence(_task())
    pool = out["evidence_pool"]
    assert [(e.evidence_grade, e.stance) for e in pool] == [("A", "pro"), ("D", "con")]
    assert all(e.tech_id == "turboquant" and e.criterion_id == "DOM-3" for e in pool)
    assert pool[0].source_url == "https://a.com/1" and pool[0].publisher == "ExampleLab"
    assert pool[0].published_at == "2026-02-03" and pool[0].conditions
    log = out["search_log"][0]
    assert log["results"] == 2 and log["rewritten"] is False
    assert log["queries"]["pro"] == ["TurboQuant throughput improvement"]


def test_collect_dedupes_same_source_same_claim(real_collect, monkeypatch):
    monkeypatch.setattr(collect_mod, "_extract",
                        lambda task, text, hint_source: [
                            _item(measurement_type="시뮬레이션"),   # C
                            _item(),                                # A — 같은 원문·방향·주장 → A만 남김
                        ])
    pool = collect_evidence(_task())["evidence_pool"]
    assert len(pool) == 1
    assert pool[0].evidence_grade == "A" and pool[0].duplicate_group


def test_collect_zero_results_rewrites_once_then_na(monkeypatch):
    monkeypatch.setenv("KV_FAKE", "0")
    monkeypatch.setattr(web_search, "search", lambda q, max_results=5: [])   # 항상 0건
    rewrites = []

    def fake_rewrite(task, queries):
        rewrites.append(queries)
        return {"pro": ["broader query"], "con": ["broader con query"]}

    monkeypatch.setattr(collect_mod, "_rewrite_queries", fake_rewrite)
    out = collect_evidence(_task())
    assert out["evidence_pool"] == []                     # NA 후보
    assert len(rewrites) == 1                             # 재작성은 1회만
    assert out["search_log"][0]["rewritten"] is True
    assert out["search_log"][0]["queries"]["pro"] == ["broader query"]


def test_collect_rag_failure_falls_back_to_web(real_collect, monkeypatch):
    import kv_eval.tools.paper_search as paper_search

    def broken(query, k=5, doc_ids=None):
        raise RuntimeError("인덱스 미구축")

    monkeypatch.setattr(paper_search, "search", broken)
    monkeypatch.setattr(collect_mod, "_extract", lambda task, text, hint_source: [_item()])
    # evidence_sources에 RAG 포함 + 논문 검색 실패(인덱스 미구축 등) → 웹 근거만으로 진행
    pool = collect_evidence(_task(evidence_sources=("RAG: 논문", "웹: 벤치마크")))["evidence_pool"]
    assert len(pool) == 1 and pool[0].source_url == "https://a.com/1"


def test_collect_rag_filters_by_tech_and_fills_published(real_collect, monkeypatch):
    import kv_eval.tools.paper_search as paper_search

    from kv_eval.graph.task_schema import Chunk, Locator

    seen = []

    def fake_search(query, k=5, doc_ids=None):
        seen.append(doc_ids)
        return [Chunk(chunk_id="turboquant-p6-1", text="논문 본문",
                      locator=Locator(doc_id="turboquant", page=6))]

    monkeypatch.setattr(paper_search, "search", fake_search)
    monkeypatch.setattr(collect_mod, "_extract",
                        lambda task, text, hint_source: [_item(source_type="논문")])
    pool = collect_evidence(_task(evidence_sources=("RAG: 논문",)))["evidence_pool"]

    assert seen and all(d == ["turboquant"] for d in seen)      # 해당 기술 논문만 검색
    paper = next(e for e in pool if e.source_type == "논문")
    assert paper.published_at == "2025-04-28"                    # manifest 게재일 반영 (recency 규칙용)
    assert paper.locator.page == 6
