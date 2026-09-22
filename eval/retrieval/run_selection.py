"""임베딩 모델 선정 실험 (설계서 B-4 (6)). 담당 1.

절차
1. 같은 청크로 후보 3개 모델의 인덱스를 만든다 (모델별 권장 접두어·instruction 적용: rag/embeddings.py).
2. gold_locators.jsonl의 한국어 질문 20개로 dense 단독 검색 → Hit@1·3·5, MRR.
3. 선정 규칙: Hit@5 최고 → 차이 0.05 이내면 MRR → 그다음 실행 부담(인덱스 생성 시간).
4. 선정 모델로 BM25 결합(hybrid) 후 Hit@5를 한 번 더 확인.
5. 결과를 results.json, model_selection.md 에 기록한다.

실행: uv run python eval/retrieval/run_selection.py
정답 위치(page)가 비어 있는 질문은 건너뛰고 경고한다 → 먼저 사람이 원문을 보고 채울 것.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from metrics import is_gold, summarize  # noqa: E402

from kv_eval.rag.embeddings import MODELS  # noqa: E402
from kv_eval.rag.index import build_for_model, load_and_chunk  # noqa: E402
from kv_eval.rag.retriever import dense_search, hybrid_search  # noqa: E402

CANDIDATES = ["BAAI/bge-m3", "intfloat/multilingual-e5-large", "Qwen/Qwen3-Embedding-0.6B"]
K_EVAL = 10          # MRR 계산용 검색 깊이
TIE = 0.05           # Hit@5 차이가 이 이내면 MRR로 판단


def load_gold() -> list[dict]:
    rows = [json.loads(x) for x in (HERE / "gold_locators.jsonl").read_text(encoding="utf-8").splitlines() if x]
    ready = [r for r in rows if r.get("page")]
    if len(ready) < len(rows):
        print(f"[경고] 정답 위치가 비어 있는 질문 {len(rows) - len(ready)}개는 제외합니다.")
    if not ready:
        sys.exit("정답 위치(page)가 채워진 질문이 없습니다. gold_locators.jsonl을 먼저 채우세요.")
    return ready


def evaluate(search_fn, gold: list[dict], **kw) -> tuple[dict, list[dict]]:
    ranked_lists, detail = [], []
    for g in gold:
        hits = search_fn(g["question"], k=K_EVAL, doc_ids=[g["doc_id"]], **kw)
        ranked = [is_gold(c.locator, g) for c in hits]
        ranked_lists.append(ranked)
        first = next((i + 1 for i, ok in enumerate(ranked) if ok), None)
        detail.append({"qid": g["qid"], "origin": g["origin"], "rank": first,
                       "top1": hits[0].chunk_id if hits else None})
    return summarize(ranked_lists), detail


def pick(results: dict[str, dict]) -> str:
    best_hit = max(r["Hit@5"] for r in results.values())
    tied = [m for m, r in results.items() if best_hit - r["Hit@5"] <= TIE]
    best_mrr = max(results[m]["MRR"] for m in tied)
    tied = [m for m in tied if best_mrr - results[m]["MRR"] <= 1e-9]
    return min(tied, key=lambda m: results[m]["build_sec"])


def main() -> None:
    gold = load_gold()
    chunks = load_and_chunk()
    results: dict[str, dict] = {}
    details: dict[str, list] = {}
    for model in CANDIDATES:
        assert model in MODELS
        t0 = time.time()
        build_for_model(chunks, model)
        build_sec = round(time.time() - t0, 1)
        summary, detail = evaluate(dense_search, gold, model_name=model)
        results[model] = {**summary, "build_sec": build_sec}
        details[model] = detail
        print(model, results[model])

    chosen = pick(results)
    # hybrid_search는 runtime.yaml의 모델 인덱스를 쓰므로, 선정 모델이 다르면 먼저 설정을 바꾸라고 알린다.
    from kv_eval.config import runtime
    hybrid = None
    if runtime()["embedding"]["model"] == chosen:
        hybrid, details["hybrid"] = evaluate(hybrid_search, gold)
    out = {"date": date.today().isoformat(), "n_questions": len(gold), "n_chunks": len(chunks),
           "dense": results, "chosen": chosen, "hybrid": hybrid, "detail": details}
    (HERE / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(out)
    print(f"선정: {chosen}" + ("" if hybrid else " → runtime.yaml embedding.model을 바꾸고 다시 실행하면 hybrid 확인"))


def write_markdown(out: dict) -> None:
    lines = [f"| 모델 | Hit@1 | Hit@3 | Hit@5 | MRR | 인덱스 생성(초) |", "|---|---:|---:|---:|---:|---:|"]
    for m, r in out["dense"].items():
        mark = " **(선정)**" if m == out["chosen"] else ""
        lines.append(f"| {m}{mark} | {r['Hit@1']} | {r['Hit@3']} | {r['Hit@5']} | {r['MRR']} | {r['build_sec']} |")
    hy = out["hybrid"]
    hybrid_line = (f"선정 모델 + BM25(RRF) Hit@5 = **{hy['Hit@5']}** (dense 단독 {out['dense'][out['chosen']]['Hit@5']})"
                   if hy else "hybrid 확인 전 (runtime.yaml embedding.model을 선정 모델로 바꾼 뒤 재실행)")
    misses = [d for d in out["detail"][out["chosen"]] if not d["rank"] or d["rank"] > 5]
    miss_lines = [f"- {d['qid']} ({d['origin']}): 정답 순위 {d['rank'] or '10위 밖'}" for d in misses] or ["- 없음"]
    body = TEMPLATE.format(date=out["date"], n=out["n_questions"], n_chunks=out["n_chunks"],
                           table="\n".join(lines), chosen=out["chosen"], hybrid=hybrid_line,
                           misses="\n".join(miss_lines))
    (HERE / "model_selection.md").write_text(body, encoding="utf-8")


TEMPLATE = """# 임베딩 모델 선정 근거 (설계서 B-4)

리더보드 점수는 후보를 좁히는 데만 썼고, 최종 선정은 우리 코퍼스(TurboQuant·CXL-PNM 논문 42쪽)와
한국어 평가 질문으로 직접 검색해 본 결과로 정했다.

## 실험 조건
- 실행일: {date} / 질문 {n}개 (공통 조사 질문 4 × 논문 2 + RAG 평가 항목 12) / 청크 {n_chunks}개
- 세 모델 모두 같은 청크 사용 (1200자 ≈ 300토큰, E5 512토큰 제한 안)
- 모델별 권장 입력: e5 `query:`/`passage:` 접두어, Qwen3 query instruction, bge-m3 없음
- dense 단독 비교, 기술별 필터(doc_id) 적용, 정답 = 사람이 표시한 문서·쪽·절

## 결과 (dense 단독)
{table}

## 선정
- 규칙: Hit@5 최고 → 차이 0.05 이내면 MRR → 그다음 실행 부담
- 선정 모델: **{chosen}**
- {hybrid}

## 상위 5위 밖 질문 (실패 유형 분석 대상: 용어 / 표·수치 / 교차언어)
{misses}
"""


if __name__ == "__main__":
    main()
