# P4: 파생 데이터를 즉시 파기하지 않고 `is_deleted` 마커와 `CANCELLED` 상태로 정리한다

- **상태**: Accepted
- **날짜**: 2026-07-23
- **관련 PR/커밋**: [ai#1](https://github.com/Team-PinLog/ai/pull/1), 공용 계약 [docs#2](https://github.com/Team-PinLog/docs/pull/2) `static/05`
- **주도(Driver)**: AI 파트

## 맥락

Context·Record 가 삭제되거나 수정으로 구 Context 가 무효화될 때, 파생된 임베딩(`ai.context_embedding`)과 키워드(`ai.context_keyword`)를 어떻게 정리할지 정해야 한다. 두 방식이 후보였다.

- **즉시 파기**: 삭제 즉시 파생 행을 물리 `DELETE` 한다.
- **마커**: `is_deleted = true`로 표시하고, 진행 중 AI 작업의 상태를 `CANCELLED`로 전이한다.

문제는 **삭제와 AI 처리가 경합**한다는 점이다. FastAPI 가 임베딩을 계산하는 도중 사용자가 Context 를 지우면, 계산이 끝나 저장하려는 순간 대상 Context 는 이미 삭제된 상태다.

## 결정

- `ai.context_embedding.is_deleted`(일반 컬럼)를 **소프트 삭제 마커**로 둔다. 삭제 시 즉시 물리 `DELETE` 하지 않는다.
- 진행 중 작업은 `ai.context_ai_state`를 `CANCELLED`로 전이해 무효화한다.
- 저장 직전 `FOR UPDATE`로 상태를 재검사한다. 상태가 `PROCESSING`이 아니면(`CANCELLED`로 전이된 경우 등) 계산 결과를 오류 없이 폐기한다. 이때 `is_deleted`는 복구하지 않는다.
- 검색은 `is_deleted = false`를 필터로 건다.
- 물리 삭제(하드 삭제)의 시점과 주체는 회원 탈퇴 등 별도 정책으로 분리해 정한다.

## 근거

- **경합을 상태로 흡수한다.** 대상 행이 사라지는 대신 마커로 남으므로, 진행 중 작업은 "저장 직전 상태를 확인하고, `CANCELLED`면 폐기한다"는 한 규칙으로 정리된다. 행이 물리적으로 사라지면 이 확인 자체가 NULL 처리와 예외 처리로 복잡해진다.
- **검색 정확성과 분리된다.** `is_deleted = false` 필터와 검색 결과에 대한 Core 재검증이 삭제된 Context 의 노출을 막는다. 따라서 물리 삭제를 급히 실행할 이유가 없다.
- **책임 경계를 유지한다.** `is_deleted`와 `CANCELLED`는 Spring 이 쓴다. FastAPI 는 `core.*` 테이블에 접근하지 않고, 자신이 만든 결과를 저장할지 여부를 저장 직전의 상태로만 판단한다.

## 채택하지 않은 대안

- **즉시 파기(DELETE)**: 진행 중 작업이 참조하던 행이 사라지므로 경합 처리가 NULL 처리와 재조회로 번지고, 검색 재검증 로직과 얽힌다.
  - 단, 팀원(MINYONG)은 즉시 파기를 선호했다. 이 선호는 **버려진 것이 아니다.** 아직 열려 있는 "회원 탈퇴 시 AI 파생 데이터 물리 삭제 시점" 결정에서 **'즉시 삭제(안 A)' 지지 의견**으로 이관해 기록한다. 이 ADR 이 다루는 경합 방어와 탈퇴 정책이 다루는 개인정보 파기는 서로 다른 문제이기 때문이다.

## 영향

- `ai.context_embedding`은 `is_deleted`를 일반 컬럼으로 갖고 PK 는 `context_id` 단독이다([back#3 마이그레이션](https://github.com/Team-PinLog/back/pull/3)). 복합 PK 로 만들면 UPSERT 의 `ON CONFLICT (context_id)`가 동작하지 않기 때문이다.
- 열린 결정: 회원 탈퇴 시 물리 삭제 시점(개인정보 정책). `back/docs/ai/deletion-cancellation.md` 및 `docs` 미결 목록을 참조한다.

## 검증

- 구현 명세에서 삭제·수정 경합 처리가 `CANCELLED` 중심으로 재작성됐다([deletion-race-control.md](../spec/deletion-race-control.md)).
- 계약과 draft 문서에서 "즉시 파기"·"HNSW" 잔존 0건을 확인했다.
