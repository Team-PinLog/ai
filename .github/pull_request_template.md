<!--
제목: <type>(<JIRA-KEY>): <간결한 설명>
예: feat(S15P11A705-14): 개인 검색 API 추가
type: feat | fix | docs | refactor | chore | test | perf | ci
-->

## 요약
<!-- 무엇을 왜 바꾸는지 1~3줄 -->

## Jira (필수)
- 키 또는 URL:
- 완료 조건:

## 변경 사항
-

## 테스트 / 검증
<!-- 명령, exit code, 핵심 결과를 적습니다. 문서 전용이면 적용 불가 사유를 적습니다. -->

### RED
- 변경 전 실패 증거:

### GREEN
- 목표 테스트 통과 증거:

### Regression
- [ ] `ruff check .`
- [ ] `python -m compileall app tools`
- [ ] `pytest --cov=app --cov-branch --cov-report=term-missing`
- [ ] DB 계약 변경 시 pgvector(PostgreSQL) Testcontainers 검증

## 리뷰 포인트
<!-- 번호를 붙여 리뷰어가 판단할 지점을 명확히 합니다. -->
1.

## 리스크
- 계약:
- 데이터·개인정보:
- 운영·배포:

## 범위 밖 / 후속
- 이번 PR에서 다루지 않는 항목:
- 후속 Jira:

## 영구 문서
<!-- 변경한 spec/proposal/implements/troubleshooting/WORKLOG. 없으면 이유. -->
-

## 관련 GitHub Issue (선택)
-
