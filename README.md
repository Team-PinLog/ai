# PinLog AI

PinLog의 FastAPI AI 서버. **AI 계산과 AI 파생 데이터 처리**를 담당합니다.

- Context Embedding 생성·저장
- Keyword Preset 후보 벡터 검색 + LLM 최종 판정(`gemini-2.5-flash`)
- 개인 Context 자연어 검색(질의 Embedding + exact cosine)
- AI State 기반 멱등 실행·부분 재개

담당하지 않는 것: Core 도메인(`core.*`) 접근·상태 변경, User 인증, Feed, DB Migration 실행(back 소유).
Client는 이 서버를 직접 호출하지 않으며, `/internal/v1/*` 계약으로 내부 네트워크에서만 노출됩니다.

## 구성

계층형 구조(`docs/spec/architecture.md` §2): `api → service → {repository, cache, client}`.

```
app/
├── main.py                  # lifespan, 라우터, 미들웨어, /health
├── api/probe.py             # /ready (readiness)
├── api/internal/v1/         # context.py(/context/process), search.py(/search)
├── service/                 # context_processing, embedding, keyword, search
├── repository/              # ai_state, context_embedding, context_keyword, keyword_preset
├── client/                  # embedding_client(GMS), llm_client(Gemini)
├── cache/preset_cache.py    # Preset 메모리 캐시
├── core/                    # config, db, errors, security, logging
├── bootstrap/load_presets.py
└── smoke/gms_roundtrip.py   # GMS 양방향 실호출 스모크(배포 게이트)
data/keyword_preset.yaml     # Preset 시드(27개)
tests/                       # 통합 테스트(Testcontainers) — tests/README.md 참고
```

## 환경

Python **3.12 고정**(`.python-version`, `pyproject.toml`). 챗봇/GraphRAG 스택 대비 상한 `<3.13`.

```bash
py -V:3.12 -m venv .venv                    # Windows (또는 python3.12 -m venv)
.venv/Scripts/pip install -r requirements.lock -r requirements-dev.lock
```

`requirements.txt`는 사람용 하한, `requirements.lock`/`requirements-dev.lock`은 정확 버전(CI·Docker 설치 기준).

## 로컬 기동

DSN은 아래 pgvector 컨테이너 기준 `pinlog:pinlog@localhost:5433/pinlog`로 통일한다
(`.env.example` 기본값). back `compose.yaml`은 포트·계정이 다르므로(15432·pinlog-local) 혼용하지 않는다.

```bash
# 1. pgvector 기동
#    이미지는 back compose.yaml·Testcontainers와 동일하게 digest까지 고정한다.
docker run -d --name pinlog-pgv -e POSTGRES_USER=pinlog -e POSTGRES_PASSWORD=pinlog \
  -e POSTGRES_DB=pinlog -p 5433:5432 \
  pgvector/pgvector:0.8.5-pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb

# 2. ai 스키마 생성 — back Flyway 마이그레이션을 순차 적용 (ai 레포는 Migration을 실행하지 않는다)
#    파일 위치: back/src/main/resources/db/migration/
#    ai.* 테이블은 V1·V100·V101 소관. V102(feed_event)는 core 소관이므로 제외한다.
#    로컬에 psql 클라이언트가 없으면 컨테이너의 psql로 적용:
for f in V1__ V100__ V101__; do
  docker exec -i pinlog-pgv psql -U pinlog -d pinlog \
    < "$(ls ../back/src/main/resources/db/migration/${f}*.sql)"
done

# 3. 설정 — .env.example을 .env로 복사 (DSN 기본값이 1단계 컨테이너와 일치, GMS 키만 채운다)
cp .env.example .env   # GMS_API_KEY · INTERNAL_SHARED_SECRET 등 CHANGME 치환

# 4. Preset 부트스트랩 (임베딩 생성 → ai.keyword_preset)
python -m app.bootstrap.load_presets

# 5. 기동
uvicorn app.main:app --port 8000
```

DB 조회도 로컬 psql 없이 컨테이너로: `docker exec -it pinlog-pgv psql -U pinlog -d pinlog`.

## 프로브와 스모크

배포 계약(`docs/implements/2026-07-29-dev-deployment-gates.md`)이 정한 세 경로입니다.

| 경로 | 판정 | 용도 |
|---|---|---|
| `GET /health` | 정적 `{"status":"ok"}` | liveness · startup. DB·캐시 상태를 섞지 않는다 |
| `GET /ready` | DB `SELECT 1` + Preset 캐시 ≥ 1건 → `200 ready` / `503 not_ready` | readiness. GMS는 호출하지 않는다 |
| `python -m app.smoke.gms_roundtrip` | embedding 1회 + judge 1회, 한쪽이라도 실패 시 exit 1 | 배포 activation 게이트. DB 접근 없음 |

두 프로브 모두 무인증입니다(`/internal/` 밖). 응답·출력에 credential·endpoint·profile 값을 싣지 않습니다.

## 테스트

```bash
pytest                       # Docker 필요 — Testcontainers가 pgvector 0.8.5(digest 고정) 기동
```

계층·컨벤션(Fake·TRUNCATE·호출 횟수·on_call 훅)은 [`tests/README.md`](tests/README.md), 시나리오 정의는 [`docs/spec/integration-tests.md`](docs/spec/integration-tests.md).

## Docker

```bash
docker build -t pinlog-ai .  # python:3.12-slim, requirements.lock 설치

# 이미지는 .env를 제외(.dockerignore)하므로 실행 시 env를 주입한다.
# host의 pgvector 컨테이너(5433)에 접근하려면 host.docker.internal을 쓴다:
docker run -d --name pinlog-ai -p 8000:8000 --env-file .env \
  -e DATABASE_URL="postgresql://pinlog:pinlog@host.docker.internal:5433/pinlog" \
  --add-host=host.docker.internal:host-gateway pinlog-ai
```

## 공용 계약

파트 간 계약(원칙·상태 정의·테이블 역할·내부 API·검증 시나리오)의 단일 원본은
**`Team-PinLog/docs`의 `static/05_AI_설계.md`**입니다. `docs/` 아래 문서는 그 계약을 참조하며,
어긋나면 `static/05_AI_설계.md`가 우선합니다.

## 연동 대상

| 파트 | 스택 | 비고 |
|---|---|---|
| Spring Backend | Java 21 / Spring Boot 4.1.0 | Core 도메인·최종 응답, `ai` 스키마 포함 Migration 실행 |
| PostgreSQL + pgvector | `ai` 스키마 | FastAPI DB 권한은 `ai` 스키마로 한정 |
