# 구조도 — `main`(RAG 과제) vs `agent/orchestrator-workers`(Agent 과제)

GitHub에서 Mermaid로 렌더링됩니다. 변경 내역 설명은 [`agent-orchestrator-changes.md`](agent-orchestrator-changes.md) 참고.

## 1. 원래 그래프 (`main`)

주황 = Agent 과제에서 교체된 부분, 회색 = 그대로 유지

```mermaid
flowchart TD
    TR["tech_research<br/>논문 RAG 기술 브리프"]
    DC["dispatch_collect<br/>기술 2 × 항목 18 = Send × 36 (고정)"]
    C1["collect_evidence<br/>셀 1개 · 채널 전부"]
    C2["collect_evidence<br/>셀 1개 · 채널 전부"]
    C3["… 항상 36개"]
    SC["채점 · score_task × 4관점<br/>Send × 8"]
    BAL{"balance_check<br/>공백·편향·조건·형식 점검"}
    SYN["apply_rules → synthesize_report"]
    E(["END (보고서 검사 없음)"])

    TR --> DC
    DC --> C1 & C2 & C3
    C1 & C2 & C3 --> SC
    SC --> BAL
    BAL -->|재검색: 같은 방식으로 다시 수집| DC
    BAL -->|재채점| SC
    BAL -->|통과 / 한도| SYN
    SYN --> E

    classDef changed fill:#FAECE7,stroke:#993C1D,color:#712B13
    classDef kept fill:#F1EFE8,stroke:#5F5E5A,color:#444441
    class DC,C1,C2,C3,E changed
    class TR,SC,BAL,SYN kept
```

## 2. 바뀐 그래프 (이 브랜치)

보라 = 새로 추가·교체, 회색 = 기존 로직 유지

```mermaid
flowchart TD
    TR["tech_research<br/>논문 RAG 기술 브리프"]
    PL["plan_tasks · Orchestrator<br/>셀별 채널 계획 → Send × N"]
    W1["Worker<br/>셀 × rag"]
    W2["Worker<br/>셀 × web"]
    W3["Worker …<br/>계획만큼 N개"]
    SC["채점 · score_task × 4관점<br/>(기존)"]
    BAL{"balance_check<br/>점검 조건은 기존 그대로"}
    SYN["apply_rules → synthesize_report"]
    QE{"quality_eval · Hybrid<br/>규칙 + LLM Judge 4축"}
    E([END])

    TR --> PL
    PL --> W1 & W2 & W3
    W1 & W2 & W3 --> SC
    SC --> BAL
    BAL -->|근거 문제 → 해당 셀만 재계획| PL
    BAL -->|재채점| SC
    BAL -->|통과 / 한도| SYN
    SYN --> QE
    QE -->|근거 문제 셀| PL
    QE -->|서술 문제 → 재작성| SYN
    QE -->|통과 / 한도| E

    classDef new fill:#EEEDFE,stroke:#534AB7,color:#3C3489
    classDef kept fill:#F1EFE8,stroke:#5F5E5A,color:#444441
    class PL,W1,W2,W3,QE new
    class TR,SC,BAL,SYN,E kept
```

## 3. Orchestrator(`plan_tasks`) 내부

주황 = LLM 판단, 보라 = 코드가 강제. 아래 예시의 채널 조합은 기본 계획(허용 채널 전부) 기준이며 실제 실행에서는 LLM이 범위 안에서 더 적게 고를 수 있음

```mermaid
flowchart TD
    S1["1. 계획 대상 셀 정하기<br/>round 0: 전체 셀 · 재계획: 미달 셀 + 사유"]
    S2["2. LLM이 셀마다 결정<br/>채널 · 추가 pro/con 쿼리 · 우선순위 · 사유"]
    S3["3. 코드 가드 검증·보정<br/>허용 채널 · 누락 셀 보충 · 대상 셀만 · 상한 80"]
    S4["4. State.plan 저장 → Send × N<br/>SubTask 1개 = Worker 1개"]
    X1["MKT-2 시장성<br/>web → Worker 1개"]
    X2["DOM-4 도메인<br/>rag + web → 2개"]
    X3["TRL-2 재현성<br/>rag · web · open_source → 3개"]

    S1 --> S2 --> S3 --> S4
    S4 --> X1 & X2 & X3

    classDef llm fill:#FAECE7,stroke:#993C1D,color:#712B13
    classDef code fill:#EEEDFE,stroke:#534AB7,color:#3C3489
    classDef ex fill:#F1EFE8,stroke:#5F5E5A,color:#444441
    class S2 llm
    class S1,S3,S4 code
    class X1,X2,X3 ex
```

## 4. Worker(`run_subtask`) 성공·실패 경로

```mermaid
flowchart TD
    W["run_subtask · Worker<br/>입력: SubTask 1개 (셀 × 채널)"]
    C["collect_evidence(channel)<br/>해당 채널만 검색·원문 확인"]
    OK["성공 (근거 0건 포함)<br/>evidence_pool + outcome done"]
    ERR["예외 발생<br/>같은 SubTask 재시도"]
    EX["2회 모두 실패<br/>excluded · last_error 기록"]
    J["evidence_join<br/>모든 Worker 종료 후 1회 · 다른 Worker는 영향 없음"]

    W --> C
    C --> OK
    C --> ERR
    ERR -->|재시도| C
    ERR --> EX
    OK --> J
    EX --> J

    classDef w fill:#EEEDFE,stroke:#534AB7,color:#3C3489
    classDef fail fill:#FAECE7,stroke:#993C1D,color:#712B13
    classDef kept fill:#F1EFE8,stroke:#5F5E5A,color:#444441
    class W,C,OK w
    class ERR,EX fail
    class J kept
```

## 5. State 저장 시점 (체크포인트)

저장은 노드가 아니라 LangGraph 실행기가 한다 — `app.py`가 `compile(checkpointer=SqliteSaver)`로 붙이면
**단계(superstep)가 끝날 때마다** State 전체를 `outputs/runs/checkpoints.sqlite`에 저장(`thread_id = run_id`).
병렬 구간은 Worker가 모두 끝나 reducer로 합쳐진 뒤 1회 저장. 보라색 파일은 노드가 직접 쓰는 관측용 파일로 재개에는 쓰지 않음.

```mermaid
flowchart TD
    A["load_config → load_rubrics → tech_research<br/>3단계, 단계마다 저장"]
    B["plan_tasks<br/>State.plan 기록"]
    C["run_subtask × N (병렬)<br/>전부 끝난 뒤 1회 저장"]
    D["evidence_join → score_dispatch<br/>2단계, 단계마다 저장"]
    E["score_task × 8 (병렬)<br/>전부 끝난 뒤 1회 저장"]
    F["score_join → balance_check<br/>2단계, 단계마다 저장"]
    G["apply_rules → synthesize_report<br/>2단계, 단계마다 저장"]
    H["quality_eval<br/>판정 · 다음 경로 기록"]
    Z([END])

    A -->|저장| B -->|저장| C -->|저장| D -->|저장| E -->|저장| F -->|저장| G -->|저장| H -->|저장| Z

    FB["decisions.jsonl · plans/r{n}.json"]
    FF["decisions.jsonl<br/>재시도 판단과 사유"]
    FG["report.md · report.pdf<br/>evidence · scores json"]
    FH["quality_r{n}.json<br/>+ decisions.jsonl"]
    B -.-> FB
    F -.-> FF
    G -.-> FG
    H -.-> FH

    classDef step fill:#F1EFE8,stroke:#5F5E5A,color:#444441
    classDef file fill:#EEEDFE,stroke:#534AB7,color:#3C3489
    class A,B,C,D,E,F,G,H,Z step
    class FB,FF,FG,FH file
    linkStyle 0,1,2,3,4,5,6,7,8 stroke:#0F6E56,color:#0F6E56
```

> 화살표의 "저장" = SQLite 체크포인트(State 전체). 점선 = 노드가 직접 쓰는 파일.
