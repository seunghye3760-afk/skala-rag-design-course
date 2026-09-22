# 아키텍처 (설계서 ver1.4 D장 요약)

전체 설계는 `deliverables/RAG-Design_*.pdf` 가 기준이다. 그림: `docs/flow.png`(전체 흐름), `docs/main_graph.png`(Main Graph).

## 흐름
load_config → load_rubrics → tech_research → dispatch_collect →(Send × 36) collect_evidence → join
→ score_dispatch →(Send × 8) score_task[trl·market·stakeholder·domain] → join → balance_check
→ (재검색 필요: dispatch_collect / 형식 오류만: score_dispatch / 통과·한도 소진: apply_rules) → synthesize_report

## 계약 파일
- `src/kv_eval/graph/task_schema.py`: Locator, Chunk, Evidence, CriterionResult, RetryTarget, CollectTask, ScoreTask …
- `src/kv_eval/graph/state.py`: MainState (설계서 D-2 + `final_results`)
- 근거는 모든 round 누적·중복 제거, 채점은 (기술, 항목)별 최신 round만 사용 (`graph/reducers.py`)

## Mermaid
`docs/main_graph.mmd` 참조
