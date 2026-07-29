# 데모 시딩 — 시연 데이터를 back API 경로로 재현 가능하게 만든다

- **상태**: 완료
- **날짜**: 2026-07-29
- **Jira**: S15P11A705-58
- **관련 PR**: (이 PR)
- **근거 계약**: [spec/context-processing.md](../spec/context-processing.md), [spec/personal-search.md](../spec/personal-search.md), [spec/state-machine.md](../spec/state-machine.md)
- **선행 기록**: [2026-07-27-e2e-verification.md](2026-07-27-e2e-verification.md) (I21)
- **도구**: [tools/demo_seed/](../../tools/demo_seed/), [tools/e2e/](../../tools/e2e/)

## 무엇을 했나

① `tools/e2e/`가 현행 코드에서 도는지 재확인하고 ② 발표 시연용 데이터를
**다시 만들 수 있는 형태로** 만들었다. 프로덕션 코드 변경은 없다.

I21(2026-07-27) 이후 back에 `-102`(Context 생성 시 AI 처리 접수)·`-120`(Feed)·
`-124`(삭제 정합)가, ai에 `-96`(`/ready`·smoke)이 들어왔다. **I21이 "불가"로
판정했던 피드 시연이 가능해진 것**이 이 작업의 전제 변화다.

> I21 §시딩 가능 범위: *"불가 — 피드 시연. core 도메인 미착수(백엔드가 member
> 엔티티 스캐폴딩 단계, core 마이그레이션 V2~ 미착수)."*

지금은 `V2__member`·`V3__core_domain`·`V4__social_account`·`V5`가 모두 있고
Feed 런타임도 있다. 그래서 I21이 권했던 **매핑 파일 방식(`e2e_contexts.yaml`)을
데모로 확장하지 않았다.** 그 방식은 core가 없다는 제약의 산물이었고, 제약이
사라졌으므로 판단을 다시 했다.

---

## 판단 1 — 시딩을 어디에 두고 어떻게 만드는가

### 결론: `tools/demo_seed/`, back API 호출

두 축의 결정이다.

**위치는 `tools/`다.** `app/bootstrap/load_presets.py`는 제품이 항상 필요로 하는
데이터를 적재하는 **운영 부트스트랩**이다. 데모 시딩은 운영에서 절대 돌면 안
되는 코드이므로 같은 자리에 두면 안 된다. `tools/`에는 이미 `e2e/`(검증 드라이버)와
`keyword_eval/`(평가 하네스)이 있고, "실행해서 무언가를 확인하는 로컬 도구"라는
성격이 같다.

**만드는 방법은 back API 호출이다.** 갈림길은 이랬다.

| | SQL 직접 INSERT | back API 호출 |
|---|---|---|
| 필요 스택 | DB만 | DB + back + FastAPI |
| `-102` PENDING 생성 | 안 탄다 (직접 써야 함) | 탄다 |
| FastAPI `/context/process` 호출 | 안 탄다 | 탄다 |
| 실패 모드 | **데이터는 있는데 파이프라인은 안 돈 상태** | 파이프라인이 실패하면 드러난다 |
| 시딩의 부가 가치 | 없음 | **통합 검증을 겸한다** |

SQL 직접 INSERT의 위험은 비용이 아니라 **거짓 성공**이다. 화면에 데이터가 보이면
잘 된 것처럼 보이는데, 정작 시연에서 "새 Context를 지금 추가하면 Keyword가
붙는다"를 보여주는 순간 그 경로가 처음 실행된다. 발표 당일에 처음 실행되는
경로를 남기지 않는 것이 이 작업의 목적에 부합한다.

실제로 이번 시딩이 그 값을 했다 — Record 14건을 만드는 동안
`core.record`·`core.context` INSERT → `ai.context_ai_state` PENDING INSERT →
`POST /internal/v1/context/process` → 임베딩·판정 저장까지가 매 건 실행됐다.

### SQL을 쓰는 두 지점과 그 근거

"전부 API"는 성립하지 않는다. 두 곳에서 SQL이 불가피하다.

**① member 생성.** back의 유일한 회원 생성 경로가 소셜 OAuth 콜백이다
(`SocialLoginService`). 스크립트가 부를 API가 없고, 실제 Google 계정 5개로
브라우저 로그인을 하는 것은 재현 가능한 절차가 아니다. 다행히 `core.member`는
`id`·`created_at`·`deleted_at` 셋뿐인 익명 테이블이라(익명 서비스 설계) 이 INSERT가
우회하는 도메인 규칙이 없다. 추적을 위해 `core.social_account`에 provider
`demo-seed`를 함께 남겼다 — **이 표식이 `--reset`의 삭제 범위이자 "이건 시딩
데이터"의 판별 근거**다.

**② `--reset` 삭제.** API의 삭제는 전부 soft delete다. "시연 직전에 DB를 비우고
다시"를 재현하려면 hard delete가 필요한데 그에 해당하는 API가 없다. 삭제 범위는
provider `demo-seed`로 식별된 member의 데이터로 한정했다.

그 외 Record·Context·Collection·Follow는 전부 API로 만든다.

> **`core`에 쓰는 것이 계약 위반 아닌가.** 계약이 금지하는 것은 *FastAPI 런타임이*
> `core`를 읽고 쓰는 것이다(README:10, [architecture.md](../spec/architecture.md) §7).
> `tools/`의 로컬 스크립트는 런타임이 아니고, 앱 코드에는 `core` 참조가 한 줄도
> 늘지 않았다. I21이 실증한 대로 로컬 접속 롤은 슈퍼유저라 DB가 이 경계를 강제하지
> 않는다(`S15P11A705-61` 미해소) — 그래서 **범위를 표식으로 좁히고 문서에 남기는
> 것이 현재 쓸 수 있는 유일한 방어선**이다.

### back 변경은 필요하지 않았다

`Forbidden`에 걸린 항목이다. 결과적으로 back 코드는 한 줄도 건드리지 않았고
`Need Decision`으로 올릴 사유도 생기지 않았다. 두 가지가 그것을 가능하게 했다.

- **JWT 서명 키 주입 경로가 이미 있다.** `JwtKeyProvider`가 `JWT_PRIVATE_KEY`를
  받고, 없으면 임시 키쌍을 만든다. 시딩은 back에 준 것과 **같은 개인키**로 Access
  토큰을 서명한다. 인증 *우회*가 아니라 키 *공급*이며, 서명·발급자·만료·용도
  클레임을 back의 `JwtTokenProvider`가 그대로 검증한다.
- **CSRF도 통과시킨다.** 우회 수단이 없기도 하고, 프론트가 밟을 경로를 그대로
  밟는 편이 낫다.

---

## 판단 2 — GMS 실호출 몇 건이 적정인가

### 결론: Context 14건 = 실호출 29회 (임베딩 14 + 판정 14 + 프리셋 1배치)

시연 3종이 각각 성립하는 최소로 잡았다.

| 시연 | 필요한 것 | 수 | 근거 |
|---|---|---|---|
| 자연어 검색 | 주인공 1명의 Context | 6 | 정답 1 + 경쟁 5. I21이 분리도 +0.2120을 실측한 규모 |
| 탐색 피드 | 타 소유자와 그 Context | 4명 × 2 = 8 | `max-per-owner=2`라 소유자 4명이 카드 8장의 상한 |
| Keyword | (위에 자연히 붙는다) | 0 | 별도 Context를 만들지 않는다 |

Collection 9개와 Follow 2건은 **Record를 재사용**하므로 GMS 호출이 늘지 않는다.
소유자당 Collection을 2개로 둔 것도 그래서다 — 화면은 채워지고 비용은 그대로다.

### 진짜 제약은 돈이 아니라 429였다

비용 산정보다 먼저 부딪힌 것은 **GMS 게이트웨이의 Gemini 429**다. 판정 호출을
15초 간격으로 12회 던져 측정했다.

```
   1.8s  OK      81.4s  OK     146.4s  OK
  17.7s  429     97.2s  429    162.5s  429
  33.2s  429    113.9s  OK     179.3s  OK
  49.1s  429    130.4s  OK
  64.8s  429
                          → 3분에 6건 통과 (분당 약 2건)
```

`RESOURCE_EXHAUSTED`. 임베딩(OpenAI 경로)에서는 관측되지 않았고 판정(Gemini
경로)에서만 났다. 공용 게이트웨이라 다른 사용자와 쿼터를 나눠 쓰는 것으로 보인다.

이것이 건수 판단을 바꿨다. **"많을수록 좋은 것이 아니다"의 이유가 비용이 아니라
시간**이다. Context 1건이 판정 1회를 쓰고 판정이 분당 2건이면, 시딩 시간은 건수에
선형으로 늘고 재현(다시 돌리기)마다 그만큼 든다. 시연에 보탬이 되지 않는 Context는
그 시간을 값 없이 쓴다.

### 429가 10분을 태우는 경로 — 이것이 설계를 바꿨다

429가 나면 `keyword_service`는 상태를 `PROCESSING`으로 둔 채 조용히 돌아온다
(재스캔 회수 전제). 그런데 `ai_state_repo.try_start`는 stale `PROCESSING`을
**`PROCESSING_EXPIRY_SEC`(기본 600초) 뒤에만** 재선점한다.

```sql
WHERE {col} IN ('PENDING','PROCESSING')
  AND ({col} = 'PENDING' OR updated_at < now() - $2::interval)
```

즉 **429 한 번이 그 Context를 10분간 얼린다.** 로컬에는 Spring 재스캔이 없으므로
그냥 두면 영영 미완료다. 세 가지 선택지가 있었다.

| 안 | 내용 | 채택 |
|---|---|---|
| A | `.env`의 `PROCESSING_EXPIRY_SEC`를 낮춘다 | ✗ FastAPI 재기동이 필요하고, 운영과 다른 값을 로컬에 남긴다 |
| B | 429가 안 날 만큼 느리게 던진다 | ✗ 15초 간격에도 429가 났다. 회피가 불가능 |
| C | 시딩이 `PROCESSING` → `PENDING`으로 되돌리고 재호출 | **✓** |

C를 골랐다. 쓰기가 `ai` 스키마 안에서 끝나고, 운영의 M3 재처리
(`COMPLETED → PENDING`, [state-machine.md](../spec/state-machine.md))와 같은 성격의
상태 되돌림이며, 설정 변경도 재기동도 필요 없다. `tools/e2e/run_pipeline.py`가
이미 `PENDING`을 직접 넣어 Spring을 대행하는 것과 같은 자세다.

결과적으로 `seed.py`는 **로컬에 없는 재스캔 워커를 대신한다** — 미완료 건을
한 건씩(`RECOVER_INTERVAL_SEC=20`) 되살린다. 이 구조 덕에 429가 나도 시딩이
실패하지 않고 느려질 뿐이다.

---

## E2E 재확인 — `tools/e2e/`는 현행 코드에서 그대로 돈다

I21 이후 ai `app/`에 들어온 변경은 `-96` 둘(`/ready`·`GMS_BASE_URL` fail-fast
추가, 공개 설정 기본값을 코드로 이동)이다. 드라이버가 쓰는 `get_settings()`·
`Database`·API 계약은 그대로였고, **깨진 곳이 없었다.**

| 드라이버 | 결과 |
|---|---|
| `run_pipeline.py` | Context 8건 → **6.0초에 두 status 전부 COMPLETED** (I21과 동일) |
| `run_search.py` | 계약 3종(200·422·401) 통과, 관련 질의 6건 전부 1위, 분리도 **+0.2120** |
| `run_equivalence.py` | **GMS 429로 중단** (아래) |
| `run_attribution.py` | 미실행 — 위와 같은 사유 |

### 검색 수치가 소수점 넷째 자리까지 같다

I21과 이번 실행이 완전히 같은 값을 냈다.

```
관련 top1 : min 0.5263 · max 0.6740 · avg 0.5989  (I21)
관련 top1 : min 0.5263 · max 0.6740 · avg 0.5988  (2026-07-29, avg는 표시 반올림 차)
무관 top1 : min 0.2362 · max 0.3143               (양쪽 동일)
간격      : +0.2120                                (양쪽 동일)
```

**임베딩은 결정적이다.** I21이 "판정은 결정적이지 않다"를 실측한 것과 대비되는
사실이며, 검색 시연이 매번 같은 순위를 낸다는 뜻이라 시연 안정성에도 의미가 있다.

### `run_equivalence.py`의 중단은 회귀가 아니다

두 가지가 겹쳤다.

1. **키 설정이 별도다.** 이 드라이버는 `tools/keyword_eval/` 하네스를 import하고,
   그쪽은 `GMS_API_KEY`를 환경변수나 `tools/keyword_eval/.env`에서 읽는다. 레포
   `.env`를 보지 않는다. `tools/e2e/README.md`에 이미 적혀 있는 전제다.
2. **키를 준 뒤에는 429로 죽었다.** 샘플 10건 × (운영 2회 + 하네스 1회) 판정을
   간격 없이 던지므로 분당 2건 쿼터에서 완주가 불가능하다. 샘플 06까지 진행한 뒤
   `TransientError: llm error: 429`로 중단됐다.

여기까지의 결과 자체는 **I21과 일치**한다 — 후보 집합·순서가 샘플 00~06 전부
Jaccard 1.00으로 일치했고, 운영 판정 2회 반복도 전부 동일했다. 코드가 바뀌어 깨진
것이 아니라 **외부 쿼터가 완주를 막은 것**이므로 수정 대상은 드라이버의 재시도·
간격 설계이고, 이번 티켓 범위(-58)가 아니다. → [남은 것](#남은-것)

---

## 시딩 결과

### 만들어진 것

아래 수치는 1회차다(2회차는 [재현성](#재현성--처음부터-다시-돌려-확인했다) 절).

```
member 5 · Record·Context 14 · Collection 9 · Follow 2
Context 14건 전부 COMPLETED · ai.context_keyword 32행 · 총 소요 약 6분
```

`--pace 25`의 1회차에서는 **회수가 한 번도 필요하지 않았다** — 25초 간격이 판정
쿼터(분당 약 2건) 안에 들어와 14건이 모두 첫 시도에 통과했다. **그럼에도 회수
루프는 필요하다** — 쿼터가 공용이라 다른 사용자 부하에 따라 달라지고, 실제로
2회차에서는 1건이 429로 막혀 회수가 발동했다.

### 시연 3종 — 전부 통과

**A. 자연어 검색** — 시연 질의 4건 전부 의도한 Record가 1위.

| 질의 | 1위 | similarity |
|---|---|---|
| **비 오는 날 가려고 저장한 곳** | [데모] 골목 안 다방 | 0.5021 |
| 혼자 조용히 작업하기 좋은 카페 | [데모] 창가 작업실 카페 | 0.4969 |
| 친구들이랑 시끌벅적하게 놀 만한 곳 | [데모] 연남 골목 선술집 | 0.6414 |
| 기념일에 야경 보면서 식사할 곳 | [데모] 언덕 위 야경 식당 | 0.5267 |

2위와의 간격이 가장 좁은 것이 "비 오는 날"(0.5021 → 0.3490, 간격 0.153)이고
가장 넓은 것이 "친구들이랑"(0.6414 → 0.4069, 간격 0.235)이다. 넷 다 시연에서
1위가 뒤집힐 여지가 없다.

**B. 탐색 피드** — 카드 8장, 소유자 4명, 본인 것 0건.

```
[0] 데모 혼자 있고 싶을 때  owner=6  [ALONE, COZY, QUIET, RAINY_DAY]
[1] 데모 비 오는 날        owner=6  [ALONE, COZY, QUIET, RAINY_DAY]
[2] 데모 모임 하기 좋은     owner=5  [DRINK, GATHERING, LIVELY, MEAL, WITH_FRIENDS]
[3] 데모 늦게까지          owner=5  [DRINK, LIVELY]
[4] 데모 동네 산책 코스     owner=3  [ALONE, QUIET, WALK]
[5] 데모 조용한 아침       owner=3  [ALONE, QUIET]
[6] 데모 요즘 뜨는 곳      owner=4  [DESSERT, TRENDY]
[7] 데모 디저트 지도       owner=4  [DESSERT, TRENDY]
```

**C. Keyword** — Context 6건(주인공)에 17행, 피드 카드 8/8장에 표시.

```
[데모] 골목 안 다방        COZY, RAINY_DAY, RETRO
[데모] 창가 작업실 카페     ALONE, QUIET, STUDY_WORK
[데모] 연남 골목 선술집     LIVELY, MEAL, WITH_FRIENDS
[데모] 언덕 위 야경 식당    ANNIVERSARY(PRIVATE_ONLY), VIEW_GOOD, WITH_PARTNER
[데모] 넓은 한상 식당      MEAL, SPACIOUS, WITH_FAMILY
[데모] 강변 산책로 초입     EXHIBITION, WALK
```

의도한 대로 붙었다. `ANNIVERSARY`는 `PRIVATE_ONLY`라 본인 Context에는 있고
타인이 보는 피드 카드 8장 어디에도 나오지 않는다 — `FeedKeywordRepository`의
비대칭이 실데이터에서 작동한다.

### 재현성 — 처음부터 다시 돌려 확인했다

`seed.py --reset`을 **두 번 완주**하고 두 번 다 `verify.py`를 통과시켰다.
한 번 성공은 재현이 아니므로, 2회차는 1회차 데이터를 hard delete한 **빈 상태에서
다시** 만들었다(member id가 2~6에서 7~11로 옮겨간 것이 그 증거다).

**같은 것 — 시연이 성립하는 조건 전부**

| | 1회차 | 2회차 |
|---|---|---|
| Context COMPLETED | 14/14 | 14/14 |
| 검색 4질의 1위 | 전부 의도대로 | 전부 의도대로 |
| 피드 카드 · 소유자 | 8장 · 4명 | 8장 · 4명 |
| 피드 배치 순서 | rainy→party→walker→dessert | 동일 |
| 본인 Collection 노출 | 0 | 0 |
| `PRIVATE_ONLY` 누출 | 0 | 0 |

similarity도 사실상 같다.

```
비 오는 날 …    0.5021 / 0.5021
혼자 조용히 …   0.4969 / 0.4969
친구들이랑 …    0.6414 / 0.6414
기념일에 …      0.5267 / 0.5266   ← 넷째 자리 1
```

**다른 것 — 그리고 달라도 되는 것**

| | 1회차 | 2회차 |
|---|---|---|
| member·record·context id | 2~6 / 2~15 | 7~11 / 16~29 |
| `ai.context_keyword` 행 수 | 32 | 34 |
| 회수 루프 발동 | 0회 | 1회 (ctx=29, 21초에 회수) |

- **id는 재현의 조건이 아니다.** `GENERATED ALWAYS AS IDENTITY`라 삭제해도
  시퀀스가 되감기지 않는다. 그래서 `verify.py`는 id를 하드코딩하지 않고 place
  이름과 `demo-seed` 표식으로 대상을 찾는다.
- **Keyword 행 수 차이는 알려진 비결정성이다.** I21이 실측한 그대로다
  ([keyword-preset.md](../spec/keyword-preset.md) §4.4). 2회차에서 "넓은 한상
  식당"에 `GATHERING`이 하나 더 붙었다. 시연 3종의 판정에는 영향이 없다 —
  판정 기준이 "특정 code가 정확히 몇 개"가 아니라 "Keyword가 붙어 화면에
  나오는가"이기 때문이고, 그 기준이 옳은 이유가 바로 이 비결정성이다.
- **2회차의 회수 1회는 설계가 작동한 증거다.** ctx=29가 `kw=PROCESSING`으로
  남았고(429), 시딩이 `PENDING`으로 되돌려 21초 만에 회수했다. 회수 루프가
  없었다면 `PROCESSING_EXPIRY_SEC` 600초를 기다려야 했다.

> **재현의 정의를 여기서 못박는다** — 같은 행이 나오는 것이 아니라 **같은 시연이
> 성립하는 것**이다. 임베딩은 결정적이라 검색 순위가 고정되고, 판정은 비결정적이라
> Keyword 조합이 흔들린다. 후자를 고정하려 들면 시딩이 판정 결과를 박아 넣어야
> 하고, 그건 파이프라인을 우회하는 것이라 이 도구가 피하려던 바로 그 상태다.

---

## 발견 — 시연 화면에 드러나는 Feed 배치 역전 (back 소관, `Need Decision`)

피드 결과에서 **팔로우한 소유자(walker·dessert)가 아래쪽(4~7)에, 팔로우하지 않은
소유자(rainy·host_party)가 위쪽(0~3)에** 배치된다. 점수 공식만 보면 나올 수 없는
순서다 — `w_follow=0.5`이고 `followSignal`은 팔로우 채널 출처면 1.0이므로,
`keywordAffinity`(가중치 0.375)가 아무리 높아도 뒤집을 수 없다.

원인은 점수가 아니라 **배치**다.

```java
// ScoredCandidate
public boolean isExploration() { return candidate.fromRandom(); }

// FeedRanker.nextBlock — 탐색 몫을 먼저 떼고, 그 몫을 블록 하위 절반에 둔다
List<ScoredCandidate> exploration = take(ranked, used, ownerCounts,
    Math.min(slots, size), ScoredCandidate::isExploration);
```

`take`는 **점수 순으로** 집는다. 그런데 데모처럼 Collection 총수가 적으면
(`random-limit=20` ≥ 후보 8) 무작위 채널이 후보 전체를 훑어 **모든 후보가
`fromRandom=true`**가 된다. 그러면 탐색 슬롯 4개가 *최고점 후보 4개*를 먼저
가져가고, 그것들이 하위 절반에 놓인다.

의도와 반대다 — `feed-scoring` 4.2(AI 소유 명세, 실물은 back 레포
`docs/ai/spec/feed-scoring.md`)가 탐색을 하위 절반에
두는 이유는 *"상단을 무작위로 채우면 첫인상이 나빠지므로"*인데, 여기서는 상단이
차점자로 채워지고 최고점이 내려간다.

- **운영 규모에서는 잘 드러나지 않는다.** Collection이 수백 개면 `random-limit=20`이
  전체의 일부만 훑으므로 대부분의 후보는 `fromRandom=false`다.
- **시연 화면에는 그대로 나온다.** 발표에서 "팔로우한 사람의 컬렉션"을 짚으면
  아래쪽을 가리켜야 한다.

**AI 파트가 판단할 계약 질문이다** — 후보·필터·탐색의 *의미*는 AI 파트 소유이고
(CONTRIBUTING «Feed 협업 경계»), 구현은 백엔드 소유다. 물어야 할 것은 하나다.

> 탐색 슬롯이 **최고점 후보를 흡수해도 되는가**, 아니면 탐색은 "점수로는 뽑히지
> 않았을 후보"에서만 채워야 하는가.

후자라면 `take(exploration)`이 점수 하위부터 집거나 exploit 선발 후 남은 것에서
집어야 한다. 이 티켓(-58)에서 임의로 정하지 않는다. `Forbidden`이 back 코드 변경을
막고 있기도 하지만, 그보다 **계약을 정하지 않고 구현만 바꾸면 같은 문제가 다시
난다.**

시딩 데이터로 이 배치를 감추지 않았다. Collection을 21개 이상으로 늘리면 무작위
채널이 전체를 덮지 못해 증상이 사라지지만, 그건 결함을 데이터로 가리는 것이고
시연 결과를 예측 불가능하게 만든다.

---

## 발견 — Record 상세 API는 Keyword를 돌려주지 않는다 (back 미구현)

```java
// RecordDetailResponse.of
return new RecordDetailResponse(
    record.getId(), PlaceSummaryResponse.from(place), contexts,
    List.of(),          // ← keywords 고정 빈 배열
    record.getCreatedAt(), addedToCollectionAt);
```

`GET /v1/records/{id}`와 `POST /v1/records`의 `keywords`는 **항상 빈 배열**이다.
`ai.context_keyword`에 행이 있어도 그렇다(위 C절에서 17행을 확인했다).

시연 영향: **Record 상세 화면에는 Keyword가 나오지 않는다.** Keyword를 보여줄 수
있는 화면은 현재 피드 카드뿐이다. `verify.py`는 이것을 통과 조건이 아니라
**관측 항목**으로 출력한다 — back의 미구현 때문에 시딩 검증이 붉게 뜨면 정작
시딩의 문제를 못 보게 된다.

발표 시연 구성에 영향이 있으므로 중앙 조정 세션에 함께 올린다.

---

## 데이터 설계

`demo_data.yaml` 하나가 시딩의 유일한 입력이다. 내용을 바꾸고 `--reset`으로 다시
돌리면 그대로 재구성된다 — **스크립트를 고쳐야 한다면 그건 결함**이다.

### 시연용임이 드러나게 만들었다

`Forbidden`의 *"실제 인물·상호를 오해하게 만드는 데이터"* 항목에 대한 처리다.

- 장소명에 `[데모]` 접두사 — 화면에 그대로 보인다
- `kakao_place_id`는 `demo-seed-*` — DB에서 판별된다
- 주소는 `서울특별시 OO구 (데모 시딩 데이터)`
- 실재 상호를 쓰지 않았다. 시연 화면이 특정 가게에 대한 실제 후기처럼 보이면 안 된다

사람 이름은 애초에 저장되지 않는다 — `core.member`에 이름 컬럼이 없다(익명 서비스).
`demo_data.yaml`의 `key`는 파일과 로그에서만 쓰는 식별자다.

### 시연 서사를 데이터에 넣었다

기능이 도는 것과 시연이 설득력 있는 것은 다르다. 두 가지를 의도적으로 배치했다.

- **`rainy` 소유자의 취향을 주인공과 겹쳐 놓았다.** 주인공이 비 오는 날·아늑한
  곳을 저장해 두었고, `rainy`의 Collection도 그렇다. Feed 점수의
  `keywordAffinity`(가중치 0.375)가 실제로 순위에 반영되는 것이 화면에서 보인다.
- **`WITH_PARTNER`·`ANNIVERSARY`가 붙을 Context를 주인공에게 넣었다.**
  `ANNIVERSARY`는 `PRIVATE_ONLY`라 본인 프로파일에는 반영되고 타인 Collection
  카드에는 나오지 않는다(`FeedKeywordRepository`의 비대칭). `verify.py`가
  이 미노출을 판정 항목으로 확인한다.

---

## 검증 방식 — "만들었다"와 "보인다"를 분리했다

`verify.py`는 DB를 세어 통과시키지 않는다. **시연에서 실제로 호출될 두 API를
그대로 호출하고 그 응답만으로 판정**한다.

```
A. 자연어 검색  POST /internal/v1/search    (FastAPI, 주인공 userId, 실 GMS 임베딩)
B. 탐색 피드    GET  /v1/feed/collections   (back, 주인공 인증)
C. Keyword      B의 응답 keywords + GET /v1/records/{id}
```

DB 수치로 통과시키면 "행은 있는데 API가 안 준다"를 놓친다. Feed는 특히 그렇다 —
`record_count > 0`·`is_published`·소유자 미탈퇴·본인 제외를 전부 WHERE 절에 걸고
있어서, 행이 있어도 응답이 빌 수 있다.

`load_ids()`가 매핑을 `seed.py`의 출력 파일이 아니라 **DB에서 복원**하는 것도
같은 이유다. 파일로 넘기면 파일과 DB가 어긋날 수 있고, 그러면 검증이 거짓
통과한다.

---

## 재현 절차

전제(스택 셋)와 실행은 [`tools/demo_seed/README.md`](../../tools/demo_seed/README.md)에
있다. 요지만 옮긴다.

```bash
cd ../back && docker compose -p pinlog-demo up -d   # Flyway가 V1~V102 전부 적용
# back 기동 (JWT_PRIVATE_KEY = ai/.demo/demo-jwt-key.pem)
python -m app.bootstrap.load_presets
uvicorn app.main:app --port 8000
python tools/demo_seed/seed.py --reset
python tools/demo_seed/verify.py
```

### I21의 마찰 F1이 이 경로에서는 사라진다

I21이 최대 마찰로 꼽은 것은 *"README 2단계 `ai` 스키마 생성 — '적용해'의 방법이
없다"*였다. ai#22가 psql 루프를 문서에 넣어 해소했지만, **데모 경로에서는 그
루프 자체가 필요 없다** — back을 띄우면 Flyway가 `V1`~`V102`를 전부 적용한다.

다만 그 대가로 **DB가 갈린다.** 레포 `README.md`는 5433 단독 컨테이너를
처방하는데(user `pinlog`/`pinlog`), 데모는 back의 compose(15432, `pinlog`/
`pinlog-local`)를 쓴다. back과 ai가 같은 DB를 봐야 시딩이 성립하기 때문이다.
I21의 F2(DSN 3중 불일치)가 남긴 함정과 같은 종류라 `tools/demo_seed/README.md`에
"같은 DB가 아니다"를 명시했다.

컨테이너 프로젝트 이름을 `pinlog-demo`로 둔 것은 다른 세션이 쓰는 `back` 프로젝트
컨테이너를 `docker compose`가 재생성하지 않게 하려는 것이다.

---

## 범위 밖으로 둔 것

- **dev·운영 DB 시딩** — `Forbidden`. 로컬과 재현 절차까지가 이 작업이다.
- **테스트 픽스처와의 통합** — `tests/`는 Testcontainers로 자체 DB를 쓴다. 섞지 않는다.
- **`tools/e2e/`와의 통합** — 두 데이터가 같은 DB에 공존해도 서로 보이지 않는다.
  e2e 데이터는 `core`에 대응 행이 없어 Feed 조인에 걸리지 않고, `/search`는
  `userId`로 갈린다. 실제로 이번에 같은 DB에서 둘을 함께 돌렸다.

## 검증

```
python tools/e2e/run_pipeline.py             Context 8건 → 6.0s 전부 COMPLETED
python tools/e2e/run_search.py               200·422·401 통과, 관련 6건 전원 1위, 간격 +0.2120
python tools/e2e/run_equivalence.py          샘플 06까지 후보 Jaccard 1.00 → 429로 중단
python tools/demo_seed/seed.py --reset       1회차 14/14(회수 0) · 2회차 14/14(회수 1)
python tools/demo_seed/verify.py             1·2회차 모두 A·B·C 전부 PASS
ruff check .                                 All checks passed
python -m compileall app tools               OK
pytest --cov=app --cov-branch                69 passed · TOTAL 77%
```

`app/` 변경이 없으므로 pytest 결과와 커버리지는 `main`과 같다.

## 남은 것

- `run_equivalence.py`·`run_attribution.py`의 **429 대응**(호출 간격·재시도).
  현행 설계로는 분당 2건 쿼터에서 완주가 불가능하다. 별도 티켓 대상.
- **어디에 시딩할 것인가** — dev 환경 시딩 여부·시점은 배포 담당과 별도로 정한다.
- `BLOCKED` 프리셋 실데이터 검증 — I21에서 이월. 프리셋에 BLOCKED가 생기면 자연 해소.
- **back 소관 2건** — 탐색 슬롯이 최고점 후보를 흡수하는 배치(계약 질문은 AI 파트
  소유), `RecordDetailResponse`의 `keywords` 미구현. 이 PR에서 고치지 않고 중앙
  조정 세션에 `Need Decision`으로 올린다.
