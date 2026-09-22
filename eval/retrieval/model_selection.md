# 임베딩 모델 선정 근거 (설계서 B-4)

> 이 파일은 `uv run python eval/retrieval/run_selection.py` 실행 시 결과로 덮어쓴다. 아래는 실행 전 절차다.

## 절차
1. `gold_locators.jsonl`의 20개 질문마다 **정답 위치(page, section)를 사람이 원문 PDF를 보고 먼저 채운다.**
   - 검색 결과를 보고 채우지 않는다 (점수가 부풀려짐).
   - 논문에 답이 없는 질문(예: 논문이 에너지를 보고하지 않는 경우)은 답이 있는 다른 질문으로 바꾼다.
2. 후보 3개(bge-m3, multilingual-e5-large, Qwen3-Embedding-0.6B)로 같은 청크의 인덱스를 만들고 dense 단독 검색.
3. Hit@5 최고 → 차이 0.05 이내면 MRR → 그다음 실행 부담(인덱스 생성 시간)으로 선정.
4. 선정 모델을 `configs/runtime.yaml` embedding.model에 고정하고 다시 실행해 BM25 결합 후 Hit@5 확인.

## 결과
실행 전
