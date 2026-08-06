# P5: 정확 cosine 검색을 사용하고 HNSW/IVFFlat 근사 인덱스를 도입하지 않는다

- **상태**: Accepted
- **날짜**: 2026-07-23
- **관련 PR/커밋**: [ai#1](https://github.com/Team-PinLog/ai/pull/1), 공용 계약 [docs#2](https://github.com/Team-PinLog/docs/pull/2) `static/05` §9
- **주도(Driver)**: AI 파트

## 맥락

개인 검색은 사용자의 자연어 질의를 임베딩해, 그 사용자의 Context 임베딩과 cosine 유사도로 매칭한다. pgvector 는 정확(exact) 스캔과 근사(ANN: HNSW, IVFFlat) 인덱스를 모두 제공한다. 어느 쪽을 쓸지 정해야 한다.

## 결정

**정확(exact) cosine 검색**을 사용한다. HNSW·IVFFlat 같은 ANN 인덱스는 **도입하지 않는다.**

- 검색은 사용자 스코프(`user_id`)로 대상을 좁힌 뒤 `is_deleted = false`와 상태 필터를 적용하고, 남은 후보를 정확 cosine 으로 정렬한다.
- 벡터 컬럼에 ANN 인덱스를 만들지 않는다. 인덱스는 `user_id`·`is_deleted` 등 스칼라 조건용만 둔다.

## 근거

- **후보 규모가 작다.** 검색 대상이 **한 사용자의 Context**로 한정되므로, 정확 스캔이 훑는 행 수는 개인의 기록 수준(수십~수천 건)이다. 이 규모에서 ANN 의 속도 이점은 미미하다. 반면 정확 스캔은 조건에 맞는 행을 하나도 놓치지 않는다(recall 100%).
- **구현·운영이 단순하다.** ANN 은 인덱스 빌드가 필요하고, HNSW 는 `m`/`ef_construction`, IVFFlat 은 `lists`/`probes` 같은 파라미터 튜닝을 요구하며, recall 과 속도 사이의 트레이드오프도 조정해야 한다. MVP 단계에 이 비용은 과하다.
- **정확도 손실이 없다.** 개인 검색의 목적은 "내 기록을 정확히 되찾기"다. 근사 검색이 정답을 놓치면 사용자가 자기 기록을 못 찾는 것이므로, 체감 품질이 직접 깎인다.

## 채택하지 않은 대안

- **HNSW**: 대규모 전역 벡터 검색에 유리하지만 개인 스코프에서는 이점이 없고, 인덱스 빌드·파라미터·recall 튜닝 비용만 남는다. 초기 문서에 포함되어 있었으나 제거했다.
- **IVFFlat**: HNSW 와 마찬가지로 대규모 검색을 전제로 한 방식이다. `lists`/`probes` 튜닝과 재색인 부담이 있다.

## 영향

- 데이터와 트래픽이 크게 성장해 개인 스코프 스캔이 병목이 되면 이 결정을 재검토한다. 그 시점에 HNSW/IVFFlat 를 스코프 필터와 함께 도입할지 평가한다.
- 인덱스 설계는 스칼라 조건 중심이다([back#3](https://github.com/Team-PinLog/back/pull/3) `V101__ai_indexes.sql`).

## 검증

- 구현 명세 `personal-search.md`가 정확 cosine·사용자 스코프·필터 전제로 작성됐다.
- 계약과 draft 문서에서 HNSW/IVFFlat 잔존 0건을 확인했다.
