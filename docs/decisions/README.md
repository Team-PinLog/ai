# 결정 기록 (ADR)

AI 파트가 내린 설계 결정을 기록합니다. 각 문서는 **무엇을 왜 정했고 무엇을 버렸는지**를 남겨, 이후 합류자와 리뷰어가 배경을 재구성할 수 있게 합니다.

공용 계약의 단일 원본은 `Team-PinLog/docs`의 `static/05_AI_설계.md`입니다. 여기 ADR은 그 계약을 만들며 내린 **AI 국한 결정의 근거**를 담습니다. 계약에 이미 반영된 결정이라도, "왜 이 선택인가"는 계약 문서에 남기지 않으므로 이곳이 그 근거의 원본입니다.

## 형식

```text
# ADR-NNN: 제목
- 상태 · 날짜 · 관련 PR/커밋 · 소유 파트
## 맥락 · 결정 · 근거 · 버린 대안 · 영향 · 검증
```

## 목록

| ADR | 제목 | 상태 |
|---|---|---|
| [ADR-001](ADR-001-immutable-context.md) | Context 불변 모델 (버전 컬럼 제거) | 채택 |
| [ADR-002](ADR-002-is-deleted-cancelled.md) | 즉시 파기 대신 `is_deleted` + `CANCELLED` 마커 | 채택 |
| [ADR-003](ADR-003-exact-cosine.md) | 정확 cosine 검색 (HNSW/IVFFlat 미도입) | 채택 |
| [ADR-004](ADR-004-keyword-preset-judgment.md) | Keyword 프리셋 구성·후보 하한·판정 프롬프트 | 채택(판정 모델은 진행 중) |
