> 구현 반영됨(ai#5·#6, `app/`). 이 문서는 계약 명세이며 구현이 이를 따른다. 리포트: [implements/2026-07-23-fastapi-implementation.md](../implements/2026-07-23-fastapi-implementation.md).
> 공용 계약은 Team-PinLog/docs의 `static/05_AI_설계.md`를 따릅니다.

# 개인 자연어 검색

근거 계약: `static/05_AI_설계.md` §9 개인 자연어 검색, §13.2 개인 검색

## 1. 엔드포인트

```text
POST /internal/v1/search
```

요청값: `userId`, `query`, `limit`, `embeddingProfile`
응답값: `recordId`, `contextId`, `similarity` 목록

`userId`는 필수이며 **검색 범위 필터로만** 사용합니다. FastAPI는 인증을 판단하지 않습니다.
반환된 Record ID는 `ai` 스키마 기준 결과이므로 소유권·삭제 여부·활성 Context 존재 여부는
Spring이 Core 기준으로 다시 검증합니다(계약 §9.5).

## 2. 질의 Embedding

질의는 **분해하지 않고 전체를 한 번** Embedding합니다.

- 검색어 LLM 분해를 하지 않습니다.
- 독립 Place 후보 검색, 독립 Keyword 후보 검색을 하지 않습니다.
- Embedding 호출은 요청당 정확히 1회입니다.

요청의 `embeddingProfile`이 서버 설정 Profile과 다르면 질의 벡터를 저장된 벡터와 비교할 수
없으므로 검색을 수행하지 않고 요청을 거부합니다([model-profile.md](model-profile.md)).

## 3. 필터 우선, 벡터 나중

MVP는 **정확 cosine 검색**을 사용합니다.

> **HNSW와 IVFFlat을 사용하지 않습니다.** ANN 인덱스는 MVP 제외 범위이며,
> 데이터 증가 이후의 확장 항목입니다(계약 §9.4, §15.2, §15.3).

정확 검색이므로 후보 행 수가 곧 비용입니다. 따라서 벡터 연산 전에 스칼라 조건으로 후보를
최대한 좁힙니다. `user_id`가 가장 강한 필터이며, `ai.context_embedding`이
`user_id`·`record_id`를 비정규화해 들고 있는 이유가 이것입니다.

```text
user_id 일치
→ is_deleted = false
→ embedding_status = COMPLETED
→ Embedding Profile 일치
→ (여기까지 좁힌 뒤) exact cosine 계산
```

필터 목록은 계약 §9.3과 동일합니다. Context가 불변이므로 본문 버전을 대조하는 조건은 없습니다.

ANN 인덱스가 없으므로 순서를 바꾸면 사용자 전체 벡터를 스캔하게 됩니다.
Query를 작성할 때 필터 조건이 벡터 연산 아래로 내려가지 않도록, 필터를 CTE로 분리하거나
서브쿼리에 고정합니다.

## 4. Query

```sql
SELECT record_id, context_id, similarity
FROM (
    SELECT DISTINCT ON (e.record_id)
           e.record_id,
           e.context_id,
           1 - (e.embedding <=> :query_embedding) AS similarity
    FROM ai.context_embedding e
    JOIN ai.context_ai_state s
      ON s.context_id = e.context_id
    WHERE e.user_id = :user_id
      AND e.is_deleted = false
      AND e.embedding_profile = :embedding_profile
      AND s.embedding_status = 'COMPLETED'
    ORDER BY e.record_id, similarity DESC
) t
ORDER BY similarity DESC
LIMIT :limit;
```

`DISTINCT ON (record_id)`은 안쪽 `ORDER BY record_id, similarity DESC` 기준으로 Record별
첫 행(=최고 유사도 Context)만 남긴다. 이 대표 Context의 `context_id`를 함께 반환해 Spring이
core에서 본문을 조회·조립할 수 있게 한다(본문 자체는 반환하지 않는다, §6).

조건별 역할:

| 조건 | 역할 |
|---|---|
| `e.user_id = :user_id` | 검색 범위를 본인 Context로 한정. 타인 데이터 차단(검증 시나리오 19) |
| `e.is_deleted = false` | 삭제된 AI 파생 데이터 제외. Spring만 이 값을 변경 |
| `s.embedding_status = 'COMPLETED'` | 미완료·실패·CANCELLED 제외 |
| `e.embedding_profile = :embedding_profile` | 차원·거리 기준이 다른 벡터 제외 |

`<=>`는 pgvector의 cosine distance 연산자이며, 유사도는 `1 - distance`로 환산합니다.
거리 기준을 cosine으로 고정하는 근거는 Embedding Profile입니다.

`CANCELLED` 제외는 `embedding_status = 'COMPLETED'` 조건에 이미 포함됩니다.
`is_deleted`와 CANCELLED는 서로를 대체하지 않는 두 개의 방어선이므로 두 조건을 모두 유지합니다.

### 수정으로 대체된 구 Context

Context 수정은 구 Context 삭제와 신 Context 생성의 조합이므로(계약 §4.2, §5.3),
구 Context는 위 두 조건 **모두**에 걸려 검색에서 제외됩니다.

```text
구 Context: is_deleted = true  AND  embedding_status = CANCELLED
신 Context: 새 context_id로 별도 행이 생기고, 자신의 처리가 끝나면 검색 대상이 됨
```

본문 버전을 대조하는 조건은 두지 않습니다. 같은 `context_id`에 두 가지 본문이 존재할 수
없으므로 검사할 대상이 없습니다(검증 시나리오 3).

## 5. Record 단위 집계

유사도는 Context 단위로 계산하고 사용자에게는 **Record 단위**로 반환합니다.

- `DISTINCT ON (record_id)`로 Record별 **최고 유사도 Context 한 행**만 남깁니다. 한 Record의
  여러 Context가 매칭되어도 Record는 한 번만 반환됩니다(검증 시나리오 20).
- Record 유사도는 그 Record에 속한 Context 유사도 중 **최댓값**입니다. `DISTINCT ON`이
  안쪽 `ORDER BY similarity DESC`로 최댓값 행을 고르므로 평균·합계를 쓰지 않습니다.
  Context는 서로 독립적인 저장 이유이므로, 하나만 강하게 일치해도 그 Record는 찾는 대상입니다.
- 그 최고 유사도 Context의 `context_id`를 대표값으로 함께 반환합니다. Spring이 어느 Context가
  매칭됐는지 알아야 core에서 본문을 조회해 응답을 조립할 수 있기 때문입니다.
- `LIMIT`은 집계(DISTINCT ON) **후에** 적용합니다. 집계 전에 자르면 서로 다른 Record 수가
  요청한 `limit`보다 적게 나올 수 있습니다.

## 6. 반환 형식

```json
{
  "results": [
    { "recordId": 1024, "contextId": 5567, "similarity": 0.8213 },
    { "recordId": 993,  "contextId": 5490, "similarity": 0.7740 }
  ]
}
```

- **Context 본문은 반환하지 않습니다.** 원문 조회와 응답 조립은 Spring의 책임입니다.
- 단 매칭 Context의 `contextId`는 반환합니다. `context_id`는 `ai` 스키마가 이미 보유한
  본인 소유 식별자이며, Spring은 이 값으로 `core`에서 본문을 조회해 `matchedContext`를
  조립합니다. id가 없으면 어느 Context가 매칭됐는지 알 수 없어 조립이 성립하지 않습니다.
  FastAPI는 id만 반환하고 `core`를 읽지 않으므로 스키마 경계는 유지됩니다.
- Keyword를 함께 반환하지 않습니다. Keyword Visibility에 따른 노출 판단은 Spring이 합니다.
- 검색 결과에 **두 개의 컷**을 겁니다(§6.1). 노출 여부의 최종 판단은 여전히 Spring의 몫이며,
  이 컷은 「보여줄지」가 아니라 「후보로 넘길 가치가 있는지」를 가릅니다.

## 6.1 결과 컷 — `τ_abs`와 `r`

```text
반환 = { x ∈ 상위 limit개 :  x.sim ≥ τ_abs(질의)   ∧   x.sim ≥ r × top1.sim }

  SEARCH_SIMILARITY_FLOOR        τ_abs = 0.30   절대 하한 — 문장형 질의
  SEARCH_SIMILARITY_FLOOR_WORD   τ_abs = 0.24   절대 하한 — 단어형 질의
  SEARCH_TOP_RATIO               r     = 0.60   1위 대비 상대 하한 (갈리지 않는다)

  단어형 = 공백이 없고 SEARCH_WORD_QUERY_MAX_CHARS(5) 자 이하
```

**`τ_abs` 는 질의 길이로 갈립니다**(S15P11A705-266, §값의 근거 둘째 절). 위 표의
「`τ_abs`는 질의마다 다른 유사도 대역을 따라가지 못한다」가 단어형 질의에서 실제 손실로
드러났습니다. `r` 은 갈리지 않습니다 — 상대 컷이라 대역 차이를 자동으로 흡수합니다.

둘 중 하나를 0으로 두면 그 컷이 꺼집니다. **하나가 다른 하나를 대체하지 않습니다** —
서로 다른 실패 모드를 막습니다.

| | `τ_abs`가 막는 것 | `r`이 막는 것 |
|---|---|---|
| 상황 | 이 사용자에게 **관련 기록이 아예 없다** | 관련 기록은 있는데 **꼬리가 길다** |
| 없으면 | 「자동차 엔진오일 정비소」에 카페·라멘집 17건이 나온다 | 1위 0.82 아래로 0.14까지 줄줄이 붙는다 |
| 다른 쪽으로 되나 | `r`은 1위를 **언제나** 남기므로 0건을 만들 수 없다 | `τ_abs`는 질의마다 다른 유사도 대역을 따라가지 못한다 |

`LIMIT` **뒤에** 겁니다. 두 컷 모두 유사도 하위만 자르므로 §4 Query의 `WHERE`에 넣은
것과 결과가 같고(유사도 단조), 그렇다면 이미 고정된 Query를 건드리지 않는 쪽이 낫습니다.
정확 검색이라 스캔 비용도 달라지지 않습니다.

`r`의 기준이 되는 1위는 **컷 전** 결과의 1위입니다. 컷 후 재계산하면 기준이 살아남은
것의 1위로 옮겨가 아무것도 더 잘리지 않는 자기충족 컷이 됩니다.

### 값의 근거 (S15P11A705-213)

검증 질의 12건(`tools/demo_seed/demo_data.yaml`, 기대 정답 부착)과 **정답이 없는 무관 질의
15건**(질의 5종 × 소유자 3명)으로 `τ_abs × r` 격자를 훑었습니다. 하네스는
`tools/search_cut/`, 리포트는 [implements/2026-07-31-search-cut.md](../implements/2026-07-31-search-cut.md).

```text
채택값 τ_abs=0.30 · r=0.60     정답 누락 0/12 · 빈 결과 0/12
                              꼬리 제거 76.3%(비관) · 무관 질의 11/15 침묵
안전 상한 τ_abs=0.36 · r=0.80  이 이상에서 정답이 사라진다
```

**상한에 붙이지 않은 이유**: 두 축의 상한을 **같은 데이터점 하나**가 정합니다 — 질의
「친구들이랑 피자에 맥주 마신 곳」의 정답이 3위 `sim=0.3642`·`r=0.807`입니다. 그 한 점이
흔들리면 두 축이 동시에 무너지므로 각각 마진을 뒀습니다(τ_abs 17% · r 25%).

### 단어형 하한의 근거 (S15P11A705-266)

위 값은 **문장형 질의 12건으로 정했습니다.** 단어형 질의(`그네`·`비건`)로 다시 재자
0.30 이 **컷 전 1위인 정답**까지 잘라내고 있었습니다(`ai#87`). 단어형 54건 × 소유자 3명
(정답 66행 · 무관 통제 45행)으로 격자를 다시 훑은 결과입니다. 하네스는
`tools/search_cut/word_matrix.py`·`word_sweep.py`, 리포트는
[implements/2026-08-03-word-query-cut.md](../implements/2026-08-03-word-query-cut.md).

```text
두 대역이 겹치지 않는다   문장형 정답 하한 0.3642   단어형 정답 하한 0.2438

0.30 단일   단어형에서 컷 전 1위 정답 5건이 0건이 된다
0.24 단일   그 5건이 살아나지만 문장형 무관 질의 침묵이 11/15 → 5/15 로 무너진다
가른다      단어형 회복 71/71 · 1위 손실 0 · 문장형 완전 불변
```

**0.24 는 「컷 전 1위인 정답을 하나도 잃지 않는 가장 높은 값」입니다** — 0.25 부터
깨집니다. 최저 정답이 `스팟` 0.2438 이라 마진이 0.0038 뿐이고, **경계를 데이터점 하나가
정한다는 것**이 이 값의 알려진 약점입니다(리포트 §리스크).

그 마진이 `T68`(`-228`, 임베딩 비결정성 `|Δsim|` 최대 0.0044)보다 작아 회차 3개로
직접 쟀습니다 — **이 조건의 흔들림 상한은 0.000209 이고 경계점 `스팟` 은 스프레드
0.000000, 격자 판정은 τ=0.20~0.30 전 구간에서 3회 일치**합니다(리포트 §재현성).

### 이 값을 다시 재야 하는 때

`0.24` 는 **데이터점 하나**(`스팟` → 동교어린이공원 0.2438)가 정합니다. 재측정 잡음은
위협이 아니지만(위 문단) **그 점이 이동하면 값이 무너집니다** — 여유를 두어도 경계를
한 점이 정한다는 구조는 사라지지 않으므로, 마진을 넓히는 대신 **언제 다시 재는지를
명시합니다.**

| 방아쇠 | |
|---|---|
| 시연 DB 재시딩 | Context 본문이 같아도 배치가 달라지면 유사도가 움직입니다 |
| Context 수의 유의미한 증가 | `top-1` 이 올라가 `r` 이 더 세게 자르고 무관 통과도 함께 늘어납니다(`-213` 이 남긴 것과 같은 조건) |
| 임베딩 모델 교체 (`S15P11A705-199`) | 벡터 공간이 바뀌므로 두 하한 **모두** 무효입니다 |

```bash
# 행렬을 다시 뜨고 (GMS 배치 1회), 회차를 하나 더 떠서 흔들림부터 본다
python tools/search_cut/word_matrix.py
python tools/search_cut/word_matrix.py --out .search/word_grid_run2.json
python tools/search_cut/word_sweep.py --repro .search/word_grid.json,.search/word_grid_run2.json
python tools/search_cut/word_sweep.py     # 격자 재확인
```

**회차 판정이 갈리기 시작하면 그 구간의 인접 값 비교를 신뢰하지 말고 결론을 보류합니다**
(`T68` 이 `base2` 를 상시 조건으로 둔 것과 같은 이유). 채택 기준은 「컷 전 **1위**인 정답을
하나도 잃지 않는 가장 높은 값」입니다 — 회복률이 아닙니다.

**경계 5 자는 측정이 아니라 판단입니다.** 측정한 단어형이 전부 공백 없는 2~5자라
「글자 수」와 「어절 수」 두 정의가 같은 답을 냈습니다. 두 조건을 **함께** 요구해 애매한
질의가 문장형(더 세게 자름) 쪽으로 기울게 했습니다.

### 이전 판단을 뒤집습니다

직전 판까지 이 문서는 「컷오프를 적용하지 않는다」였고, 근거는 무관 질의 **1건**의 실측
(top-1 0.3143, 관련 질의 top-1 최소 0.5263과 간격 +0.2120)이었습니다. 무관 질의를 15건으로
늘리자 **그 간격이 사라졌습니다.**

```text
무관 질의 top-1 최댓값  0.3819   (「치과 임플란트 상담 받을 곳」 → 연남칼국수)
기대 정답 최솟값        0.3642
                       간격 -0.0176   겹친다
```

**어떤 `τ_abs`도 무관 질의를 전부 침묵시키면서 정답을 전부 살릴 수는 없습니다.** 이전
판단이 본 여유는 표본 1건의 우연이었습니다. 다만 그것이 「그러므로 컷을 걸지 말자」를
뜻하지는 않습니다 — 컷이 없으면 무관 질의에 **보유 기록 전량**이 반환되고, 실측에서는
`τ_abs=0.30` 하나로 무관 질의 15건 중 11건이 0건이 되면서 정답은 하나도 잃지 않았습니다.

## 6.2 오류 응답

검색은 요청당 정확히 1회 Embedding을 호출하므로(§2), 실패의 대부분이 그 호출입니다.
업스트림 실패는 **분류에 따라 상태 코드가 갈립니다** —
[failure-recovery.md §2.5](failure-recovery.md)가 정본이고 여기서는 검색 경로 기준으로만
요약합니다.

| 상황 | 응답 |
|---|---|
| Embedding 게이트웨이 5xx·`429`·타임아웃, 재시도 소진 | `503` |
| Embedding 인증 실패·모델명 오류·차원 불일치 | `502` |
| 요청 Profile ≠ 서버 Profile | `422` (`requestProfile`·`serverProfile` 동봉) |
| 공유 시크릿 불일치 | `401` |
| 그 밖 | `500` — 우리 코드의 결함 |

`422`만 본문에 값을 싣습니다. 어느 쪽 설정을 고쳐야 하는지가 두 값의 비교에서만 나오고,
그것이 `back`이 유일하게 파싱하는 필드이기 때문입니다(`AiSearchClient.translate`).
나머지는 고정 문구 한 줄이며 **credential·endpoint·검색어를 싣지 않습니다.**

계약 테스트는 `tests/test_api_error_contract.py`이며 업스트림 상태 코드부터 응답 상태
코드까지 한 요청으로 관통해 고정합니다.

## 7. 하지 않는 것

- HNSW / IVFFlat 인덱스 생성 (인덱스는 back의 migration 소유이기도 합니다)
- 검색어 LLM 분해
- 타인 Context·Collection 검색
- Place 이름 검색·지도 검색 (카카오맵 장소 검색 기능으로 별도 유지)
- Record 삭제 여부와 소유권 판단 (Spring의 Core 재검증)
