# PinLog AI

PinLog의 FastAPI AI 서버 레포지토리입니다.

> **현재 이 레포에는 구현 코드가 없습니다.** 여기 있는 것은 구현 예정 명세뿐이며,
> FastAPI 애플리케이션·의존성 정의·마이그레이션·배포 스크립트는 아직 작성되지 않았습니다.

## 이 서버의 역할

FastAPI AI 서버는 PinLog에서 **AI 계산과 AI 파생 데이터 처리**를 담당합니다.

- Context Embedding 생성과 저장
- Keyword Preset 후보 벡터 검색과 LLM 최종 판정
- 개인 Context 자연어 검색(질의 Embedding + exact cosine)
- AI State 기반 멱등 실행과 부분 재개

담당하지 않는 것:

- Core 도메인(`core.*` 스키마) 접근과 상태 변경
- User 인증·권한 판단
- Feed API 제공과 Feed 점수 계산
- DB Migration 실행 (`ai` 스키마 포함, back 레포가 실행 주체)

Client는 이 서버를 직접 호출하지 않습니다. 모든 외부 요청은 Spring Backend를 거치며,
FastAPI는 내부 네트워크에서 `/internal/v1/*` 계약으로만 노출됩니다.

## 문서

구현 명세는 [`docs/`](docs/README.md)에 있습니다.

## 공용 계약

파트 간 공용 계약(원칙, 상태 정의, 테이블 역할, 내부 API 계약, 필수 검증 시나리오)의
단일 원본은 이 레포가 아니라 **`Team-PinLog/docs` 레포의 `static/05_AI_설계.md`** 입니다.

`docs/` 아래 문서는 그 계약을 전문 복사하지 않고 참조하며, 계약과 구현 명세가 어긋나면
`static/05_AI_설계.md`가 우선합니다.

## 연동 대상

| 파트 | 스택 | 비고 |
|---|---|---|
| Spring Backend | Java 21 / Spring Boot 4.1.0 | 패키지 `com.pinlog.pinlogback`, Core 도메인과 최종 응답 담당 |
| PostgreSQL + pgvector | `ai` 스키마 | FastAPI에 부여하는 DB 권한은 `ai` 스키마로 한정 |
