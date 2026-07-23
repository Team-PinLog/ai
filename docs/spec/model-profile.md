> 현재 코드가 없는 구현 예정 명세입니다.
> 공용 계약은 Team-PinLog/docs의 `static/05_AI_설계.md`를 따릅니다.

# 모델 설정과 Embedding Profile

근거 계약: `static/05_AI_설계.md` §7.1 모델과 Profile, §7.2 Embedding 저장, §7.3 Profile 일치

## 1. MVP 모델

| 항목 | 값 |
|---|---|
| Model | `text-embedding-3-small` |
| Dimension | 1536 |
| Distance | cosine |
| 저장 | PostgreSQL + pgvector |

Profile 문자열은 이 세 가지를 하나의 식별자로 묶습니다.

```text
openai-text-embedding-3-small-1536-cosine-v1
```

Context Embedding과 Keyword Preset Embedding은 **같은 Profile**을 사용합니다.
서로 다른 Profile의 벡터는 비교 자체가 성립하지 않습니다.

## 2. 단일 주입

> Profile을 Spring과 FastAPI에 각각 하드코딩하지 않습니다.
> 배포 환경의 **단일 설정**에서 주입합니다.

### 2.1 FastAPI 쪽 규칙

- Profile 문자열은 환경변수 `PINLOG_EMBEDDING_PROFILE` 하나로 주입받습니다.
- 읽는 지점은 `app/core/config.py` 한 곳입니다. 다른 모듈은 설정 객체를 통해서만 접근합니다.
- 코드 어디에도 `"openai-text-embedding-3-small-1536-cosine-v1"` 리터럴을 두지 않습니다.
  테스트 fixture에서도 상수를 재선언하지 않고 설정 객체를 사용합니다.
- 모델명·차원·거리 기준도 각각 설정값으로 주입받되, Profile 문자열과 **불일치하면 기동을
  실패**시킵니다. 두 개의 진실이 생기는 것을 막기 위한 기동 시 검사입니다.

```python
class Settings(BaseSettings):
    embedding_profile: str          # PINLOG_EMBEDDING_PROFILE
    embedding_model: str            # PINLOG_EMBEDDING_MODEL
    embedding_dimension: int        # PINLOG_EMBEDDING_DIMENSION
    embedding_distance: str         # PINLOG_EMBEDDING_DISTANCE
```

- 기본값을 코드에 넣지 않습니다. 값이 없으면 기동 실패입니다.
  기본값이 있으면 배포 설정 누락이 조용한 Profile 불일치로 나타납니다.

### 2.2 Spring과의 동기화

Spring은 `POST /internal/v1/search` 요청에 `embeddingProfile`을 실어 보냅니다.
이 값은 Spring이 자신의 설정에서 읽은 값이며, 같은 배포 설정에서 나온 값이어야 합니다.

FastAPI는 이 요청값을 **자신의 설정값과 대조**합니다. 대조는 Profile이 두 곳에서
따로 관리되고 있지 않은지 확인하는 런타임 검증입니다.

## 3. Profile 불일치 시 동작

Profile은 세 지점에서 비교됩니다. 각각 동작이 다릅니다.

### 3.1 요청 Profile ≠ 서버 설정 Profile (검색)

배포 설정이 어긋난 상태이며, 어떤 벡터를 만들어도 저장된 벡터와 비교할 수 없습니다.

- 질의 Embedding을 호출하지 않습니다. 비용만 쓰고 쓸 수 없는 벡터가 됩니다.
- 빈 결과를 반환하지 않습니다. 빈 결과는 "일치하는 기록이 없음"으로 보여 설정 오류를 숨깁니다.
- `422`로 거부하고 양쪽 Profile 값을 로그에 남깁니다. Spring은 이를 AI 검색 실패로 처리하되,
  기본 기능(저장·조회·발행)에는 영향을 주지 않습니다.

### 3.2 Context Embedding Profile ≠ 현재 Profile (재사용 판정)

저장된 Embedding이 이전 Profile로 만들어진 경우입니다.

- 재사용하지 않습니다([partial-resume.md](partial-resume.md) §2 조건 2).
- `embedding_status`가 PENDING이면 새 Profile로 다시 생성합니다.
  Profile 변경은 재생성 대상 판별의 기준입니다.
- `embedding_status`가 COMPLETED인데 Profile이 다르면 FastAPI가 스스로 재생성하지 않습니다.
  COMPLETED에서 PROCESSING으로 전이할 권한이 없기 때문입니다.
  이 경우 Keyword 판정을 수행할 수 없으므로 §3.3과 동일하게 처리합니다.
  Context는 불변이고 **기존 State를 PENDING으로 되돌리는 전이도 존재하지 않으므로**
  (계약 §6.3), Profile 전환 배포에서 기존 Context를 어떻게 재처리할지는 Spring의 운영 결정이며
  ai 레포의 판단 범위 밖입니다. FastAPI는 어떤 경우에도 스스로 재처리를 시작하지 않습니다.

### 3.3 Context Embedding Profile ≠ Preset Profile (Keyword 판정)

- 판정을 중단합니다(검증 시나리오 13).
- LLM을 호출하지 않고 `ai.context_keyword`에 아무것도 쓰지 않습니다.
- "Keyword 없음"으로 COMPLETED 처리하지 **않습니다.** 판정 불가와 판정 결과 0개는 다릅니다.
- 영구 오류로 분류합니다([failure-recovery.md](failure-recovery.md)).

### 3.4 검색 Query에서의 Profile

검색은 `embedding_profile` 일치 조건을 SQL 필터로 유지합니다
([personal-search.md](personal-search.md) §4). 애플리케이션 레벨 검사가 있더라도
Query 조건을 빼지 않습니다. Profile 전환 중에는 두 Profile의 벡터가 테이블에 공존할 수 있고,
차원이 다른 벡터에 cosine 연산을 걸면 계산이 성립하지 않기 때문입니다.

## 4. Profile 문자열 변경 기준

Profile은 "이 벡터를 저 벡터와 비교해도 되는가"를 판별하는 값입니다.
따라서 **벡터 공간을 바꾸는 모든 변경**에서 버전 접미사를 올립니다.

올려야 하는 경우:

- 모델 교체
- 차원 변경
- 거리 기준 변경
- Embedding 입력 텍스트 구성 방식 변경
  (예: `placeMeta`를 본문에 결합하기 시작하거나 중단하는 경우)

올리지 않는 경우:

- 프롬프트나 LLM 모델 변경 — Keyword 판정에만 영향을 주며 벡터 공간과 무관합니다.
  이 값은 `ai.context_keyword_analysis.model_profile`에 별도로 기록합니다.
- Preset의 `display_name` 변경 — 표시 문구는 벡터에 영향을 주지 않습니다.
  단 `description`·`examples` 변경은 Preset Embedding 재생성 대상입니다.

Profile 접미사를 올리면 기존 Context Embedding과 Preset Embedding이 모두 재생성 대상이 됩니다.
재생성 범위와 시점은 배포 운영 결정이며, 그 결정과 실행의 주체는 Spring입니다.
FastAPI는 요청받은 Context만 처리합니다.

Profile 문자열의 버전 표기는 모델·전처리 구성을 식별하는 값이며 **Context와 무관합니다**
(계약 §7.1). Preset의 `version`도 프리셋 목록의 개정 번호일 뿐이며 마찬가지로 Context와
무관합니다. Context 본문에는 버전 개념이 없습니다.

## 5. 차원 검증

Embedding API 응답 벡터의 길이가 `embedding_dimension`과 다르면 저장하지 않습니다.
pgvector 컬럼은 고정 차원이므로 INSERT 시 DB 오류로 드러나지만, 그보다 앞선 지점에서
영구 오류로 분류해 재시도 낭비를 막습니다.
