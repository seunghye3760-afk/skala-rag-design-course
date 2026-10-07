# KV Cache 최적화 기술 다관점 평가 Agentic RAG

본 프로젝트는 KV cache 최적화 기술인 **TurboQuant(SW)** 와 **CXL-PNM(HW)** 을 선정하여,
TRL·시장성·이해관계자·도메인(AI 데이터센터 LLM 추론) 관점에서 근거 기반으로 평가하는
Agentic RAG 프로젝트입니다. 기술의 우열이나 순위를 판정하지 않고, 관점별 평가와 상충 조건,
정보 공백을 정리한 보고서를 자동으로 생성합니다.

![전체 흐름](docs/flow.png)

## Overview

- **Objective**: 소프트웨어와 하드웨어 기반 KV cache 최적화 기술을 복수 관점에서 비교·평가
- **Method**: LangGraph Orchestrator-Workers + Agentic RAG
- **Evaluation Perspectives**: TRL, 시장성, 이해관계자, 도메인 적합성
- **Domain**: AI 데이터센터의 장문 LLM 추론 인프라
- **Output**: 근거, 조건, 상충 후보, 정보 공백을 포함한 Markdown·PDF 보고서
- **Principle**: 점수 합산이나 순위화 없이 관점별 결과를 독립적으로 제시

## Selected Technologies

- **SW — TurboQuant**: 온라인 벡터 양자화를 통해 KV cache의 메모리 사용량과 대역폭 병목을 줄일 수 있으며, 기존 GPU 기반 추론 환경에 적용 가능한 소프트웨어 기술이라는 점에서 선정했습니다.
- **HW — CXL-PNM**: CXL 기반 메모리 확장과 Processing-Near-Memory를 결합하여 대용량 KV cache의 저장·전송·연산 병목을 하드웨어 관점에서 개선할 수 있어 비교 대상으로 선정했습니다.

## Features

- 논문 PDF 기반 기술 정보 추출 및 원문 위치 추적
- Tavily 기반 웹 검색과 원문 확인을 통한 최신 근거 수집
- FAISS dense 검색과 BM25 sparse 검색을 RRF로 결합한 하이브리드 검색
- 기술별·평가 항목별 긍정 및 비판 근거의 병렬 수집
- State 기반 `WorkPlan`과 LangGraph `Send`를 이용한 동적 Worker fan-out
- TRL·시장성·이해관계자·도메인 관점별 독립 평가
- 근거 등급, 중복, 관련성 및 측정 유형 분류
- 근거 부족, 편향, 조건 누락, 형식 오류에 대한 자동 점검 및 제한적 재검색·재평가
- 점수 상한·하한, TRL 게이트, 상충 후보를 코드 기반 규칙으로 적용
- 관점별 결과, 트레이드오프, 정보 공백을 포함한 Markdown·PDF 보고서 생성
- 필수 섹션·Evidence ID·근거 없는 수치·우열 표현 검사와 최대 2회의 재작성·압축
- `pypdf`로 실제 페이지 수를 확인해 10페이지 이하인 PDF만 최종 저장
- **확증 편향 방지 전략**: 두 기술에 동일한 검색 규칙을 적용하고, 긍정·비판 근거를 함께 수집하며, 검색 요약이 아닌 원문을 확인합니다. 또한 LLM의 판단과 규칙 기반 검증을 분리하고, 근거가 부족한 항목은 추정하지 않고 정보 공백으로 기록합니다.

## Tech Stack

- **Language**: Python 3.11–3.12
- **Framework**: LangGraph, LangChain
- **LLM / Generator**: GPT-5.5
- **LLM / Judge**: GPT-5.5
- **Retrieval**: FAISS + BM25, Reciprocal Rank Fusion(RRF), Top-K 5
- **Retrieval Evaluation**: Hit@1·3·5, MRR *(모델 선정 실험 결과 입력 전)*
- **Embedding**: BAAI/bge-m3
- **Web Search**: Tavily
- **Validation**: Pydantic
- **Report**: Markdown, ReportLab PDF
- **Package Manager**: uv
- **Test / Lint**: pytest, Ruff

## Agents

- **Orchestrator / Planner**: 기술·루브릭·선택 항목·재시도 대상을 읽어 결정적인 `WorkPlan`을 만든다. LLM은 사용하지 않는다.
- **Evidence Worker**: 기술 1개와 평가 항목 1개를 입력받아 근거와 실행 상태를 반환한다.
- **Score Worker**: 기술·관점별 `ScoreTask`에 포함된 항목만 채점하며 TRL·시장성·이해관계자·도메인 Agent를 호출한다.
- **Aggregator**: reducer와 join으로 병렬 결과를 합치고 실패를 기존 `RetryTarget`으로 변환한다.
- **Synthesizer**: 관점별 결과와 상충 조건을 종합해 저장 전 보고서 초안을 만든다.
- **Quality Evaluator**: 보고서 계약과 인용을 검사하고 재작성 또는 압축 여부를 결정한다.
- **Renderer**: 미리보기 PDF를 만들고 실제 페이지 수를 센 뒤 통과한 결과만 최종 저장한다.

## Architecture

```mermaid
flowchart TD
    A[설정·루브릭·기술 조사] --> P1[코드 기반 수집 Plan]
    P1 -->|Send × N| W1[Evidence Workers]
    W1 --> J1[reducer · join]
    J1 --> P2[코드 기반 채점 Plan]
    P2 -->|Send × N| W2[Score Workers]
    W2 --> J2[reducer · join]
    J2 --> Q{균형·실패 점검}
    Q -->|research| P1
    Q -->|rescore| P2
    Q -->|통과·한도 소진| R[규칙 적용]
    R --> S[보고서 초안]
    S --> V{품질 검사}
    V -->|rewrite, 최대 2회 공유| S
    V -->|통과| PDF[미리보기 PDF · pypdf 페이지 검사]
    PDF -->|10페이지 초과, 최대 2회 공유| C[본문 압축]
    C --> V
    PDF -->|10페이지 이하| F[최종 저장]
```

상세 그래프의 기준 소스는 [`docs/main_graph.mmd`](docs/main_graph.mmd)이며,
State·입출력 계약은 [`docs/architecture.md`](docs/architecture.md)에서 확인할 수 있습니다.

## Directory Structure

```text
├── configs/                 # 기술·도메인 설정, 실행 설정, 평가 루브릭
├── data/                    # 코퍼스 목록, 원문, 청크 및 검색 인덱스
├── deliverables/            # 설계서와 최종 제출물
├── docs/                    # 아키텍처 문서와 그래프 이미지
├── eval/retrieval/          # 임베딩 모델 선정 및 Hit@K·MRR 평가
├── outputs/runs/            # 실행별 평가 결과와 보고서
├── prompts/                 # 기술 조사·근거 추출·채점·종합 프롬프트
├── scripts/                 # 코퍼스 다운로드 스크립트
├── src/kv_eval/
│   ├── agents/              # 기술 조사·관점별 평가·종합 Agent
│   ├── evidence/            # 근거 수집·등급화·중복 처리
│   ├── graph/               # LangGraph 상태·계약·분배·메인 그래프
│   ├── rag/                 # 문서 로딩·청킹·임베딩·검색
│   ├── reporting/           # Markdown·PDF 보고서 생성
│   ├── rules/               # 균형 점검·점수 제한·TRL 게이트·상충 탐지
│   └── tools/               # 논문 검색·웹 검색·원문 확인 도구
├── tests/                   # 단위 및 통합 테스트
├── app.py                   # 실행 스크립트
├── pyproject.toml           # 프로젝트 및 의존성 설정
└── README.md
```

## Usage

### 1. 프로젝트 설치

```bash
git clone https://github.com/seunghye3760-afk/skala-rag-design-course.git
cd skala-rag-design-course
uv sync
```

### 2. 환경 변수 설정

```bash
cp .env.example .env
```

Windows에서는 다음 명령을 사용합니다.

```powershell
copy .env.example .env
```

생성된 `.env` 파일에 `OPENAI_API_KEY`와 `TAVILY_API_KEY`를 입력합니다.

### 3. 테스트 실행

```bash
uv run pytest
```

오케스트레이션 변경 파일의 Ruff 검사는 다음처럼 실행합니다.

```bash
git diff --name-only main...HEAD -- '*.py' | xargs uv run ruff check
```

### 4. 임베딩 모델 적용

```
  uv run python -m kv_eval.rag.index --model BAAI/bge-m3
```

### 5. 보고서 생성

전체 18개 평가 항목을 실행합니다.

```bash
uv run python app.py
```

일부 평가 항목만 실행할 수도 있습니다.

```bash
uv run python app.py --criteria TRL-1,MKT-2,DOM-4
```

외부 API 없이 전체 그래프 구조를 검증하려면 다음과 같이 실행합니다.

```bash
KV_FAKE=1 uv run python app.py
```

실행 결과는 `outputs/runs/<run_id>/`에 저장됩니다.

- `report.pdf`, `report.md`: 의사결정용 요약 보고서. PDF는 반드시 10페이지 이하이다.
- `evidence.json`, `scores.json`: PDF에서 생략한 전체 근거와 상세 채점 결과이다.
- `search_log.json`, `info_gaps.json`: 검색 실행 내역과 재시도 후에도 남은 정보 공백이다.

`report.pdf`는 초안 품질 검사와 실제 페이지 검사를 모두 통과한 뒤에만 확정된다. 8~10페이지를
권장하지만 10페이지 이하가 필수 조건이며, 재작성과 압축은 합쳐 최대 2회만 수행한다.

> 현재 저장소는 FAKE 데이터를 사용해 전체 그래프의 실행 흐름을 검증할 수 있습니다.
> 각 담당 영역의 실제 구현과 임베딩 선정 실험 결과는 순차적으로 반영합니다.

## 비고

report.pdf 생성 후 사람 검증 후 평가하는 항목(체크박스)은 추후 작성해야 하는 내용으로 상정합니다.


## Contributors

1. **안서현**: Embedding 모델 선정, 설계서 작성, 코드 점검 및 통합, 이슈 확인
2. **이민기**: RAG 적용 대상 설계, AI 루브릭 설계, 근거 수집 및 기술 평가 설계
3. **박재흥**: RAG 적용 대상 설계, 시장성 및 이해관계자 평가 설계, 설계서 현행화 점검
4. **김승혜**: 기술 선정 방식 설계, 관점별 웹 가드레일 설계, 문서·임베딩·검색 및 그래프 통합
