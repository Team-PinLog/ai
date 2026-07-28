# AI 개발 워크플로

상세 규칙은 [`CONTRIBUTING.md`](../../CONTRIBUTING.md)가 기준이다. 이 문서는
하나의 작업을 시작해 `main`에 병합하는 실행 순서를 정리한다.

```text
Jira 발급·담당자 지정
  → 최신 main에서 독립 브랜치 생성
  → 실패 테스트와 RED 증거
  → 최소 구현과 GREEN 증거
  → 전체 Regression 검증
  → 영구 문서와 WORKLOG 갱신
  → Draft PR 및 계약 소유자 리뷰 요청
  → strict ai-ci / check와 리뷰 대화 해결
  → squash 병합·브랜치 삭제
  → Jira Done 상태 확인
```

## 1. Jira와 범위

구현 전에 Jira 키, 담당자, 완료 조건, 범위 밖 항목을 확정한다. 여러 레포를
변경하면 레포별 티켓과 PR을 만들고 서로 연결한다. Feed처럼 여러 PR이 필요한
기능은 Epic 아래에 PR 크기의 Task를 둔다.

계약이나 스키마 경계를 바꾸는 판단은 코드보다 먼저 `docs/proposals/` 또는
`docs/spec/`에 남긴다.

## 2. 브랜치

최신 `origin/main`에서 `{type}/{jira-key}-{summary}` 브랜치를 만든다. 담당자별로
독립 branch/worktree를 사용하며 다른 사람의 worktree를 공유하지 않는다.

진행 중 운영체계가 `main`에 병합되면 작업 브랜치에서 최신 `main`을 반영하고
회귀검증을 다시 수행한다.

## 3. RED, GREEN, Regression

1. 변경 의도를 재현하는 테스트를 작성하고 실패 결과를 RED로 기록한다.
2. 해당 테스트를 통과시키는 최소 구현을 하고 GREEN을 기록한다.
3. `ruff check .`, compile 검사, 전체 pytest를 실행해 Regression을 기록한다.
4. DB 계약은 pgvector Testcontainers로 검증한다.

문서 전용 변경은 실행 가능한 링크·명령·CI 계약을 검증하고, 적용할 수 없는
RED/GREEN에는 그 이유를 PR에 적는다.

## 4. PR과 리뷰

PR 제목과 본문은 템플릿을 따른다. 계약 변경은 Draft 단계에서 소유자를
reviewer로 지정한다. PR 생성 후 Jira `In Progress`를 확인한다.

병합 전 다음 조건을 모두 확인한다.

- 최신 `main` 기준 `ai-ci / check` 성공
- 모든 리뷰 대화 해결
- PR 본문의 검증 증거와 리스크가 최신 상태
- 필요한 영구 문서와 후속 Jira 티켓 존재

승인 수는 기술적으로 강제하지 않으므로 “reviewer 지정”을 “검토 완료”로
간주하지 않는다.

## 5. 병합 후

squash로 병합하고 기능 브랜치를 삭제한다. Jira가 `Done`인지 직접 확인하고,
병합 후 새로 발견된 작업은 기존 완료 범위에 숨기지 말고 후속 티켓으로 만든다.
