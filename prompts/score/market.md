# market 채점 추가 지시

공통 지시는 `configs/rubrics.json`의 `judge.common_prompt`를 따른다. 아래는 시장성(MKT)
채점에서만 추가로 지키는 것.

- 위 [출력] 예시(criterion_id/tech/score/evidence[...]/rationale/confidence/cap_applied/
  intra_conflict)는 사람이 읽기 위한 설명이다. 실제로 채워야 하는 값은 함수 호출 스키마의
  `items[].evidence_ids`이며, 아래 근거 목록에 적힌 `[evidence_id]` 문자열만 그대로
  사용한다. claim·source·grade·stance는 evidence_id로 이미 연결되어 있으므로 다시
  옮겨 적지 않는다.
- 목표 시장 정의가 근거에 없으면(예: "AI 반도체 전체"처럼 상위·인접 시장 수치만 있는
  경우) MKT-1은 최고점을 줄 수 없다. rationale에 어떤 시장 정의·기관·연도가 빠졌는지
  적는다.
- MKT-2(상용화·채택)와 MKT-3(생태계 지지)을 혼동하지 않는다: MKT-2는 "채택 사례의 수",
  MKT-3은 "지지 주체 유형의 다양성"이다.
- 개인 개발자의 사용기는 채택 사례(MKT-2)로 세지 않는다. 그건 STK-2(이해관계자) 담당이다.
- "5년 뒤 주류가 될 것" 같은 예측 문장만으로 MKT-4에 4점 이상을 주지 않는다. 로드맵·후속
  발표처럼 관찰 가능한 신호만 인정한다.
- `intra_conflict`은 pro/con 방향이 반대라고 무조건 true가 아니다. **B등급 이상 근거끼리**
  서로 반대일 때만 true이다. 한쪽이 C·D등급이면(등급이 다르면) 등급이 높은 쪽 방향을 따르고
  intra_conflict은 false로 둔다.

## 예시 (형식 참고용 — 실제 값은 항상 제공된 근거로만 채운다)

```
criterion_id: MKT-1
score: 3
evidence_ids: ["ev_014"]
rationale: "ev_014는 기업 자체 발표로 CAGR 수치나 조사 기관명이 없어 정성적 성장 언급만
있는 3점에 해당한다. 독립 기관 자료가 없어 4점은 주지 않았다."
confidence: "low"
intra_conflict: false
```

