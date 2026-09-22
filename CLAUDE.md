# CLAUDE.md — 팀 공통 규칙 (Claude Code가 자동으로 읽음)

## 프로젝트
KV cache 최적화 기술 TurboQuant(SW)·CXL-PNM(HW)을 TRL·시장성·이해관계자·도메인(AI 데이터센터) 4관점에서
평가하는 LangGraph Agentic RAG. **우열 판정·점수 합산·순위화 금지.**

## 기준 문서 (충돌 시 이 순서)
1. `deliverables/RAG-Design_*.pdf` (설계서 ver1.4)  2. `configs/rubrics.json`  3. `docs/architecture.md`

## 명령어
- 설치: `uv sync`
- 테스트: `uv run pytest`  (PR 전 반드시 통과)
- 실행: `uv run python app.py --criteria TRL-1,DOM-4` (작게) / `uv run python app.py` (전체)
- 논문 받기: `uv run python scripts/download_corpus.py`

## 절대 규칙
- `src/kv_eval/graph/task_schema.py`, `graph/state.py` 는 계약 파일: 수정은 `contract` 라벨 PR로만.
- 함수 이름·인자·반환 형식(시그니처)은 유지하고 내부만 구현한다. `TODO(담당 N)` 주석이 작업 위치.
- 담당 폴더 밖 파일을 고치면 PR 설명에 이유를 적는다.
- TRL 단계·점수 상한·상충 후보·균형 점검은 `rules/`의 코드로만 (LLM에게 맡기지 않음).
- 근거 없는 수치·출처를 만들지 않는다. 검색 요약이 아니라 원문을 확인한 내용만 Evidence로.
- `.env`, API 키, 논문 PDF, `data/indexes/`, `outputs/runs/` 는 커밋 금지.
- 무거운 라이브러리(sentence-transformers, faiss)는 함수 안에서 import.

## 담당
1 문서·임베딩·검색: `rag/`, `tools/paper_search.py`, `eval/retrieval/`, `agents/tech_research.py`
2 근거 수집·기술 평가: `evidence/`, `tools/web_search.py`, `tools/open_source.py`, `agents/trl.py`, `agents/domain.py`
3 시장·이해관계자: `agents/market.py`, `agents/stakeholder.py`, `prompts/score/market.md`, `stakeholder.md`
4 통합·보고서: `graph/`, `rules/`, `agents/score_task.py`, `agents/synthesis.py`, `reporting/`, `app.py`

## 작업 방식
- 큰 변경은 plan mode(Shift+Tab)로 계획 먼저 보여주기.
- 커밋 메시지: `feat(rag): ...`, `fix(rules): ...` 형식, 한국어 가능.
- 브랜치: `feat/<영역>-<내용>` → PR → 리뷰 1명 → merge.
