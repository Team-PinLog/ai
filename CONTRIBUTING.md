# PinLog AI Contributing Guide

이 문서는 PinLog AI 레포의 개발·협업 규칙에 대한 단일 기준이다. 사람과 코딩
에이전트 모두 이 문서를 따른다. 실행 절차와 리뷰 방법은
[`docs/development/`](docs/development/)에 두며, 같은 규칙을 도구별 설정이나 개인
세션 문서에 복제하지 않는다.

## 처음 읽는 순서

1. 이 문서
2. [개발 워크플로](docs/development/workflow.md)
3. [코드 리뷰 가이드](docs/development/code-review.md)
4. 작업과 관련된 [`docs/spec/`](docs/spec/) 및
   [`docs/proposals/`](docs/proposals/)
5. 테스트 작업이면 [`tests/README.md`](tests/README.md)

제품·데이터 공용 계약은 `Team-PinLog/docs`가 최상위 기준이다. AI 레포 문서가
공용 계약과 충돌하면 공용 계약을 우선하고, 임의로 해석해 구현하지 말고 소유
파트와 합의한 뒤 양쪽 문서를 갱신한다.

## 공동 진실 원천

공동 작업 상태와 결정은 다음 위치에만 남긴다.

- 작업 상태와 담당자: Jira
- 변경, 검증, 리뷰, 병합 상태: GitHub
- 계약과 장기 보존할 결정: 레포의 영구 문서

개인 Claude/Codex 대화, 세션 ID, 로컬 control board, 개인 hook과 설정은 공동
진실 원천이 아니다. 세션에서 내려진 결정은 당일 Jira 또는 영구 문서로 옮긴다.
담당자마다 독립 브랜치 또는 worktree와 독립 에이전트 세션을 사용한다.

## Jira와 Git

일반 작업은 `티켓 1개 = 브랜치 1개 = PR 1개`를 원칙으로 한다. 구현 전에 Jira
키를 발급받고 다음 형식을 사용한다.

```text
branch: {type}/{jira-key}-{summary}
commit: {type}({jira-key}): {summary}
PR:     {type}({jira-key}): {summary}
```

허용하는 type은 `feat`, `fix`, `docs`, `refactor`, `chore`, `test`, `perf`, `ci`다.

브랜치는 `back`과 같은 2단 구성이다. `dev`가 통합 브랜치이고 `main`이 배포
브랜치다. 일반 작업은 **최신 `dev`에서 분기해 `dev`를 대상으로 PR을 연다.**
`dev`와 `main` 어느 쪽에도 직접 push하지 않는다. `main`으로는 `dev`를 릴리스
시점에 병합하며, 컨테이너 이미지 publish는 `main` push에서만 일어난다
(`infra`의 GitOps 반영이 source branch를 `main`으로 어서션한다).

여러 레포에 걸친 변경은 레포별 티켓과 PR로 분리하고 Jira에서 서로 연결한다. 코드
동작에 영향이 없는 단순 오탈자만 Jira 예외가 될 수 있으며 PR 본문에 예외 근거를
적는다.

PR을 열면 Jira가 `In Progress`, squash 병합 후에는 `Done`인지 사람이 확인한다.
자동 전환이 설정되어 있더라도 성공을 가정하지 않는다.

## 구현과 문서

- 기존 코드와 테스트를 먼저 읽고, 동작 변경은 의도를 증명하는 실패 테스트
  (RED)를 먼저 만든다.
- 외부 LLM·embedding 호출은 테스트에서 fake로 대체하고 호출 횟수와 순서를
  검증한다.
- PostgreSQL 동작은 SQLite나 H2로 대체하지 않고 pgvector Testcontainers로
  검증한다.
- 되돌리기 어려운 API, 데이터, 모델, 상태 전이, 의존성 결정은 구현 전에
  `docs/proposals/` 또는 `docs/spec/`에 기록한다.
- 구현 결과는 `docs/implements/`, 장애 해결은 `docs/troubleshooting/`, 작업
  이력은 `docs/WORKLOG.md`에 보존한다.

DB migration의 V100–V199는 백엔드 레포가 소유하고 AI는
`tests/schema/ai_snapshot.sql`을 소유한다. 스키마 계약이 바뀌면 연결된 백엔드와
AI Jira·PR을 만들고, 백엔드 migration과 AI snapshot을 함께 동기화한다. AI
레포에는 백엔드의 Flyway 불변성 검사나 Java·Gradle·Checkstyle 규칙을 복사하지
않는다.

## 검증

PR을 요청하기 전에 다음을 실행한다. Docker가 없다는 이유로 DB 테스트를
생략하지 않는다.

```bash
ruff check .
python -m compileall app tools
pytest --cov=app --cov-branch --cov-report=term-missing
```

CI는 Ruff, compile 검사, pytest/Testcontainers, PR 컨테이너 빌드를 수행한다.
`app`의 line·branch coverage는 현재 기준선 측정 단계이므로 수치만으로 병합을
막지 않는다. 80% 게이트는 기준선 기록과 테스트 보강을 완료한 별도 Jira·PR에서
활성화한다. 커버리지 제외 범위를 넓히려면 PR에 근거를 남겨야 하며, 계약·통합
테스트가 수치보다 우선한다.

## PR과 리뷰

PR 본문에는 Jira, 변경 목적, RED/GREEN/Regression 증거, 번호가 붙은 리뷰 포인트,
계약·데이터·운영 리스크, 범위 밖 항목과 후속 티켓, 변경한 영구 문서를 기록한다.
계약 변경은 Draft PR 단계에서 해당 소유자에게 리뷰를 요청한다.

리뷰는 정확성, 테스트, API·데이터 계약, 보안·개인정보, 유지보수성, 티켓 범위를
확인한다. 의견마다 반영 내용 또는 반영하지 않은 근거를 답하고, 모든 리뷰 대화를
병합 전에 해결한다. 사후 리뷰를 정상 절차로 사용하지 않는다.

병합 조건은 `dev`와 `main`이 같다. 두 브랜치 모두 strict 상태 검사 **둘**, 미해결
대화 없음, 관리자 포함 보호 적용, 필수 승인 수 0이다.

```text
ai-ci / check
ai-ci / embedding profile parity
```

필수 승인 수가 0이므로 리뷰 요청과 실제 검토 여부는 Jira와 PR에서 명시적으로
확인한다. 다른 것은 조건이 아니라 **무엇을 병합하는가**다.

- **`dev`** — 일상 작업의 병합 대상. 조건을 통과하면 squash로 병합하고 기능 브랜치를
  삭제한다.
- **`main`** — 대상은 `dev`의 릴리스 병합이며 기능 브랜치를 직접 병합하지 않는다. 이
  병합이 이미지 publish를 유발하므로 `dev`에서 CI가 통과한 상태만 올린다.

위 검사 이름은 GitHub branch protection의 required status checks와 문자열까지
일치해야 한다. 검사를 추가하거나 이름을 바꿀 때는 **이 절을 먼저 고치고** 하위 문서와
GitHub 설정을 거기에 맞춘다 — 순서가 뒤집히면 낡은 값이 하위 문서로 퍼진다
(`S15P11A705-158` 실측).

## Feed 협업 경계

- Feed 런타임 구현은 Spring 기반 `Team-PinLog/back`에서 수행한다. 이 레포
  (FastAPI)에는 Feed API나 실시간 scoring 실행 코드를 추가하지 않는다.
- AI 파트는 계약을 소유한다 — deterministic scoring 정책, PUBLIC Keyword 공개
  범위, 개인정보 경계, 후보·필터·fallback·impression 의미, Feed 관련 계약 리뷰.
- 백엔드 파트는 구현을 소유한다 — 후보 조회, scoring 실행, API와 cursor,
  requestId, DB·Redis·트랜잭션, impression 저장과 중복 처리, 백엔드 테스트.
- Feed 구현은 `S15P11A705-111` 산하 Task와 `back` 레포 PR로 추적한다.
  레포가 다르면 티켓·브랜치·PR도 분리하고 서로 연결한다.
- Feed 계약 변경은 병합 전에 AI 계약 리뷰어에게, 백엔드 런타임 변경은 백엔드
  담당자와 AI 계약 리뷰어에게 요청한다.
- Feed와 운영체계 변경은 별도 티켓·브랜치·PR로 병렬 진행하며, 운영체계 PR 병합
  후 진행 중인 브랜치는 최신 `dev`를 반영한다.
