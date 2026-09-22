# stakeholder 채점 추가 지시

공통 지시는 `configs/rubrics.json`의 `judge.common_prompt`를 따른다. 아래는 이해관계자
(STK) 채점에서만 추가로 지키는 것.

- 위 [출력] 예시(criterion_id/tech/score/evidence[...]/rationale/confidence/cap_applied/
  intra_conflict)는 사람이 읽기 위한 설명이다. 실제로 채워야 하는 값은 함수 호출 스키마의
  `items[].evidence_ids`이며, 아래 근거 목록에 적힌 `[evidence_id]` 문자열만 그대로
  사용한다.
- rationale에서 인용하는 근거마다 발언 주체 유형 태그를 직접 붙인다: [경쟁사] [도입 기업]
  [개발자] [애널리스트] [미디어]. 그리고 그 주체가 이 기술과 이해관계가 있는지도 표시한다
  (예: "이해당사자" / "제3자"). 이해당사자의 발언은 최대 B등급으로 취급하고, 반대 근거가
  있으면 함께 적는다.
- STK-3(채택 장벽 인식)은 역방향 채점이다: 장벽 지적이 없고 심각하지 않을수록 높은 점수.
  "지적이 많을수록 좋다"로 착각하지 않는다.
- 주가·투자 지표는 기술 품질의 근거가 아니라 "시장의 파급력 인식"의 근거로만 STK-4에
  쓴다. rationale에 이 해석을 명시한다.
- 채점 대상 주체는 각 항목에 정의된 유형으로 한정한다. 근거 없는 "의도"나 "숨은 전략"을
  추정하지 않는다.
- `intra_conflict`은 pro/con 방향이 반대라고 무조건 true가 아니다. **B등급 이상 근거끼리**
  서로 반대일 때만 true이다. 한쪽이 C·D등급이면(등급이 다르면) 등급이 높은 쪽 방향을 따르고
  intra_conflict은 false로 둔다.

## 예시 (형식 참고용 — 실제 값은 항상 제공된 근거로만 채운다)

```
criterion_id: STK-1
score: 2
evidence_ids: ["ev_031"]
rationale: "[경쟁사, 이해당사자] ev_031에서 경쟁사 임원이 한계를 주장했지만 벤치마크 등
1차 근거는 제시하지 않아 2점에 해당한다. 1차 근거 제시가 없어 1점은 주지 않았다."
confidence: "medium"
intra_conflict: false
```

