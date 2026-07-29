# P44: AI 레포 협업 운영 기준

- **상태**: Accepted
- **날짜**: 2026-07-28
- **주도(Driver)**: AI
- **관련 Jira**: S15P11A705-108
- **관련 PR/커밋**: S15P11A705-108 PR

## 결정

백엔드 레포의 Jira-first, TDD 증거, 영구 문서, 리뷰 대화 해결 원칙을 AI
레포의 Python/FastAPI 환경에 맞게 수용한다. AI의 단일 기준은
[`CONTRIBUTING.md`](../../CONTRIBUTING.md)이며, 도구별 안내 문서는 이 기준을
복제하지 않고 읽기 순서만 제공한다.

Jira, GitHub, 레포 영구 문서만 공동 진실 원천으로 사용한다. 개인 Claude/Codex
세션, control board, hook과 로컬 설정은 팀 상태로 사용하지 않는다.

## 수용 항목과 이유

| 항목 | 이유 |
|---|---|
| 티켓 1개 = 브랜치 1개 = PR 1개 | 목적, 코드, 검증, 배포 책임을 추적할 수 있다. |
| RED/GREEN/Regression PR 증거 | 구현 결과뿐 아니라 변경 의도와 회귀 안전성을 리뷰할 수 있다. |
| 계약 결정 선문서화 | API·데이터·모델 경계가 세션 기억에만 남는 것을 막는다. |
| 미해결 리뷰 대화 병합 차단 | 승인 수보다 실제로 발견된 문제의 해결 여부를 강제한다. |
| 관리자 포함 branch protection | 긴급·편의성에 의한 우회를 감사 가능한 예외로 바꾼다. |
| `app` branch coverage 단계 도입 | 현재 개발을 중단시키지 않고 측정 가능한 품질 게이트로 이동한다. |
| 담당자별 독립 worktree·세션 | 다른 사람의 미완성 변경과 개인 AI 상태가 섞이는 것을 막는다. |

## 수용하지 않는 항목

| 항목 | 이유 |
|---|---|
| Flyway migration 불변성 검사 | V100–V199 migration은 백엔드가 소유하며 AI는 snapshot 소비자다. |
| Java·Gradle·Checkstyle·Spring 규칙 | Python/FastAPI 실행 환경과 검증 지점이 다르다. |
| 백엔드 문서의 전체 복제 | 중복된 규칙은 시간이 지나며 서로 달라진다. |
| 개인 `.claude` board·session·hook 공유 | 로컬 종속, 노후화, 개인 설정 노출 위험이 있다. |

스키마 변경 시에는 Flyway 검사를 복제하는 대신 연결된 백엔드·AI Jira와 PR,
백엔드 migration, AI `tests/schema/ai_snapshot.sql` 동기화를 리뷰에서 확인한다.

## 리스크와 완화

| 리스크 | 완화 |
|---|---|
| 필수 승인 수가 0이라 아무 의견 없이 병합 가능 | Jira와 PR에 reviewer를 명시하고 계약 변경은 Draft 단계에서 요청한다. |
| 문서가 여러 위치에서 충돌 | 규칙은 `CONTRIBUTING.md` 한 곳에만 두고 나머지는 링크와 실행 순서만 둔다. |
| Jira 행정 비용 증가 | 코드 동작에 영향 없는 단순 오탈자만 근거를 적어 예외 처리한다. |
| coverage 수치 맞추기용 테스트 증가 | 계약·실패 경로와 Testcontainers 통합 테스트를 우선하고 제외 확대를 리뷰한다. |
| CI 시간 증가 | 먼저 측정하며, 80% 차단은 테스트 보강 후 별도 Jira·PR에서 활성화한다. |
| Feed와 운영체계 변경 충돌 | 별도 티켓·브랜치·worktree로 병렬 진행하고 운영체계 병합 후 최신 `main`을 반영한다. |
| 문서와 실제 GitHub 설정 불일치 | branch protection 변경 직후 API로 설정을 재조회한다. |

## 단계적 coverage 기준

S15P11A705-108의 최초 기준선은 Python 3.12, 52 tests에서 line 76.95%
(474/616), branch 62.5% (55/88)다. 이 단계에서는 보고서만 CI artifact로
보존하고 낮은 수치로 실패시키지 않는다.

테스트 보강과 line·branch 80% 게이트 활성화는 후속 S15P11A705-110에서 수행한다.
