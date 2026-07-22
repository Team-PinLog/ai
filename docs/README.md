> 현재 코드가 없는 구현 예정 명세입니다.
> 공용 계약은 Team-PinLog/docs의 `static/05_AI_설계.md`를 따릅니다.

# AI 파트 구현 문서

FastAPI AI 서버의 내부 구현 명세입니다. 공용 계약은 여기서 정의하지 않고 참조만 합니다.

## 문서 목록

| 문서 | 내용 |
|---|---|
| [architecture.md](architecture.md) | FastAPI 모듈·계층 구조, Preset Cache 위치, DB 세션 경계 |
| [context-processing.md](context-processing.md) | `POST /internal/v1/context/process` 파이프라인 구현 |
| [state-machine.md](state-machine.md) | 두 status 컬럼의 조건부 UPDATE 구현과 FastAPI 허용 전이 |
| [version-race-control.md](version-race-control.md) | `!=` Version 비교, 사전 검사와 저장 직전 `FOR UPDATE` 검사 |
| [partial-resume.md](partial-resume.md) | Embedding 재사용과 Keyword 단계 재개 |
| [personal-search.md](personal-search.md) | `POST /internal/v1/search` 벡터 검색 Query 구현 |
| [keyword-preset.md](keyword-preset.md) | Preset Cache, 후보 TOP-K, LLM 구조화 출력, 결과 저장 |
| [model-profile.md](model-profile.md) | 모델 설정과 Embedding Profile 주입 |
| [failure-recovery.md](failure-recovery.md) | 오류 분류와 복구, Spring 재스캔과의 관계 |
| [integration-tests.md](integration-tests.md) | 필수 검증 시나리오 매핑과 Fixture 전략 |

## 공용 계약

파트 간 계약의 단일 원본은 `Team-PinLog/docs` 레포의 `static/05_AI_설계.md`입니다.

각 문서 상단에는 해당 문서가 근거로 삼는 계약 절을 표기합니다. 구현 명세와 계약이
어긋나면 계약이 우선하며, 계약을 바꿔야 하는 경우 이 레포가 아니라 docs 레포에서 바꿉니다.

## 읽는 순서

1. `architecture.md` — 어디에 무엇이 있는지
2. `context-processing.md` — 주 파이프라인
3. `state-machine.md`, `version-race-control.md`, `partial-resume.md` — 정합성 3종
4. `keyword-preset.md`, `personal-search.md`, `model-profile.md` — 기능별 상세
5. `failure-recovery.md`, `integration-tests.md` — 운영과 검증
