# P4: 즉시 파기 대신 `is_deleted` + `CANCELLED` 마커

- **상태**: Accepted
- **날짜**: 2026-07-23
- **관련 PR/커밋**: [ai#1](https://github.com/Team-PinLog/ai/pull/1), 공용 계약 [docs#2](https://github.com/Team-PinLog/docs/pull/2) `static/05`
- **주도(Driver)**: AI 파트

## 맥락

Context·Record가 삭제되거나 수정으로 구 Context가 무효화될 때, 파생된 임베딩(`ai.context_embedding`)과 키워드(`ai.context_keyword`)를 어떻게 정리할지 정해야 한다. 두 갈래가 있었다.

- **즉시 파기**: 삭제 즉시 파생 행을 물리 `DELETE`.
- **마커**: `is_deleted = true`로 표시하고, 진행 중 AI 작업 상태를 `CANCELLED`로 전이.

문제는 **삭제와 AI 처리가 경합**한다는 점이다. FastAPI가 임베딩을 계산하는 도중 사용자가 Context를 지우면, 계산이 끝나 저장하려는 순간 대상은 이미 삭제 대상이다.

## 결정

- `ai.context_embedding.is_deleted`(일반 컬럼)를 **소프트 삭제 마커**로 둔다. 삭제 시 즉시 물리 `DELETE`하지 않는다.
- 진행 중 작업은 `ai.context_ai_state`를 `CANCELLED`로 전이해 무효화한다.
- 저장 직전 `FOR UPDATE`로 상태를 재검사하고, `PROCESSING`이 아니면(=`CANCELLED` 등) 결과를 **조용히 폐기**한다. `is_deleted`는 복구하지 않는다.
- 검색은 `is_deleted = false`를 필터로 건다.
- 물리 삭제(하드 삭제)의 시점·주체는 별도 정책(회원 탈퇴 등)으로 분리한다.

## 근거

- **경합을 상태로 흡수한다.** 대상 행이 사라지는 대신 마커로 남아, 진행 중 작업은 "저장 직전 상태 확인 → CANCELLED면 폐기"라는 한 규칙으로 정리된다. 행이 물리적으로 사라지면 이 확인 자체가 NULL·예외 처리로 복잡해진다.
- **검색 정확성과 분리.** `is_deleted=false` 필터 + 검색 결과의 Core 재검증으로 노출을 막으므로, 물리 삭제를 급히 할 이유가 없다.
- **책임 경계 유지.** `is_deleted`/`CANCELLED`는 Spring이 쓴다. FastAPI는 `core.*`에 접근하지 않고, 자신이 만든 결과를 저장 직전 상태로만 판단한다.

## 버린 대안

- **즉시 파기(DELETE)**: 진행 중 작업이 참조하던 행이 사라져 경합 처리가 NULL·재조회로 번지고, 검색 재검증 로직과 얽힌다.
  - 단, 팀원(MINYONG)은 즉시 파기를 선호했다. 이 선호는 **버려진 게 아니라**, 아직 열린 "회원 탈퇴 시 AI 파생 데이터 물리 삭제 시점" 결정에서 **'즉시 삭제(안 A)' 지지 의견**으로 이관해 기록한다. 경합 방어(이 ADR)와 개인정보 파기(탈퇴 정책)는 다른 문제다.

## 영향

- `ai.context_embedding`은 `is_deleted`를 일반 컬럼으로 갖고 PK는 `context_id` 단독([back#3 마이그레이션](https://github.com/Team-PinLog/back/pull/3) — 복합 PK면 UPSERT `ON CONFLICT (context_id)`가 깨짐).
- 열린 결정: 회원 탈퇴 시 물리 삭제 시점(개인정보 정책). `back/docs/ai/deletion-cancellation.md` 및 `docs` 미결 목록 참조.

## 검증

- 구현 명세에서 삭제·수정 경합이 `CANCELLED` 중심으로 재작성됨([deletion-race-control.md](../spec/deletion-race-control.md)).
- 계약·draft에서 "즉시 파기"·"HNSW" 잔존 0건 확인.
