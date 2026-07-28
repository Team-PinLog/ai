# 구현 리포트 (Implements)

무엇을 만들었고 어떻게 검증했는지 기록합니다. `spec/`이 "무엇을 만들 것인가"라면, 여기는 "어떻게 만들었나"와 검증 결과입니다.

## 보존 원칙

이 폴더는 구현 이력을 기록합니다. **완료된 항목도 삭제하지 않고 상태 표시만 갱신합니다.** 회고·복기에서 "무엇을 어떻게 만들었는가"를 추적하기 위함입니다.

- 완료 → 문서 유지 + `상태: 완료`
- 무효화 → 문서 유지 + `상태: 무효(사유)` 표기
- 삭제 → 하지 않음. 잘못 작성된 문서도 정정으로 처리

※ `spec/`은 현재 유효한 명세이므로 이 원칙의 대상이 아닙니다(낡은 내용은 갱신·삭제).

## 개별 리포트

| 문서 | 유형 | 내용 |
|---|---|---|
| [2026-07-23-keyword-preset-seed.md](2026-07-23-keyword-preset-seed.md) | 구현 | Keyword Preset 27개 산출·검증 (ai#2) |
| [2026-07-23-architecture-diagrams.md](2026-07-23-architecture-diagrams.md) | 구현 | architecture.md 구조도(Mermaid) 4종 (ai#4) |
| [2026-07-23-keyword-matching-eval.md](2026-07-23-keyword-matching-eval.md) | 검증 | Keyword 매칭 평가 A/B/C 요약·포인터 (판정 모델 gemini-2.5-flash 확정) |
| [2026-07-23-fastapi-implementation.md](2026-07-23-fastapi-implementation.md) | 구현 | FastAPI scaffold + /context/process + /search 구현·검증 (ai#5·#6) |
| [2026-07-24-e3-test-harness.md](2026-07-24-e3-test-harness.md) | 구현 | E3 통합 테스트 하네스 + 저수준 27케이스 + 파이프라인 20 + Dockerfile + ai-ci 정비 (ai#14·#16·#18) |
| [2026-07-27-e2e-verification.md](2026-07-27-e2e-verification.md) | 검증 | E2E 실경로 — 실제 GMS 프리셋 적재·파이프라인·검색 품질·하네스 동등성·권한 경계 |
| [2026-07-28-s1-implementation-recovery.md](2026-07-28-s1-implementation-recovery.md) | 구현 | S1 세션 구현 판단 맥락 복원 — 설계선택 19·불변식·spec↔구현 불일치(구현결함)·인프라 미복원 |

> **유형**: 구현(무엇을 만들었나) / 검증(어떻게 검증했나). 검증 성격 문서가 늘면 이 컬럼이 분류 기준이 된다.
> **분리 트리거**: 리포트가 15개를 넘고 검증 유형이 절반 이상이면 `verification/` 분리를 검토한다.

## 구현·산출 — 전수 (AI 소유)

| I | 산출 | 반영처 |
|---|---|---|
| I1 | AI 공용 설계 단일 원본 `static/05_AI_설계.md`(836줄, 21 테스트 시나리오) | docs#2 |
| I2 | `static/05-1` 파트간 요구사항(front/infra) | docs#3 |
| I3 | API 상세명세 `draft/11`(디자인 화면→엔드포인트) | docs#4·#5 |
| I4 | AI 구현 명세 10문서 | [spec/](../spec/) |
| I5 | `version-race-control` → `deletion-race-control` 리네임·재작성 | [spec/deletion-race-control.md](../spec/deletion-race-control.md) |
| I9 | Keyword Preset seed 27개 | [preset-seed 리포트](2026-07-23-keyword-preset-seed.md) |
| I10 | architecture 구조도 4종 | [구조도 리포트](2026-07-23-architecture-diagrams.md) |
| I11 | 세 PR 초안(docs/ai/back 제목·본문·리뷰포인트) | docs#2·ai#1·back#1 |
| I12 | MINYONG 공유 코멘트(결정 4건) | docs#2 |
| I13 | eval 하네스 A/B/C (`tools/keyword_eval/`) | `test/keyword-matching-eval` |
| I14 | eval REPORT(A/B/C-1) — 보정 불필요·프롬프트 확정·하한 0.30 | [eval 리포트](2026-07-23-keyword-matching-eval.md) |
| I15 | `/search` 응답 `contextId` 추가(DISTINCT ON, Spring matchedContext 조립) — **소규모 변경이라 전용 리포트 없이 인벤토리만** | ai#11·docs#10, [P40](../proposals/README.md), [spec/personal-search.md](../spec/personal-search.md) |
| I16 | AI 작업기록 문서(구조도+ADR 4+트러블슈팅+리포트 5) | 이 트리 |
| I17 | 문서화 규약 메모리 | (로컬 메모리) |
| I18 | 누적 계획 파일 | (로컬 plans) |
| I19 | FastAPI 구현(scaffold + `/context/process` + `/search` + Preset 부트스트랩) | [FastAPI 리포트](2026-07-23-fastapi-implementation.md) |
| I20 | E3 통합 테스트 하네스 + 저수준 27 + 파이프라인 20 + Dockerfile + ai-ci 정비(Python 3.12·lock·Jira 검증) | [E3 리포트](2026-07-24-e3-test-harness.md), PR ai#14·#16·#18 |
| I21 | E2E 실경로 검증 + 검증 드라이버(`tools/e2e/`) — 문서 마찰 F1·F2·F5·F6, 하네스 동등성 실측, 권한 경계 실증(`-61` 근거), 시딩 가능 범위 | [E2E 리포트](2026-07-27-e2e-verification.md), [tools/e2e/](../../tools/e2e/) |
| I22 | S1 구현 판단 맥락 복원 — 설계선택 19·불변식·spec↔구현 불일치(구현결함 A·F절)·실행 인프라 미복원 | [S1 복원 리포트](2026-07-28-s1-implementation-recovery.md) |

> I6·I7·I8은 백엔드 아티팩트라 **back 레포** `docs/ai/implements`에 있습니다.
