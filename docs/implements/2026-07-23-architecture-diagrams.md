# 작업 리포트 — architecture.md 구조도(Mermaid) 보강

- **상태**: 완료
- **날짜**: 2026-07-23
- **커밋**: `210f90c` — `docs: architecture.md에 구조도(Mermaid) 4종 추가`
- **브랜치**: `docs/ai-work-records` ← `main`
- **대상**: `docs/architecture.md`

## 목표

`architecture.md`는 내용은 충실했으나 다이어그램이 전혀 없이 ASCII 텍스트 트리·표뿐이었다. 시스템 경계·진입 경로·계층 의존·트랜잭션 흐름을 **그림으로** 보강해, 구조를 한눈에 파악하고 경계 위반(FastAPI의 `core.*` 접근 금지)을 시각적으로 못박는다.

## 산출물 (다이어그램 4)

| # | 위치 | 종류 | 내용 |
|---|---|---|---|
| 1 | §2 시스템 맥락 | flowchart LR | Client→Spring→FastAPI, ai/core 스키마·Redis·외부 API. **`core.*` 접근 금지 경계를 빨간 선(`linkStyle`)으로 표시** |
| 2 | §2 시스템 맥락 | flowchart TB | 두 진입 경로 — `context/process`(비동기 202) vs `search`(동기) |
| 3 | §3 모듈 구조 | flowchart TB | 계층 의존 방향 api→service→repository/client/cache, 단방향 규칙 |
| 4 | §6.1 DB 세션 경계 | sequenceDiagram | TX1 사전검사 → TX2 조건부 PROCESSING → 모델 호출(잠금 밖) → TX3 `FOR UPDATE` 재검사·저장, 경합 시 폐기 |

- §2 시스템 맥락 신설로 이후 섹션(§3~§8)과 하위(§6.1~6.3) 번호를 재정정.
- 기존 prose·표는 유지하고 다이어그램만 삽입.

## 검증

- **문법**: `mermaid.parse`(v11 + jsdom)로 **4/4 통과**. `linkStyle`·`classDef`·`alt/else`·노드 shape 유효. → 방법은 [troubleshooting/mermaid-headless-validation.md](../troubleshooting/mermaid-headless-validation.md).
- **내부 링크**: 상대 링크 전부 유효.
- GitHub는 `mermaid` 코드펜스를 자동 렌더하므로 PR에서 그림으로 표시된다.

## 비고

- 이 다이어그램은 구조 이해용이며, 상세 규칙의 원본은 여전히 각 구현 명세 문서다.
- 후보 추가 다이어그램: 재스캔/Finalizer 상태 흐름, 배포 토폴로지(미착수).
