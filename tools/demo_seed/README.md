# 데모 시딩

발표 시연용 데이터를 **back API 경로로** 만든다. 한 번 만들고 끝이 아니라
`--reset`으로 지우고 다시 만들 수 있어야 하는 것이 이 도구의 목적이다.

판단 근거와 실측 결과: [docs/implements/2026-07-29-demo-seeding.md](../../docs/implements/2026-07-29-demo-seeding.md)

## 무엇을 만드나

| | 수 | 비고 |
|---|---|---|
| member | 5 | 주인공 1 + 피드 후보 소유자 4 |
| Record·Context | 14 | 주인공 6 + 소유자별 2 |
| Collection | 9 | Record를 재사용하므로 GMS 호출이 늘지 않는다 |
| Follow | 2 | 주인공 → walker·dessert |

**GMS 실호출 29회** — 임베딩 14 + 판정 14 + 프리셋 1배치. 규모의 근거는
[`demo_data.yaml`](demo_data.yaml) 머리말에 있다.

시연 3종이 이 데이터로 성립한다.

```
자연어 검색   "비 오는 날 가려고 저장한 곳" → [데모] 골목 안 다방 1위
탐색 피드     소유자 4명의 Collection 8장이 섞여 나온다 (본인 것 제외)
Keyword       피드 카드와 Record 상세 양쪽에 code 가 붙는다
```

## 전제 — 스택 셋이 함께 떠 있어야 한다

`tools/e2e/`(FastAPI 단독)와 달리 **back까지 필요**하다. Context를 API로 만들어야
`-102`가 붙인 `PENDING` 생성과 FastAPI 호출이 실제로 타기 때문이다.

### 1. DB·Redis

back의 `compose.yaml`을 쓴다. Flyway가 `V1`~`V102`를 전부 적용하므로 `ai.*`
스키마를 손으로 만들 필요가 없다 — 레포 `README.md` 2단계의 psql 루프는 back을
띄우지 않을 때의 절차다.

```bash
cd ../back && docker compose -p pinlog-demo up -d
```

프로젝트 이름을 `pinlog-demo`로 두는 것은 다른 세션이 쓰는 `back` 프로젝트
컨테이너를 재생성하지 않기 위해서다. 포트는 기본값(15432·16379)을 쓴다.

### 2. 서명 키

back과 시딩 스크립트가 **같은 RSA 개인키**를 써야 한다. 키가 없으면 스크립트가
`.demo/demo-jwt-key.pem`에 만든다(gitignore 대상, 커밋하지 않는다).

```bash
python -c "import sys; sys.path.insert(0,'tools/demo_seed'); import _client; _client.ensure_key()"
```

### 3. back 기동

```bash
cd ../back && ./gradlew bootJar
JWT_PRIVATE_KEY="$(cat ../ai/.demo/demo-jwt-key.pem)" \
PINLOG_AI_INTERNAL_SECRET="<ai .env 의 INTERNAL_SHARED_SECRET 과 같은 값>" \
PINLOG_AI_BASE_URL=http://localhost:8000 \
SPRING_PROFILES_ACTIVE=local \
java -jar build/libs/pinlog-back-0.0.1-SNAPSHOT.jar
```

`PINLOG_AI_INTERNAL_SECRET`이 비면 FastAPI가 401을 주고 `AiProcessClient`가 그
실패를 삼킨다 — **임베딩이 하나도 안 생기는데 로그 말고는 신호가 없다.**
기동 로그에 `pinlog.ai.internal-secret이 비어 있다` 경고가 없는지 확인하라.

### 4. FastAPI

`.env`의 `DATABASE_URL`이 위 compose DB(포트 15432, user `pinlog`, password
`pinlog-local`)를 가리켜야 한다. 레포 `README.md`가 처방하는 5433 단독 컨테이너와
**같은 DB가 아니다** — back과 ai가 같은 DB를 봐야 시딩이 성립한다.

```bash
python -m app.bootstrap.load_presets
uvicorn app.main:app --port 8000
```

## 실행

```bash
python tools/demo_seed/seed.py --reset      # 지우고 다시 만든다
python tools/demo_seed/verify.py            # 시연 3종 확인
```

| 옵션 | 기본 | 뜻 |
|---|---|---|
| `--reset` | 없음 | 기존 데모 데이터를 hard delete한 뒤 만든다 |
| `--pace` | **1** | Record 생성 간격(초). 2026-07-30 실측에서 37건이 42초에 완주했다. 429가 실제로 날 때만 올린다 |
| `--back` | `http://localhost:8080/api/core` | back base URL |
| `--ai` | `http://localhost:8000` | FastAPI base URL |

## 얼마나 걸리나 — 그날 GMS 상태에 달렸다

**쿼터가 상수가 아니다.** GMS는 SSAFY 공용 게이트웨이라 우리 전용 할당이 아니고,
시점과 프로바이더 경로에 따라 달라진다([T27](../../docs/troubleshooting/2026-07-30-seeding-quota-and-encoding.md)).

```
2026-07-29   판정 분당 약 2건 · 15초 간격에도 429
2026-07-30   분당 30건 이상 · 동시 10건도 통과
```

같은 데이터 37건을 두 번 시딩한 실측이다.

| | 시간 | 회수 | 토큰 |
|---|---|---|---|
| `--pace 25` | 15분 8초 | 0회 | 32,912 |
| `--pace 1` | **42초** | 0회 | 32,967 |

**결과와 토큰은 같다.** 차이는 우리가 쉰 시간뿐이었다. 그래서 기본값이 1이다.

429가 나면 `retry.py`의 지수 백오프가 호출 단위로 재시도하고, 그래도 미완료면
아래 회수 루프가 한 건씩 되살린다. **429를 실제로 본 뒤에 `--pace`를 올려라.**

## 데이터를 바꾸려면

[`demo_data.yaml`](demo_data.yaml)만 고치고 `seed.py --reset`을 다시 돌린다.
스크립트를 고칠 일이 없어야 한다 — 그렇지 않다면 그건 결함이다.

## 주의

- **로컬 전용이다.** dev·운영 DB에 돌리지 마라. 어디에 시딩할지는 배포 담당과
  따로 정한다.
- `tools/e2e/`의 검증 데이터(user 9001·context 1xxx)와 **섞이지 않는다.** 그쪽은
  `core`에 대응 행이 없는 `ai` 단독 데이터라 Feed 조인에 걸리지 않고, `/search`는
  `userId`로 갈린다. 같은 DB에 공존해도 서로 보이지 않는다.
- 테스트 픽스처와도 섞이지 않는다. `tests/`는 Testcontainers로 자체 DB를 쓴다.
- 데모 member는 `core.social_account`에 provider `demo-seed`로 표시된다. 이
  표식이 `--reset`의 삭제 범위이자 "이건 시딩 데이터"의 판별 근거다.
