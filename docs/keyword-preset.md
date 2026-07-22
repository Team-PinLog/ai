> 현재 코드가 없는 구현 예정 명세입니다.
> 공용 계약은 Team-PinLog/docs의 `static/05_AI_설계.md`를 따릅니다.

# Keyword Preset과 LLM 판정

근거 계약: `static/05_AI_설계.md` §8 Keyword Preset, §12.1 keyword_preset, §12.4 context_keyword, §12.5 context_keyword_analysis

## 1. 전제

Keyword는 **LLM이 자유 생성하지 않습니다.**

```text
Context Embedding
→ Preset Embedding 유사도 후보 검색
→ 후보만 LLM에 전달
→ 후보 keyword_id 중 선택
```

타인에게 공개되는 Keyword의 안전성은 "Preset이 사전 정의 목록"이라는 전제에 의존합니다.
구현이 이 전제를 깨는 지점은 두 곳이며, 둘 다 코드로 막습니다.

1. LLM에게 후보 밖 값을 만들 수 있는 여지를 주는 것 → 구조화 출력 + 후보 ID 제약(§4)
2. 반환값을 검증 없이 저장하는 것 → 매핑 단계 폐기(§4.3)

## 2. Preset Cache

Preset은 25~30개 규모이고 변경은 배포 작업으로만 이루어지므로 프로세스 메모리에 캐싱합니다.
Cache 위치와 수명은 [architecture.md](architecture.md) §4에 정의되어 있습니다.

적재 Query:

```sql
SELECT id, code, display_name, category, description, examples,
       visibility, version, embedding
FROM ai.keyword_preset
WHERE active = true
  AND embedding_profile = :embedding_profile;
```

- 폐기된 Preset은 행 삭제가 아니라 `active = false`이므로 적재 시 제외합니다.
- 현재 Profile과 다른 Preset은 애초에 적재하지 않습니다. 비교가 성립하지 않는 벡터를
  후보 집합에 두면 §3의 Profile 검사가 무의미해집니다.
- 적재 결과가 0건이면 기동을 실패로 처리합니다. Preset 없이 뜬 서버는 모든 Context를
  "Keyword 없음"으로 정상 완료시켜 조용히 데이터를 망칩니다.
- `BLOCKED` Preset은 후보 집합에서 제외합니다. `BLOCKED`는 본인 제공·타인 공개·개인화 Profile·
  Feed 특징 어디에도 쓰이지 않으므로 판정 대상으로 삼을 이유가 없습니다.
  Spring 응답 조립에서도 다시 제외되므로 이중 방어가 됩니다(검증 시나리오 11).

`preset_version`으로 저장할 값은 판정에 사용한 Preset 세트의 버전입니다.
Cache 적재 시 스냅샷 버전을 함께 확정해 두고, 그 요청 처리 전체에서 같은 값을 사용합니다.
후보 검색과 저장 사이에 Cache가 재적재되어 버전이 섞이는 일이 없도록, 파이프라인은
Cache 스냅샷 객체를 한 번 잡아 끝까지 들고 갑니다.

## 3. 후보 TOP-K 검색

Context Embedding을 질의 벡터로 사용해 Preset 중 상위 K개를 뽑습니다.

- 거리 기준은 cosine이며, Context Embedding과 동일한 Profile을 사용합니다.
- Preset이 25~30개이므로 Cache에 적재된 벡터로 메모리에서 계산합니다.
  DB 왕복과 인덱스가 필요 없는 규모입니다.
- K는 설정값(`KEYWORD_CANDIDATE_TOP_K`)으로 두고 기본값 10을 사용합니다.
  전체가 30개 내외이므로 K를 지나치게 키우면 "후보로 좁힌다"는 의미가 사라지고
  LLM이 무관한 Keyword를 고를 여지가 커집니다.
- 유사도 하한을 함께 두어, 하한 미만인 후보는 K에 미달하더라도 제외합니다.
  후보가 0개가 되는 것은 허용되며, 이 경우 LLM을 호출하지 않고 "선택 0개"로 정상 완료합니다.

### Profile 불일치

Context Embedding의 Profile과 Preset의 Profile이 다르면 **판정을 중단**합니다
(계약 §7.3, 검증 시나리오 9).

- LLM을 호출하지 않습니다.
- `ai.context_keyword`에 아무것도 쓰지 않습니다.
- "Keyword 없음"으로 COMPLETED 처리하지 않습니다. 이는 판정 결과가 아니라 판정 불가입니다.
- 상태 처리는 [failure-recovery.md](failure-recovery.md)의 영구 오류 규칙을 따릅니다.

## 4. LLM 판정

### 4.1 입력

LLM에 전달하는 것:

- Context 본문
- 후보 Preset 목록: `keyword_id`, `display_name`, `category`, `description`, `examples`

`description`과 `examples`는 판정 품질을 좌우하므로 반드시 포함합니다.
후보 목록에 없는 Preset의 정보는 전달하지 않습니다.

### 4.2 출력

구조화 출력(JSON Schema)으로 받습니다.

```json
{
  "selected": [
    { "keywordId": 12, "confidence": 0.82 }
  ],
  "unmatchedConcepts": ["비 오는 날 혼자", "야근 후"]
}
```

- `keywordId`는 이번 요청의 후보 ID 집합으로 제약합니다. 스키마 수준에서 enum으로 고정할 수
  있으면 고정하고, 그렇지 않더라도 §4.3의 검증은 생략하지 않습니다.
- 자유 텍스트 Keyword 필드를 두지 않습니다. `unmatchedConcepts`만 자유 텍스트이며,
  이는 Keyword가 아니라 분석 데이터입니다.
- `confidence`는 0~1 범위로 받고, 범위를 벗어나면 클램프하지 않고 해당 항목을 폐기합니다.

### 4.3 매핑 단계 폐기

```python
candidate_ids = {p.id for p in candidates}
selected = [s for s in llm_result.selected if s.keyword_id in candidate_ids]
```

- 후보 집합에 없는 `keywordId`는 폐기합니다. 오류로 처리하지 않고 조용히 버립니다.
- 중복 `keywordId`는 `confidence` 최댓값으로 하나만 남깁니다.
- 전부 폐기되어 0개가 되어도 정상 완료입니다.
- 폐기가 발생한 사실은 로그로 남깁니다. Preset 설명 품질이나 프롬프트 문제를 드러내는 신호입니다.

## 5. 결과 저장

Keyword 저장은 **delete-insert**입니다. UPSERT가 아닙니다.

```sql
DELETE FROM ai.context_keyword
WHERE context_id = :context_id;

INSERT INTO ai.context_keyword
    (context_id, context_version, keyword_id, confidence, preset_version)
VALUES (...);   -- 0건일 수 있음
```

이유: Context와 Keyword는 1:N이고, 재판정 결과는 이전보다 **적을 수도** 있습니다.
UPSERT는 사라져야 할 이전 Keyword를 남깁니다. 특히 "이전엔 3개, 이번엔 0개"인 경우
UPSERT로는 구 Keyword가 그대로 공개됩니다(검증 시나리오 3).

저장 순서와 트랜잭션 경계는 [context-processing.md](context-processing.md) §4.7을 따릅니다.
`SELECT ... FOR UPDATE` 재검사 → DELETE → INSERT → `keyword_status = COMPLETED`가
하나의 트랜잭션 안에서 이루어집니다.

### 5.1 빈 결과는 정상 COMPLETED

- 선택된 Keyword가 0개여도 `keyword_status = 'COMPLETED'`입니다.
- 이를 FAILED나 PENDING으로 두지 않습니다. 재스캔이 계속 같은 Context를 다시 판정하게 됩니다.
- "Keyword 없음"과 "아직 처리 안 됨"은 애플리케이션이 반드시 구분해야 하는 두 상태이며,
  그 구분을 담당하는 것이 `keyword_status`입니다.
- 화면과 API 응답은 Keyword가 비어 있는 상태를 정상으로 취급합니다(계약 §5.2, 검증 시나리오 10, 15).

### 5.2 unmatchedConcepts

같은 트랜잭션에서 `ai.context_keyword_analysis`에 기록합니다.

```text
context_id
context_version
preset_version
unmatched_concepts
model_profile
updated_at
```

- 목적은 **Preset 보정을 위한 분석**입니다. 사용자에게 공개하지 않습니다.
- Context당 한 행이므로 UPSERT합니다.
- `unmatchedConcepts`가 비어 있어도 행은 남깁니다. "미매칭 없음"도 분석 데이터입니다.
- `model_profile`에는 판정에 사용한 모델 식별 정보를 기록합니다. Preset 보정 시 어떤 모델의
  판단이었는지 구분해야 하기 때문입니다.
- Preset 보정 자체는 3~4주차 1회 분석 후 배포 작업으로 처리하며, 자동 반영 경로를 두지 않습니다.

## 6. Visibility는 저장하지 않는다

`ai.context_keyword`에는 `visibility`가 없습니다. Visibility는 `ai.keyword_preset`의 속성이며,
`PUBLIC` / `PRIVATE_ONLY` / `BLOCKED`에 따른 노출 판단은 **Spring의 응답 조립**이 담당합니다.

FastAPI는 판정 결과만 저장하고 공개 범위를 판단하지 않습니다.
