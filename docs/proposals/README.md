# 제안·결정 (Proposals)

이 폴더의 문서 중 상태가 **Accepted**인 것은 확정된 결정이며 구현이 따라야 한다. **Driver**는 스코프가 아니라 제안·주도 파트다.

이 폴더는 **ai 레포의 결정**이다 — AI 구현·공용 프로세스(컨벤션·문서 통합 등 AI 파트 주도분)를 `P##` 번호로 기록한다. `P##`는 전수 인벤토리의 제안 번호와 일치한다.

## 헤더 형식

```text
- **상태**: Accepted | Proposed | Rejected | Superseded by P-XX
- **날짜**:
- **주도(Driver)**:
- **관련 PR/커밋**:
```

## 개별 문서

| P | 제목 | 상태 | Driver |
|---|---|---|---|
| [P1](P1-immutable-context.md) | Context 불변 모델 (버전 컬럼 제거) | Accepted | AI |
| [P4](P4-is-deleted-cancelled.md) | 즉시 파기 대신 `is_deleted` + `CANCELLED` 마커 | Accepted | AI |
| [P5](P5-exact-cosine.md) | 정확 cosine 검색 (HNSW/IVFFlat 미도입) | Accepted | AI |
| [P26](P26-keyword-preset-judgment.md) | Keyword 프리셋 구성·후보 하한·판정 프롬프트 | Accepted (판정 모델은 M4 진행 중) | AI |

## 제안 — 전수 (Accepted)

| P | 결정 | Driver | 반영처 |
|---|---|---|---|
| P1 | Context 불변 모델(본문 in-place UPDATE 금지, 수정=삭제+INSERT, 새 context_id) | AI | [P1](P1-immutable-context.md) |
| P2 | insert-first — 신 Context를 삭제보다 먼저 → "마지막 Context 삭제 금지" 가드 자연 통과 | AI | [P1](P1-immutable-context.md), [spec/deletion-race-control.md](../spec/deletion-race-control.md) |
| P3 | 버전 컬럼 전면 제거(stale 방어는 CANCELLED) | AI | [P1](P1-immutable-context.md) |
| P4 | `is_deleted` + `CANCELLED` 마커(즉시 파기 대신) | AI | [P4](P4-is-deleted-cancelled.md) |
| P5 | 정확 cosine(ANN 미도입) | AI | [P5](P5-exact-cosine.md) |
| P6 | AI 스키마 5테이블 확정 | AI | [spec/architecture.md](../spec/architecture.md) |
| P7 | `context_embedding` PK=`context_id` 단독(UPSERT 성립), `is_deleted` 일반 컬럼·Spring 전용 | AI | [P4](P4-is-deleted-cancelled.md), [spec/architecture.md](../spec/architecture.md) |
| P8 | `context_ai_state` 두 status 분리(embedding/keyword) | AI | [spec/state-machine.md](../spec/state-machine.md) |
| P9 | FastAPI `core.*` 접근 금지(DB role ai 한정, 조인은 백엔드 방향만) | AI(+infra) | [spec/architecture.md](../spec/architecture.md) |
| P11 | PROCESSING 재선점 = 만료 건 한정(멱등성) | AI | [spec/deletion-race-control.md](../spec/deletion-race-control.md) |
| P12 | Keyword Visibility 3등급(PUBLIC/PRIVATE_ONLY/BLOCKED, MVP에 BLOCKED 없음) | AI | [spec/keyword-preset.md](../spec/keyword-preset.md) |
| P14 | API 기본 경로 `/api/core/v1` | 공용(AI 주도) | 계약 static/05 |
| P15 | Conventional Commits + 브랜치 규율(main 직접 커밋 금지) | 공용(AI 주도) | [../WORKLOG.md](../WORKLOG.md) |
| P16 | `static/05` 단일 원본 통합 | 공용(AI 주도) | docs#2 |
| P17 | `static/06` 파트간 요구사항 신설 | 공용(AI 주도) | docs#3 |
| P18 | 수정 UX = 삭제+생성(버튼 유지, 새 contextId) | AI(+front) | [spec/deletion-race-control.md](../spec/deletion-race-control.md) |
| P19 | draft/06 §7 "AI 워커가 core.context 확인" 제거 → CANCELLED 방어 | AI | [P4](P4-is-deleted-cancelled.md) |
| P20 | `core.context.updated_at` 제거(불변이라 불필요) | 공용 | 계약 |
| P26 | Keyword 프리셋 구성·후보 하한·판정 프롬프트 | AI | [P26](P26-keyword-preset-judgment.md) |
| P27 | `keyword_preset.yaml` 경로 = ai 레포 `data/` | AI | [implements](../implements/2026-07-23-keyword-preset-seed.md) |
| P28 | 판정 유사도 하한 0.30(eval) | AI | [P26](P26-keyword-preset-judgment.md) |
| P29 | LLM 판정 프롬프트 확정 + 부대시설 제외 규칙(eval C-1) | AI | [P26](P26-keyword-preset-judgment.md) |
| P30 | 후보 TOP-K 기본 10, 후보 0개면 LLM 미호출·선택 0 정상 | AI | [spec/keyword-preset.md](../spec/keyword-preset.md) |
| P31 | LLM 구조화 출력 `{selected:[{keywordId,confidence}]}`, 후보 밖 id 폐기 | AI | [spec/keyword-preset.md](../spec/keyword-preset.md) |
| P32 | 임베딩 계약 text-embedding-3-small/1536/cosine, `PINLOG_EMBEDDING_PROFILE` | AI | [spec/model-profile.md](../spec/model-profile.md) |
| P34 | ai 레포 기본 브랜치 main 변경 | AI | — |
| P35 | 문서화 3계층(계약/작업기록 분리) | 공용(AI 주도) | 이 트리 |
| P36 | ADR 스코프 = 레포 단위, 파트는 Driver 메타 | 공용(AI 주도) | 이 README |
| P37 | 분류 3범주 proposals/implements/troubleshooting + spec | 공용(AI 주도) | 이 트리 |
| P38 | rebase Option B(MINYONG 독립작업 위 재정리) | AI | [troubleshooting](../troubleshooting/) |

> P10·P13·P21~P25·P33·P39는 백엔드 아티팩트 결정이라 **back 레포** `docs/ai/proposals`에 있습니다.

## 미결 (Open)

| M | 쟁점 | Driver | 상태 |
|---|---|---|---|
| M1 | 탈퇴/삭제 시 AI 파생 데이터 물리 삭제 시점(즉시(안 A)/유예/소프트) | 공용(개인정보) | 미결 |
| M2 | Context 목록 `created_at` 기준(최초 vs 현재), `origin_context_id` 계보 — core V2~ blocker | 공용 | 미결 |
| M3 | Embedding Profile 전환 재처리 경로(§6.3 좁히기 + §7.3 운영 절) | AI | 미결 |
| M4 | eval 판정 LLM 모델 확정(C-2 모델 비교) | AI | 미결(진행 중, 타 세션) |
| M5 | `09_유저플로우` draft/static 중복 정리 | 공용 | 미결 |
| M6 | `FOLLOWUP_IMPLEMENTATION.md` staleness(version 개념 잔존) | AI | 미결(경미) |
| **M7** | **검색 장소·시간 필터 — 매칭 테스트 후 최우선 고려**(Spring 내부 Place 텍스트 사전 매칭 방식, FastAPI·프론트 무변경) | 백엔드(AI 협의) | 미결 |
