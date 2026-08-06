# P1: Context 를 불변 엔티티로 정의하고 버전 컬럼을 제거한다

- **상태**: Accepted
- **날짜**: 2026-07-23
- **관련 PR/커밋**: [ai#1](https://github.com/Team-PinLog/ai/pull/1) (`cbd776d` align immutable), 공용 계약 [docs#2](https://github.com/Team-PinLog/docs/pull/2) `static/05` §4.2
- **주도(Driver)**: AI 파트

## 맥락

사용자가 Context 본문을 고치는 동안 AI가 같은 Context 의 임베딩과 키워드를 만들고 있으면, 완성된 임베딩이 수정 전 본문을 가리키는 경합이 생긴다. 초기 설계는 이 경합을 막으려고 `context_version`(또는 `body_version`) 컬럼을 두고, 임베딩·키워드·검색 결과를 버전과 함께 저장하고 비교하는 방식이었다. 이 방식에서는 모든 AI 테이블에 버전 컬럼이 전파된다. 검색 SQL 과 저장 로직마다 "지금 이 결과가 최신 본문의 것인가"를 버전으로 판정해야 한다. 결국 방어 로직이 버전 비교의 형태로 코드 곳곳에 퍼진다.

## 결정

Context 를 **불변(immutable) 엔티티**로 정의한다.

```text
동일한 context_id는 항상 동일한 Context 본문을 의미한다.
```

- 본문을 같은 행에서 UPDATE 하지 않는다.
- 수정은 구 Context 를 소프트 삭제하고 신 Context 를 새 `context_id`로 INSERT 하는 두 동작으로 처리한다. 두 동작은 하나의 Core 트랜잭션 안에서 실행한다.
- 신 Context INSERT 를 구 Context 삭제보다 **먼저** 실행한다.
- `context_version`·`body_version` 등 본문 세대를 나타내는 컬럼을 **전면 제거**한다.

## 근거

- **방어해야 할 가변 상태 자체를 없앤다.** 같은 `context_id`가 항상 같은 본문을 가리키면, "이 임베딩이 최신 본문의 것인가"라는 질문 자체가 성립하지 않는다. 그 질문에 답하기 위한 버전 비교 로직이 통째로 사라진다.
- **오래된 결과의 차단을 한 곳으로 모은다.** 수정으로 무효가 된 진행 중 작업은 버전 비교가 아니라 상태로 걸러진다. `ai.context_ai_state`가 `CANCELLED`로 전이되면 그 작업의 결과는 저장되지 않는다([P4](P4-is-deleted-cancelled.md)).
- **"마지막 Context 는 삭제 불가" 가드를 우회 없이 통과한다.** 신 Context 를 삭제보다 먼저 넣으면, 삭제를 실행하는 시점에 Context 가 최소 하나 남아 있어 가드 조건이 자연히 만족된다. 가드에 수정 경로만을 위한 특례를 두지 않아도 된다.

## 채택하지 않은 대안

- **`context_version` 유지**: 경합은 막지만 버전이 모든 테이블과 모든 쿼리로 퍼지고, 수정할 때마다 버전 증가·전파·비교를 관리해야 한다. 불변 모델이 같은 목표를 더 적은 상태로 달성한다.
- **본문을 같은 행에서 UPDATE 하고 재처리를 트리거**: 수정 직후 임베딩·키워드가 잠시 수정 전 본문과 불일치하는 기간이 생기고, 그 기간을 버전이나 락으로 가려야 한다. 불변 모델에는 그 기간이 없다. 새 `context_id`는 처음부터 PENDING 상태로 시작하기 때문이다.

## 영향

- 모든 `ai` 테이블에서 버전 컬럼을 제거한다([back#3 마이그레이션](https://github.com/Team-PinLog/back/pull/3)이 반영).
- FastAPI 구현에서 저장 불변식이 `status == PROCESSING` 확인 하나로 단순해지고, 검색 SQL 과 키워드 저장에서 버전 조건이 삭제된다([deletion-race-control.md](../spec/deletion-race-control.md)).
- Front 는 Context 수정 시 응답의 `context_id`가 바뀐다는 점을 반영해야 한다. 새 id 를 반영하고, 구 id 를 캐시 키로 쓰지 않는다(`docs/static/05-1` §1).

## 검증

- 공용 계약과 draft 문서에서 `context_version`/`body_version` 잔존 0건을 확인했다(rebase 검증 스크립트).
- 구현 명세 전반이 버전 없는 전제로 재작성됐다([ai#1](https://github.com/Team-PinLog/ai/pull/1)).
