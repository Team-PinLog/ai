# architecture.md 구조도 보강 — Mermaid 다이어그램 4종을 추가하고 문법 검증을 통과했다

- **상태**: 완료
- **날짜**: 2026-07-23
- **커밋**: `210f90c` — `docs: architecture.md에 구조도(Mermaid) 4종 추가`
- **브랜치**: `docs/ai-work-records` ← `main`
- **대상**: `docs/architecture.md`

## 배경

`architecture.md`는 설명 내용은 충실했지만 다이어그램이 하나도 없었고, 구조 정보가 ASCII 텍스트 트리와 표로만 표현되어 있었다. 이 작업은 시스템 경계·진입 경로·계층 의존·트랜잭션 흐름을 그림으로 보강한다. 목적은 두 가지다. 독자가 구조를 한눈에 파악할 수 있게 하고, 경계 위반 규칙(FastAPI 는 `core.*` 스키마에 접근하지 않는다)을 시각적으로 분명하게 표시한다.

## 완료한 작업 — 다이어그램 4종

1. **시스템 맥락** (§2 시스템 맥락, flowchart LR) — Client → Spring → FastAPI 로 이어지는 호출 관계와 ai/core 스키마·Redis·외부 API 의 위치를 그렸다. FastAPI 가 `core.*` 에 접근하면 안 된다는 금지 경계를 빨간 선(`linkStyle`)으로 표시했다.
2. **진입 경로** (§2 시스템 맥락, flowchart TB) — 두 진입 경로를 비교했다. `context/process`는 202 응답을 먼저 반환하는 비동기 경로이고, `search`는 동기 경로다.
3. **계층 의존** (§3 모듈 구조, flowchart TB) — api → service → repository/client/cache 로 향하는 계층 의존 방향과, 역방향 의존을 금지하는 단방향 규칙을 그렸다.
4. **DB 세션 경계** (§6.1 DB 세션 경계, sequenceDiagram) — 트랜잭션 흐름을 그렸다. TX1 에서 사전검사를 하고, TX2 에서 조건부로 PROCESSING 전이를 수행하고, 모델 호출은 잠금 밖에서 실행한 뒤, TX3 에서 `FOR UPDATE` 로 재검사하고 저장한다. 경합이 확인되면 결과를 폐기한다.

문서 구조도 함께 정리했다.

- §2 시스템 맥락 절을 신설했다. 이에 따라 이후 섹션(§3~§8)과 하위 절(§6.1~6.3)의 번호를 다시 매겼다.
- 기존 문장과 표는 그대로 유지하고 다이어그램만 삽입했다.

## 검증

- **문법**: `mermaid.parse`(v11 + jsdom)로 다이어그램 4종 전부 파싱을 통과했다(4/4). `linkStyle`·`classDef`·`alt/else`·노드 shape 문법이 유효함을 확인했다. 검증 방법은 [troubleshooting/mermaid-headless-validation.md](../troubleshooting/mermaid-headless-validation.md)에 기록했다.
- **내부 링크**: 문서 안의 상대 링크가 전부 유효함을 확인했다.
- GitHub 는 `mermaid` 코드펜스를 자동 렌더링하므로 PR 화면에서 그림으로 표시된다.

## 비고

- 이 다이어그램은 구조 이해를 돕기 위한 것이다. 상세 규칙의 원본은 여전히 각 구현 명세 문서다.
- 추가 후보 다이어그램으로 재스캔/Finalizer 상태 흐름과 배포 토폴로지가 있다. 아직 착수하지 않았다.
