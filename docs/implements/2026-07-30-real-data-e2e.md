# 실사용자 데이터 E2E 검증 — 정확도·소요 시간·토큰 실측

- **티켓**: S15P11A705-174
- **날짜**: 2026-07-30
- **선행**: [데모 시딩](2026-07-29-demo-seeding.md) (`S15P11A705-58`) · [E2E 검증](2026-07-27-e2e-verification.md)
- **함정**: [T27·T28](../troubleshooting/2026-07-30-seeding-quota-and-encoding.md)

## 요약

가공 데모 14건에 **실사용자 기록 23건**을 더해 37건으로 전체 통합 경로를 돌렸다.

```
검색 정확도   10 / 12  (83.3%)      가공 4/4 · 실데이터 6/8
탐색 피드     PASS                  15장 · 소유자 6명 · 본인 것 0건
Keyword      PASS                  74행 · 피드 카드 15/15 표시
시딩 소요     15분 8초               37건 · 회수 0회 · retry_count>0 = 0건
토큰          32,912                 판정이 임베딩의 17배
```

**실패 2건은 원인이 서로 다르고, 하나는 설계 관측이다**(아래 §3.2).

## 1. 실행 환경

| | |
|---|---|
| DB | PostgreSQL + pgvector, `pinlog-demo` compose (15432) · Flyway V5 |
| back | `dev` `4a9ab36` — `#104` 재스캔 Scheduler 포함 |
| FastAPI | `dev` `fbdb2dd` + 이 브랜치의 토큰 계측 |
| Embedding | `openai-text-embedding-3-small-1536-cosine-v1` |
| Judge | `gemini-2.5-flash`, `thinkingBudget=0` |
| 데이터 | member 7 · Record 37 · Collection 16 · Follow 5 |

실데이터는 두 사람의 실제 방문 기록이다(김가현 11건 · 이정헌 12건). 가공 데이터와 달리
`[데모]` 접두사를 붙이지 않았다 — 실제 기록을 가공으로 위장하면 오히려 오해를 만든다.

## 2. 소요 시간

```
시딩 전체     15분 8초   (06:36:32Z → 06:51:40Z)
검증 전체     11초       (06:52:02Z → 06:52:13Z)
```

| 단계 | 값 |
|---|---|
| 임베딩 | 병목이 아니다. 호출당 1초 미만 |
| 판정 | `--pace 25` 로 25.1초 간격. **이것이 총 시간을 결정한다** |
| 회수 루프 | **0회** — 37건 전부 첫 시도 통과, `retry_count > 0` 인 행이 0 |
| 429 | 0건 |

**37건 × 25초 ≈ 15.4분**이라는 사전 추정이 실측 15분 8초와 맞았다. 어제 14건 6분에서
건수에 선형으로 늘어난 것이 확인된다.

`--pace 25` 는 GMS Gemini 쿼터(분당 약 2건, [T27](../troubleshooting/2026-07-30-seeding-quota-and-encoding.md))
안에 들어가는 값이다. **줄이면 총 시간이 줄지 않는다** — 앞에서 몰아 던진 만큼 429가 나고
그 Context가 10분간 얼어 뒤에서 회수해야 한다.

## 3. 정확도 — 자연어 검색 12건

### 3.1 결과

| # | 질의 | 주체 | 1위 | 유사도 | 2위와 차 | 판정 |
|---|---|---|---|---|---|---|
| 1 | 비 오는 날 가려고 저장한 곳 | host | 골목 안 다방 | 0.5020 | +0.1528 | PASS |
| 2 | 혼자 조용히 작업하기 좋은 카페 | host | 창가 작업실 카페 | 0.4969 | +0.1674 | PASS |
| 3 | 친구들이랑 시끌벅적하게 놀 만한 곳 | host | 연남 골목 선술집 | 0.6414 | +0.2345 | PASS |
| 4 | 기념일에 야경 보면서 식사할 곳 | host | 언덕 위 야경 식당 | 0.5266 | +0.0778 | PASS |
| 5 | 돈카츠 먹으러 자주 갔던 곳 | jeongheon | 카츠요 | 0.6069 | +0.1244 | PASS |
| 6 | 미슐랭에 오른 라멘집 | jeongheon | 사루카메 | 0.5191 | **+0.0087** | PASS |
| 7 | 친구들이랑 피자에 맥주 마신 곳 | jeongheon | 카츠요 | 0.4512 | — | **FAIL** |
| 8 | 밥 먹고 산책하면서 쉬어가는 공원 | jeongheon | 치킨버거 이스트사이드 | 0.5263 | — | **FAIL** |
| 9 | 채식 샌드위치 먹던 단골집 | jeongheon | 플랜트 연남점 | 0.5682 | +0.1128 | PASS |
| 10 | 책 사면 꽃을 주는 서점 | gahyeon | 오케이어 맨션 | 0.8023 | +0.4833 | PASS |
| 11 | 화덕에 구운 피자집 | gahyeon | 주토피아 서울 | 0.5116 | +0.1146 | PASS |
| 12 | 양갱 파는 분위기 좋은 카페 | gahyeon | 적당 | 0.8188 | +0.3766 | PASS |

```
전체      10/12  83.3%
가공        4/4  100%
실데이터    6/8   75%
```

**6번은 0.0087 차이로 통과했다.** 사루카메(미슐랭 라멘)와 쿠로코식당(라멘) 둘 다 라멘집이라
분리가 어려운 것이 정상이며, 이 정도 차이는 다음 실행에서 뒤집힐 수 있다. 성공으로 세되
안정적이라고 보지 않는다.

### 3.2 실패 원인 — 둘이 다르다

**7번 「친구들이랑 피자에 맥주 마신 곳」 — 축약어가 안 이어졌다**

```
기대  뉴오더클럽 연남  3위 0.3642   "…신한 친구들이랑 피맥했고 … 같이 가서 피맥함"
실제  카츠요           1위 0.4512   "6개월 동안 신한 부트캠프 친구들과 자주 먹었던 돈카츠 집"
```

본문의 **「피맥」이 질의의 「피자에 맥주」와 임베딩상 멀다.** 반면 질의의 「친구들이랑」이
두 본문 모두에 강하게 걸려, 그 축이 승부를 갈랐다. 축약어·은어가 임베딩에서 풀리지 않는
전형적 사례다.

**8번 「밥 먹고 산책하면서 쉬어가는 공원」 — 장소명이 임베딩에 없다**

```
기대  동교어린이공원        2위 0.4573   "그네팟 스팟. 밥먹고 산책하면서 여기 머물다가…"
실제  치킨버거 이스트사이드   1위 0.5263   "치킨버거 맛있더라. 이거 사들고 그네 공원 갔음"
```

본문만 보면 동교어린이공원이 질의와 거의 같은 문구(「밥먹고 산책하면서」)를 담고 있는데도
2위다. 원인은 **질의의 「공원」이 본문에 없다**는 것이다 — 그 단어는 장소명에만 있고,
치킨버거 쪽은 본문에 「공원」이 들어 있다.

임베딩 입력이 **Context 본문 하나**이기 때문이다.

```python
# tools/demo_seed/seed.py:334 — back 도 같은 필드를 보낸다
"text": rec["context"]
```

**이것은 버그가 아니라 현재 설계다.** 다만 사용자는 장소명을 검색어에 쓴다는 것이
실데이터에서 처음 드러났다. 개선 후보는 §6에 적는다.

### 3.3 이 정확도를 어떻게 읽어야 하는가

**기대값은 내가 정했다.** 질의 8건과 정답을 이 검증을 위해 작성했으므로, 83.3% 는
"사용자가 실제로 그 질의를 했을 때의 만족도"가 아니라 **"내가 의도한 매칭이 재현되는 비율"**이다.

두 실패 모두 기대한 Record 가 1~3위 안에는 들어 있다. 순위가 아니라 **1위 일치**를
기준으로 세었기 때문에 FAIL 이며, top-3 기준이면 12/12 다.

## 4. 탐색 피드 — PASS

```
requestId=a1f45a33…  items=15  hasNext=False
후보 비지 않음       PASS  15건
여러 소유자 혼재     PASS  소유자 6명
본인 Collection 제외 PASS  0건
```

실데이터 Collection 7개(가보고 싶은 카페·다녀온 맛집·분위기 좋은 곳·연남 단골·부캠 시절
밥집·그네팟 코스·면 요리)가 가공 Collection 과 섞여 나왔다. `max-per-owner` 상한이
소유자별로 작동하는 것이 소유자 6명 분포로 확인된다.

## 5. Keyword — PASS

```
Context 부착        74행 (주인공 6 Record 에 18행)
피드 카드 표시       15/15장
PRIVATE_ONLY 미노출  PASS  (ANNIVERSARY* 가 타인 화면에 없음)
```

**관측 1건** — `GET /v1/records/{id}` 의 `keywords` 가 `[]` 다. back 의
`RecordDetailResponse.of` 가 `List.of()` 를 고정 반환한다(미구현). Record 상세 화면에는
Keyword 가 나오지 않는다. `S15P11A705-58` 에서 이미 발견된 back 소관 결함이며 이 티켓
범위가 아니다.

## 6. 토큰 사용량

```
[embedding]  49회      1,845 토큰   건당    37.7
[judge]      37회     31,067 토큰   건당   839.6   prompt 790.2 + output 49.5
             thoughts      0        thinkingBudget=0 이 실제로 먹힌다
─────────────────────────────────────────────────
총           86회     32,912 토큰   15.6분
```

임베딩 49회 = 시딩 37 + 검색 질의 12. 별도로 **Keyword Preset 적재 1배치 2,157 토큰**이
시딩 전에 들었다(27건 한 번에).

### 판정이 임베딩의 17배를 쓴다

건당 839.6 대 37.7 이다. 원인은 **판정 프롬프트가 후보 Keyword 27개를 매번 싣는다**는 것이다
— `prompt` 790 중 대부분이 후보 목록이고, Context 본문은 수십 토큰에 불과하다.

```
Context 1건 처리 = 임베딩 37.7 + 판정 839.6 ≈ 877 토큰
```

Preset 이 늘면 판정 prompt 가 선형으로 늘어난다. 지금은 27개지만 프리셋을 확장할 때
**비용이 Context 수 × Preset 수로 곱해진다**는 것을 알고 결정해야 한다.

### 개선 후보 (이 티켓 범위 밖)

| 후보 | 기대 | 위험 |
|---|---|---|
| 임베딩 입력에 장소명 포함 | §3.2 8번 유형 해소 | 공용 계약 `static/05` §7 변경. 기존 임베딩 전량 재생성 |
| 판정 후보를 top-K 로 줄여 전달 | prompt 790 → 대폭 감소 | 후보에서 빠진 Keyword 는 절대 안 붙는다. `KEYWORD_CANDIDATE_TOP_K` 가 이미 있으므로 실제 적용 여부 확인 필요 |
| 축약어·은어 사전 | §3.2 7번 유형 | 유지 비용. MVP 범위 아님 |

## 7. 재현

```bash
cd back && docker compose -p pinlog-demo up -d
cd ../ai
python -c "import sys; sys.path.insert(0,'tools/demo_seed'); import _client; _client.ensure_key()"

DATABASE_URL="postgresql://pinlog:pinlog-local@localhost:15432/pinlog" \
PINLOG_TOKEN_LOG=".demo/token-usage.jsonl" \
  python -m app.bootstrap.load_presets

DATABASE_URL="..." PINLOG_TOKEN_LOG="..." python -m uvicorn app.main:app --port 8000

cd ../back && JWT_PRIVATE_KEY="$(cat ../ai/.demo/demo-jwt-key.pem)" \
  PINLOG_AI_INTERNAL_SECRET="<ai .env 의 INTERNAL_SHARED_SECRET>" \
  PINLOG_AI_BASE_URL=http://localhost:8000 SPRING_PROFILES_ACTIVE=local \
  java -jar build/libs/pinlog-back-0.0.1-SNAPSHOT.jar

cd ../ai
DATABASE_URL="..." PINLOG_TOKEN_LOG="..." python tools/demo_seed/seed.py --reset --pace 25
DATABASE_URL="..." python tools/demo_seed/verify.py
python tools/demo_seed/token_report.py .demo/token-usage.jsonl
```

`PYTHONIOENCODING` 은 더 이상 필요 없다 — 두 스크립트가 stdout 을 스스로 UTF-8 로
재구성한다([T28](../troubleshooting/2026-07-30-seeding-quota-and-encoding.md)).

## 8. 검증하지 않은 것

- **`tools/e2e/` 드라이버 4종** — 이번에는 `demo_seed` 경로만 돌렸다
- **재스캔 Scheduler 실동작** — `#104` 가 포함된 jar 로 띄웠지만 429가 0건이라
  스케줄러가 회수할 대상이 없었다. **동작을 관측하지 못했다**
- **반복 측정** — 검색 정확도는 1회 실행 결과다. 6번(차이 0.0087)처럼 아슬아슬한 건은
  재실행에서 뒤집힐 수 있다
- **프론트 화면** — API 응답까지만 확인했다
