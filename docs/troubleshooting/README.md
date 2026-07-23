# 트러블슈팅 (Troubleshooting)

구현·문서 작업 중 겪은 문제와 그 해결을 재현 가능한 형태로 남깁니다.

## 개별 문서

| 문서 | 내용 |
|---|---|
| [mermaid-headless-validation.md](mermaid-headless-validation.md) | Mermaid 다이어그램 브라우저리스 문법 검증 (T7·T8) |

## 문제 해결 — 전수 (AI 소유)

| T | 증상 | 해결 |
|---|---|---|
| T1 | main에 직접 커밋됨 | 백업→revert→피처 브랜치 재적용, Conventional Commits 채택 |
| T2 | draft/06·07·09 rebase 충돌(MINYONG 독립 반영) | Option B rebase, 비-AI 개선 보존 + AI 스키마 교체, force-with-lease |
| T3 | PR3 "가드 수정경로 적용 금지" ↔ PR1 모순 | insert-first면 가드 자연통과 → "그대로 통과·특례 금지"로 통일 |
| T4 | `updated_at` 확정/미결 엇갈림 | MINYONG 안 채택으로 확정 통일(제거) |
| T5 | `gh: command not found`(bash) | `C:\Program Files\GitHub CLI\gh.exe` 전체경로 호출 |
| T6 | ai 레포 기본 브랜치가 피처 브랜치 | `gh repo edit`로 main 변경 |
| T7 | Mermaid 검증 실패(@mermaid-js/parser는 flowchart 미지원, jsdom navigator getter-only) | mermaid@11+jsdom, `Object.defineProperty`로 navigator 우회 → 4/4 valid |
| T8 | Mermaid 추출 파싱 오류(CRLF) | 정규식 `.replace(/\r\n/g,"\n")` |
| T11 | 마이그레이션 실검증 필요 | pgvector 컨테이너에서 V1→V102 순차·PK·extension·중복스키마 실패 확인 |
| T12 | preset YAML 규칙 위반 방지 | Python 스크립트로 개수/배분/유일/visibility/examples 검증 |
| T13 | docs `11_개발_컨벤션.md` 삭제 부작용(pgvector 검토 유일 출처) | 소실 내용 식별·고지 |
| T14 | `docs/ai-architecture-diagrams` 브랜치가 eval 하네스 커밋 위에 오정렬 | main 기준 재정렬(`rebase --onto`) |
| T15 | back ADR "소유 파트: AI 파트" 혼란(표준 ADR엔 소유파트 필드 없음) | 주도(Driver)로 격하 + 레포 스코프 명시(P36) |

> T9(H2·pgvector)·T10(flyway.schemas)은 백엔드 아티팩트라 **back 레포** `docs/ai/troubleshooting`에 있습니다.
