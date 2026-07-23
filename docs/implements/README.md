# 구현 리포트 (Implements)

무엇을 만들었고 어떻게 검증했는지 기록합니다. `spec/`이 "무엇을 만들 것인가"라면, 여기는 "어떻게 만들었나"와 검증 결과입니다.

## 개별 리포트

| 문서 | 내용 |
|---|---|
| [2026-07-23-keyword-preset-seed.md](2026-07-23-keyword-preset-seed.md) | Keyword Preset 27개 산출·검증 (ai#2) |
| [2026-07-23-architecture-diagrams.md](2026-07-23-architecture-diagrams.md) | architecture.md 구조도(Mermaid) 4종 (ai#4) |
| [2026-07-23-keyword-matching-eval.md](2026-07-23-keyword-matching-eval.md) | Keyword 매칭 평가 A/B/C-1 요약·포인터 (진행 중) |

## 구현·산출 — 전수 (AI 소유)

| I | 산출 | 반영처 |
|---|---|---|
| I1 | AI 공용 설계 단일 원본 `static/05_AI_설계.md`(836줄, 21 테스트 시나리오) | docs#2 |
| I2 | `static/06` 파트간 요구사항(front/infra) | docs#3 |
| I3 | API 상세명세 `draft/11`(디자인 화면→엔드포인트) | docs#4·#5 |
| I4 | AI 구현 명세 10문서 | [spec/](../spec/) |
| I5 | `version-race-control` → `deletion-race-control` 리네임·재작성 | [spec/deletion-race-control.md](../spec/deletion-race-control.md) |
| I9 | Keyword Preset seed 27개 | [preset-seed 리포트](2026-07-23-keyword-preset-seed.md) |
| I10 | architecture 구조도 4종 | [구조도 리포트](2026-07-23-architecture-diagrams.md) |
| I11 | 세 PR 초안(docs/ai/back 제목·본문·리뷰포인트) | docs#2·ai#1·back#1 |
| I12 | MINYONG 공유 코멘트(결정 4건) | docs#2 |
| I13 | eval 하네스 A/B/C (`tools/keyword_eval/`) | `test/keyword-matching-eval` |
| I14 | eval REPORT(A/B/C-1) — 보정 불필요·프롬프트 확정·하한 0.30 | [eval 리포트](2026-07-23-keyword-matching-eval.md) |
| I16 | AI 작업기록 문서(구조도+ADR 4+트러블슈팅+리포트 3) | 이 트리 |
| I17 | 문서화 규약 메모리 | (로컬 메모리) |
| I18 | 누적 계획 파일 | (로컬 plans) |

> I6·I7·I8은 백엔드 아티팩트라 **back 레포** `docs/ai/implements`에 있습니다.
