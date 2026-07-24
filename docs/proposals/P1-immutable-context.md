# P1: Context 불변 모델 (버전 컬럼 제거)

- **상태**: Accepted
- **날짜**: 2026-07-23
- **관련 PR/커밋**: [ai#1](https://github.com/Team-PinLog/ai/pull/1) (`cbd776d` align immutable), 공용 계약 [docs#2](https://github.com/Team-PinLog/docs/pull/2) `static/05` §4.2
- **주도(Driver)**: AI 파트

## 맥락

초기 설계는 Context 수정 중 발생하는 경합(사용자가 본문을 고치는 동안 AI가 임베딩·키워드를 만드는 상황)을 막으려고 `context_version`(또는 `body_version`) 컬럼을 두고, 임베딩·키워드·검색 결과를 버전과 함께 저장·비교했다. 그러면 모든 AI 테이블에 버전이 전파되고, 검색 SQL·저장 로직마다 "지금 이 결과가 최신 본문 것인가"를 버전으로 판정해야 한다. 방어 로직이 버전 비교로 곳곳에 퍼진다.

## 결정

Context를 **불변(immutable) 엔티티**로 정의한다.

```text
동일한 context_id는 항상 동일한 Context 본문을 의미한다.
```

- 본문을 in-place로 UPDATE하지 않는다.
- 수정 = **구 Context 소프트 삭제 + 신 Context INSERT**(새 `context_id`). 두 동작은 한 Core 트랜잭션.
- 신 Context INSERT를 삭제보다 **먼저** 실행한다.
- `context_version`·`body_version` 등 본문 세대 컬럼을 **전면 제거**한다.

## 근거

- **방어해야 할 가변 상태 자체를 없앤다.** 같은 `context_id`가 항상 같은 본문이면, "이 임베딩이 최신 본문 것인가"라는 질문이 성립하지 않는다. 버전 비교 로직이 통째로 사라진다.
- **stale 결과 차단을 한 곳으로 모은다.** 수정으로 무효가 된 진행 중 작업은 `ai.context_ai_state`의 `CANCELLED`로 걸러진다([P4](P4-is-deleted-cancelled.md)) — 버전이 아니라 상태로.
- **"마지막 Context는 삭제 불가" 가드를 우회 없이 통과한다.** 신 Context를 삭제보다 먼저 넣으면, 삭제 시점에 Context가 최소 하나 남아 가드가 자연히 만족된다. 가드에 수정 경로 특례를 두지 않아도 된다.

## 버린 대안

- **`context_version` 유지**: 경합은 막지만 버전이 전 테이블·전 쿼리로 퍼지고, 수정마다 버전 증가·전파·비교를 관리해야 한다. 불변 모델이 같은 목표를 더 적은 상태로 달성한다.
- **본문 in-place UPDATE + 재처리 트리거**: 수정 순간 임베딩·키워드가 잠시 옛 본문과 불일치하고, 그 창(window)을 버전이나 락으로 가려야 한다. 불변 모델은 그 창이 없다(새 id는 처음부터 PENDING).

## 영향

- 모든 `ai` 테이블에서 버전 컬럼 제거([back#3 마이그레이션](https://github.com/Team-PinLog/back/pull/3)이 반영).
- FastAPI 구현: 저장 불변식이 `status == PROCESSING`으로 단순화, 검색 SQL·키워드 저장에서 버전 조건 삭제([deletion-race-control.md](../spec/deletion-race-control.md)).
- Front: Context 수정 시 응답의 `context_id`가 바뀐다 → 새 id 반영, 구 id 캐시키 금지(`docs/static/05-1` §1).

## 검증

- 공용 계약·draft 문서에서 `context_version`/`body_version` 잔존 0건 확인(rebase 검증 스크립트).
- 구현 명세 전반이 버전 없는 전제로 재작성됨([ai#1](https://github.com/Team-PinLog/ai/pull/1)).
