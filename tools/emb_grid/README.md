# 임베딩 4조건 실경로 측정

`S15P11A705-174` 의 검색 정확도 10/12 에서 실패 2건이 나왔고, 그중 8번
(「밥 먹고 산책하면서 쉬어가는 공원」)의 원인이 **질의의 「공원」이 본문에 없고 장소명에만
있다**는 것이었다. 임베딩 입력이 Context 본문 하나이므로 장소명은 벡터에 들어갈 경로가 없다.

개선 축이 둘인데 어느 쪽이 듣는지 모른다. 그래서 교차시켜 넷을 잰다.

| 조건 | 모델 | dim | 장소명 결합 | profile |
|---|---|---|---|---|
| **A** | `text-embedding-3-small` | 1536 | 끔 | `openai-text-embedding-3-small-1536-cosine-grid-a` |
| **B** | `text-embedding-3-small` | 1536 | 켬 | `openai-text-embedding-3-small-1536-cosine-grid-b` |
| **C** | `text-embedding-3-large` | 3072 | 끔 | `openai-text-embedding-3-large-3072-cosine-grid-c` |
| **D** | `text-embedding-3-large` | 3072 | 켬 | `openai-text-embedding-3-large-3072-cosine-grid-d` |

정본은 `conditions.py` 하나다. 위 표는 그 파일을 읽고 적은 것이며, 셸에는
`eval "$(python tools/emb_grid/conditions.py A)"` 로 넘긴다 — 값을 셸 스크립트에 다시 적으면
둘이 갈라지고, 갈라진 채 재면 어느 조건의 숫자인지 알 수 없게 된다.

**A 가 기준선이다.** `-174` 의 10/12 를 재현해야 나머지 셋의 수치가 비교 대상을 얻는다.
재현하지 못하면 그 사실 자체를 먼저 기록하고, 원인을 밝히기 전에는 B·C·D 를 채택 근거로
쓰지 않는다.

## 무엇을 재지 않는가

- **탐색 피드·Keyword 표시** — `verify.py` 의 B·C 절이 보는 것이며 임베딩 입력과 무관하다
- **질의 쪽 장소명** — 사용자는 어느 장소를 찾는지 모르므로 질의에 장소명을 붙일 수 없다.
  조건 B·D 는 **저장과 질의의 입력 형식이 비대칭**인 상태를 재는 것이고, 그 비대칭의 영향까지
  포함한 것이 여기 숫자다
- **반복 실행의 분산** — 임베딩은 결정적이라 검색 순위가 재현되지만 판정(Keyword)은 비결정적이다.
  토큰 수치는 실행마다 조금씩 흔들린다

## 실행

### 0. 준비 — 한 번만

```bash
cd back && docker compose -p pinlog-demo up -d
cd ../back/.claude/worktrees/emb-grid && ./gradlew bootJar -x test
```

`back` 은 **worktree 에서 빌드해야 한다.** 장소명 결합 스위치(`EmbeddingInputComposer`)가
그 브랜치에만 있고, `dev` 의 jar 로 재면 조건 B·D 가 조건 A 와 같은 숫자를 낸다.

### 1. 조건마다 — env 를 셋 모두에 같게 준다

```bash
cd ai
eval "$(python tools/emb_grid/conditions.py A)"
export DATABASE_URL="postgresql://pinlog:pinlog-local@localhost:15432/pinlog"
```

`DATABASE_URL` 을 반드시 덮어쓴다. `.env` 는 `5433`(`pinlog-pgv-e2e`)을 가리키는데 측정은
`15432`(`pinlog-demo`)에서 돈다 — back 의 `local` 프로필이 그쪽을 보기 때문이다.

FastAPI · `seed.py` · `run_condition.py` 가 **모두 같은 profile** 을 봐야 한다. `verify` 계열은
자기 프로세스 설정으로 profile 을 읽어 검색 요청에 싣고 FastAPI 는 자기 설정과 대조해 다르면
422 로 거절한다. 한쪽에만 주면 측정이 시작되지 않는다.

### 2. 차원 — C·D 앞에서만

```bash
python tools/emb_grid/alter_dim.py --to 3072
```

기존 벡터를 전부 버린다. 자세한 것은 그 파일 첫머리에 있다.

### 3. 두 서버를 띄운다

```bash
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

```bash
cd back/.claude/worktrees/emb-grid
JWT_PRIVATE_KEY="$(cat ../../../../ai/.demo/demo-jwt-key.pem)" \
  PINLOG_AI_BASE_URL=http://localhost:8000 \
  SPRING_PROFILES_ACTIVE=local \
  java -jar build/libs/pinlog-back-0.0.1-SNAPSHOT.jar
```

`PINLOG_AI_INTERNAL_SECRET` 은 `ai/.env` 의 `INTERNAL_SHARED_SECRET` 과 같아야 한다. 다르면
모든 내부 호출이 401 이고 **back 이 그 실패를 삼키므로** 임베딩이 하나도 생기지 않는다.
`PINLOG_AI_EMBEDDING_PROFILE` · `PINLOG_AI_EMBEDDING_INCLUDE_PLACE_NAME` 은 1단계 `eval` 이
셸에 넣어 두었으므로 그 셸에서 띄우면 그대로 상속된다.

### 4. 잰다

```bash
python tools/emb_grid/run_condition.py A
```

프리셋 적재 → 시딩(`--reset`) → 검색 12건 → 토큰 → 저장 비용 순으로 돌고
`.grid/condition-A.json` 을 남긴다. 조건이 환경과 어긋나면 **재지 않고 멈춘다.**

조건마다 `--reset` 으로 완전히 초기화한다. profile 로 걸러지므로 벡터는 섞이지 않지만
`context_ai_state` 는 Context 당 1행이라 재시딩이 앞 조건의 상태를 덮어써 **시간·토큰이
오염된다** — 이미 COMPLETED 인 Context 는 임베딩을 다시 만들지 않기 때문이다.

## 측정이 끝나면 — 로컬 DB 복구

```bash
cd ai
export DATABASE_URL="postgresql://pinlog:pinlog-local@localhost:15432/pinlog"
python tools/emb_grid/alter_dim.py --to 1536
eval "$(python tools/emb_grid/conditions.py A)"   # 아무 1536 조건이면 된다
python -m app.bootstrap.load_presets
```

차원을 되돌리고 프리셋을 실배포 profile 로 다시 적재한다. **마지막 줄을 빼먹으면 `-174` 의
데모 데이터가 다시 시딩될 때까지 `keyword_preset` 이 `grid-*` profile 인 채로 남아** `/ready` 가
503 이거나 판정이 후보를 못 찾는다.

실배포 profile 로 되돌리려면 `PINLOG_EMBEDDING_*` 를 셸에서 지우고(`.env` 기본값으로) 적재한다.

```bash
unset PINLOG_EMBEDDING_MODEL PINLOG_EMBEDDING_DIMENSION \
      PINLOG_EMBEDDING_DISTANCE PINLOG_EMBEDDING_PROFILE
python -m app.bootstrap.load_presets
```

**Flyway 마이그레이션은 만들지 않았다.** 이 측정은 차원 채택을 결정하지 않는다.
`vector(3072)` 을 실제로 쓰기로 하면 그때 별건으로 마이그레이션과 전량 재임베딩 계획이 필요하다.
