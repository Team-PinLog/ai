# Keyword Preset seed 초안 — keyword_preset.yaml 에 프리셋 27개를 작성했다

- **상태**: 완료
- **날짜**: 2026-07-23
- **PR**: [ai#2](https://github.com/Team-PinLog/ai/pull/2) — `feat: Keyword Preset seed 초안 (keyword_preset.yaml)`
- **주요 커밋**: `de6e995` (merge `f69883f`)
- **브랜치**: `feat/keyword-preset-seed` ← `main`
- **산출**: `data/keyword_preset.yaml` (27개)

## 목표

Keyword Preset 은 이 시점까지 계약·명세 문서로만 존재했고 실제 데이터가 없었다. 임베딩·분류·검색 실험과 이후 부트스트랩 적재가 가능하도록 프리셋 시드 초안을 만든다. 적재 계약은 `docs/keyword-preset.md` 가 정의하고 로더 코드는 구현 단계(E) 소관이므로, 이 작업의 범위는 데이터 초안까지다.

## 산출물

`data/keyword_preset.yaml` — 프리셋 27개.

| 범주 | 개수 | id 블록 | 예 |
|---|---|---|---|
| COMPANION | 6 | 1xx | WITH_PARTNER, WITH_FAMILY, WITH_COLLEAGUES(PRIVATE_ONLY) |
| ACTIVITY | 8 | 2xx | DATE_COURSE, STUDY_WORK, QUICK_STOP |
| ATMOSPHERE | 7 | 3xx | QUIET, COZY, SPACIOUS |
| SITUATION | 6 | 4xx | CELEBRATION, ANNIVERSARY(PRIVATE_ONLY) |

필드 규칙은 다음과 같이 확정했다.

- `description`: 20~40자 한 문장. 사전적 정의가 아니라 의미 범위를 적는다. 동의어와 인접 개념을 포함한다.
- `examples`: 구어체 3~5개. 키워드 단어가 등장하지 않는 문장을 최소 1개 포함한다. 문어체는 쓰지 않는다.
- `visibility`: 기본은 `PUBLIC` 이다. 개인 정보를 유추할 소지가 있으면 `PRIVATE_ONLY` 로 지정하고 사유를 주석으로 남긴다. `BLOCKED` 항목은 없다.
- `id`: 명시적으로 고정한다. 임베딩 값은 YAML 에 포함하지 않는다. 부트스트랩이 임베딩을 생성한 뒤 INSERT 한다.

## 검증

Python 점검 스크립트로 다음을 확인했다.

- [x] 총 27개이고 범주 배분(6/8/7/6)이 계획과 일치한다.
- [x] `id` 와 `code` 가 각각 유일하다.
- [x] `visibility` 값이 유효하다. PRIVATE_ONLY 는 2건(WITH_COLLEAGUES·ANNIVERSARY)이다.
- [x] 모든 항목의 `examples` 에 키워드 단어가 없는 문장이 1개 이상 있다.

## 후속 검증(별도 트랙)

이 시드는 문서 규칙만 만족한 상태였다. 실제 임베딩·매칭 품질은 별도 평가 트랙에서 측정했고, 결과적으로 보정이 필요 없음을 확인했다. 결과는 [2026-07-23-keyword-matching-eval.md](2026-07-23-keyword-matching-eval.md)와 [P26](../proposals/P26-keyword-preset-judgment.md)에 있다.

## 관련

- 적재 계약: [`keyword-preset.md`](../spec/keyword-preset.md)
- 스키마: [back#3](https://github.com/Team-PinLog/back/pull/3) `V100__ai_tables.sql`의 `ai.keyword_preset`
