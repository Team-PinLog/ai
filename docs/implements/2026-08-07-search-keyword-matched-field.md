# 검색 응답에 키워드 매치 여부 필드 추가

- **티켓**: S15P11A705-399
- **날짜**: 2026-08-07
- **성격**: 응답 스키마 확장. 재정렬(P49 §4, `S15P11A705-339`)의 순서·계약은 바꾸지 않는다.

## 배경

`OFFTOPIC-CONFIDENCE-GATE-HANDOFF-DRAFT.md`(중앙 조정 세션 인계 문서) §4.2가 지적한 것 — 키워드 재정렬(`_rerank_by_keyword`)은 컷 통과 후보와 Preset 후보가 실제로 match 하는지 이미 계산하지만, 그 결과는 정렬 키로만 쓰이고 버려진다. 결합 신뢰도 게이트(§4, `S15P11A705-400`)가 S3(키워드 매치) 신호를 쓰려면 이 값이 응답까지 살아 있어야 한다.

## 변경

- `_rerank_by_keyword`가 재정렬된 목록만이 아니라 실제로 match 한 Record id 집합도 함께 돌려준다(`tuple[list, set[int]]`). 재정렬이 생략되는 모든 경로(플래그 off·캐시 없음·후보 Preset 없음·조회 실패·match 없음)에서는 빈 집합을 돌려주므로 그 경로들에서 새 필드는 자연히 `False`다.
- `SearchService.search()`가 이 집합을 받아 응답 각 행에 `keywordMatched: bool`을 싣는다.
- `SearchResultItem` 스키마에 `keywordMatched: bool` 필드를 추가했다(기본값 없음 — 모든 경로가 명시적으로 채운다).

`similarity`는 여전히 원래 코사인 값 그대로다 — 이 필드는 정렬 점수를 새어 보내는 것이 아니라 이미 계산된 match 여부 하나만 싣는다.

## 검증

- 신규 단위 테스트 4건(`tests/test_search_rerank.py`) — match된 Record만 `True`, 그리고 flag off·조회 실패·후보 Preset 없음 세 경로에서 전부 `False`.
- 기존 재정렬 계약 테스트(RED/GREEN 5개)는 수정 없이 그대로 통과 — `_rerank_by_keyword`의 반환 형태가 튜플로 바뀌었지만 `search()` 내부에서만 소비하므로 외부 계약(응답의 `recordId`·`similarity` 순서·값)은 그대로다.
- 통합 테스트(`tests/test_api.py::test_search_returns_context_id`, Testcontainers)에 `keywordMatched is False` 단정을 추가했다 — 재정렬 기본 off 상태에서 실제 API 응답까지 필드가 배선됐는지 확인한다.
- `ruff check .` · `compileall app tools` · `pytest --cov` 전체 통과, 커버리지 게이트 line 94.64%·branch 85.00%(둘 다 ≥80%).
