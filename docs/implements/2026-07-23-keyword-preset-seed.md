# 작업 리포트 — Keyword Preset seed 초안

- **상태**: 완료
- **날짜**: 2026-07-23
- **PR**: [ai#2](https://github.com/Team-PinLog/ai/pull/2) — `feat: Keyword Preset seed 초안 (keyword_preset.yaml)`
- **주요 커밋**: `de6e995` (merge `f69883f`)
- **브랜치**: `feat/keyword-preset-seed` ← `main`
- **산출**: `data/keyword_preset.yaml` (27개)

## 목표

Keyword Preset이 계약·명세로만 존재했다. 임베딩·분류·검색 실험과 이후 부트스트랩 적재가 가능하도록 **프리셋 시드 초안**을 만든다. 적재 계약은 `docs/keyword-preset.md`, 로더 코드는 E(구현) 소관이므로 이 작업은 **데이터 초안**까지다.

## 산출물

`data/keyword_preset.yaml` — 27개.

| 범주 | 개수 | id 블록 | 예 |
|---|---|---|---|
| COMPANION | 6 | 1xx | WITH_PARTNER, WITH_FAMILY, WITH_COLLEAGUES(PRIVATE_ONLY) |
| ACTIVITY | 8 | 2xx | DATE_COURSE, STUDY_WORK, QUICK_STOP |
| ATMOSPHERE | 7 | 3xx | QUIET, COZY, SPACIOUS |
| SITUATION | 6 | 4xx | CELEBRATION, ANNIVERSARY(PRIVATE_ONLY) |

필드 규칙(확정):
- `description`: 20~40자 한 문장, 정의가 아니라 **의미 범위**(동의어·인접 개념).
- `examples`: 3~5개 구어체, **키워드 단어가 없는 문장 최소 1개**(문어체 금지).
- `visibility`: 기본 `PUBLIC`, 개인 유추 소지 시 `PRIVATE_ONLY`(사유 주석), `BLOCKED` 없음.
- `id`: 명시적 고정. 임베딩은 YAML에 미포함(부트스트랩이 생성 후 INSERT).

## 검증

Python 점검 스크립트로 확인:
- [x] 총 27개, 범주 배분(6/8/7/6) 일치.
- [x] `id`·`code` 유일.
- [x] `visibility` 값 유효, PRIVATE_ONLY 2건(WITH_COLLEAGUES·ANNIVERSARY).
- [x] 모든 항목 `examples`에 키워드 단어 없는 문장 ≥1 포함.

## 후속 검증(별도 트랙)

이 시드는 문서 규칙만 만족한 상태였다. **실제 임베딩·매칭 품질**은 별도 평가 트랙에서 측정했고, 결과적으로 **보정 불필요**로 확인됐다 → [reports/2026-07-23-keyword-matching-eval.md](2026-07-23-keyword-matching-eval.md), [P26](../proposals/P26-keyword-preset-judgment.md).

## 관련

- 적재 계약: [`keyword-preset.md`](../spec/keyword-preset.md)
- 스키마: [back#3](https://github.com/Team-PinLog/back/pull/3) `V100__ai_tables.sql`의 `ai.keyword_preset`
