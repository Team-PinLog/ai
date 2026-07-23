# Keyword 판정 프롬프트 (초안)

`/context/process`의 LLM 판정 단계에서 사용하는 프롬프트와 구조화 출력 스키마입니다.
근거 계약: `ai/docs/keyword-preset.md` §4. 이 프롬프트는 테스트 C로 검증하며, 확정본이 구현부에 그대로 들어갑니다.

## 입력

- Context 본문 텍스트 (개인 데이터)
- 후보 Preset 목록 (임베딩 유사도로 좁힌 TOP-K). 각 항목: `keyword_id`, `display_name`, `category`, `description`, `examples`
- 후보에 없는 Preset의 정보는 전달하지 않는다.

## System 프롬프트

```text
당신은 장소 기록 서비스의 Keyword 분류기입니다.

사용자가 장소를 저장한 이유를 적은 짧은 글(Context)과, 후보 Keyword 목록이 주어집니다.
후보 목록에서 이 Context에 실제로 들어맞는 Keyword만 고르세요.

규칙:
- 반드시 후보 목록의 keyword_id 중에서만 고릅니다. 목록에 없는 것을 만들지 마세요.
- 글에서 근거를 찾을 수 있는 것만 고릅니다. 그럴듯하다는 이유로 넣지 마세요.
- 하나도 맞지 않으면 빈 목록을 반환합니다. 억지로 채우지 마세요.
- 보통 0~3개입니다. 많이 고를수록 정확도가 떨어집니다.
- description은 그 Keyword의 의미 범위이고, examples는 실제 사용자 말투 예시입니다. 둘 다 참고하세요.
- confidence는 근거의 확실함을 0~1로 나타냅니다. 애매하면 낮게 줍니다.
```

## User 메시지 템플릿

```text
[Context]
{context_text}

[후보 Keyword]
{candidates}
```

`{candidates}`는 후보마다 한 줄:

```text
- id={keyword_id} | {display_name} ({category}) | 의미: {description} | 예: {examples를 · 로 연결}
```

## 구조화 출력 (JSON Schema)

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["selected"],
  "properties": {
    "selected": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["keywordId", "confidence"],
        "properties": {
          "keywordId": { "type": "integer", "enum": [<후보 id들>] },
          "confidence": { "type": "number", "minimum": 0, "maximum": 1 }
        }
      }
    }
  }
}
```

- `keywordId`의 `enum`은 **이번 요청의 후보 id 집합**으로 매 호출마다 채운다(스키마 수준 제약).
- 스키마로 못 막는 모델이라도, 저장 전에 후보 집합에 없는 `keywordId`는 **조용히 폐기**한다(오류 아님).
- 빈 `selected`는 정상(선택 0개 COMPLETED).

## 판정 후 처리

```text
selected = [s for s in result.selected if s.keywordId in candidate_ids]
```

- 중복 `keywordId`는 하나로 접는다(최댓값 confidence 유지).
- confidence는 `ai.context_keyword.confidence`(NUMERIC(4,3))로 저장한다.

## 검증 (테스트 C, 2단계)

판정 모델은 계약상 **미확정**(GMS: GPT-5.x / Gemini / Claude). 테스트 C가 프롬프트 검증 + 판정 모델 선택을 겸한다.

**C-1 프롬프트 안정화** (모델 1개, 세션 Claude 무방 — 프롬프트 결함은 모델 무관):
- 스키마 위반: 후보 밖 id 반환 시 폐기되어 크래시 없이 처리되는가
- 과잉 선택: 근거 약한데 3개 초과로 고르는 사례
- 과소 선택: 명백히 맞는데 0개인 사례
- confidence 분포가 근거 강도와 대체로 일치하는가

**C-2 모델 비교** (확정 프롬프트, **GMS 경로**, 경량 tier 우선):
- ①스키마 준수율 ②선택 개수 분포 ③confidence 변별력 ④응답 지연 ⑤토큰·크레딧
- → 판정 모델 확정, `REPORT.md`에 근거 기록. 태스크가 "후보에서 고르기"라 경량 모델로 충분한지 먼저 확인.
