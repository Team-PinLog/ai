# P26: Keyword 프리셋 구성·후보 하한·판정 프롬프트

- **상태**: Accepted
- **날짜**: 2026-07-23
- **관련 PR/커밋**: [ai#2](https://github.com/Team-PinLog/ai/pull/2) (`de6e995` preset seed), [ai#3](https://github.com/Team-PinLog/ai/pull/3) eval 하네스(`test/keyword-matching-eval`, C-2 판정 모델 확정)
- **주도(Driver)**: AI 파트
- **근거 리포트**: [reports/2026-07-23-keyword-matching-eval.md](../implements/2026-07-23-keyword-matching-eval.md)

## 맥락

Context 본문에 붙일 Keyword를 고정 프리셋에서 고른다. 세 가지를 정해야 했다. (1) 프리셋 구성(개수·범주·공개 등급), (2) 임베딩 후보 검색 파라미터(TOP-K·유사도 하한), (3) LLM 판정 프롬프트와 출력 스키마. 이 결정들은 문서상 추정이 아니라 **실측(eval 하네스)** 으로 보정했다.

## 결정

### 프리셋 구성 (27개)
- 범주: `COMPANION`(6) / `ACTIVITY`(8) / `ATMOSPHERE`(7) / `SITUATION`(6). **지역·장소 범주 제외**.
- 필드: `id`(명시적 고정) · `code` · `display_name` · `category` · `description`(의미 범위) · `examples`(구어체 3~5, 키워드 단어 없는 문장 ≥1) · `visibility`.
- 공개 등급: `PUBLIC` / `PRIVATE_ONLY` / `BLOCKED`. **MVP에 BLOCKED 없음.** 개인 유추 소지가 있는 `WITH_COLLEAGUES`·`ANNIVERSARY`는 `PRIVATE_ONLY`.

### 후보 검색
- `KEYWORD_CANDIDATE_TOP_K = 10`, **유사도 하한 0.30**.
- 후보 0개면 LLM 미호출·선택 0개로 정상 완료.

### LLM 판정
- 구조화 출력 `{selected: [{keywordId, confidence}]}`. `keywordId`는 **후보 id enum으로 제약**, 후보 밖 id는 조용히 폐기.
- 프롬프트에 **부대시설/서비스 언급 제외 규칙** 포함(예: "주차가 넓어서"만으로 `SPACIOUS` 선택 금지).

## 근거 (eval 실측)

- **프리셋 독립성 OK** — 테스트 A: cosine ≥ 0.9 병합 후보 **0건**. 최근접도 `WITH_PARTNER↔DATE_COURSE` 0.578로 별개. → 병합·삭제 불필요.
- **커버리지 건전** — 테스트 B: 미매칭율 2.9%, 쏠림 max-share 10%·Gini 0.286, 사각지대 0. top-1 분포상 진짜 매칭은 대체로 0.45+, 무관 입력은 0.30 부근에서 갈림.
- **하한 0.30 유지** — 0.35로 올리면 "시험기간에 살다시피"(간접 표현, STUDY_WORK) 같은 약한 임베딩(0.30~0.35)이 유실된다. 하한을 올리는 대신 **프롬프트로 정밀도를 보완**한다.
- **판정 계층이 임베딩 노이즈를 교정** — 테스트 C-1: "여자친구랑…"에서 top-1 후보가 `WITH_FAMILY`(0.485)였으나 판정이 기각하고 `WITH_PARTNER` 선택. 스키마 위반·파싱 실패·과잉 선택(>3) 각 0건.
- **판정 모델 확정 — 테스트 C-2**: 확정 프롬프트로 3사 4모델 비교(35샘플). 정확도(스키마·선택 분포)는 4모델 사실상 동일 → 태스크가 "후보에서 고르기"라 경량 tier로 충분. `gemini-2.5-flash`(thinkingBudget=0)가 최속(1.12s)·최소 토큰(25314)으로 최우수. gpt-5-nano 탈락(최장 지연·최다 토큰). confidence는 전 모델 변별력 낮음.

## 버린 대안

- **유사도 하한 0.35+**: 정밀도는 오르나 간접 표현 재현율이 급락. 프롬프트 정밀화가 더 나은 지점.
- **confidence를 강한 랭킹 신호로 사용**: 판정 모델(gpt-5-mini)의 confidence가 과신 경향(mean 0.94, 낮은 변별력)이라 MVP에서 신뢰 신호로 부적합.

## 영향

- 확정 프롬프트는 eval 브랜치 `tools/keyword_eval/prompts/keyword_judgment.md`. E 구현의 `/context/process` 판정부에 그대로 투입한다.
- 후보 검색 하한·TOP-K는 `/search` 및 키워드 후보 생성에 반영.
- **판정 모델 확정: `gemini-2.5-flash`(thinkingBudget=0)** — 테스트 C-2 5개 지표(스키마·선택 분포·confidence·지연·토큰) 기준 최적. 차선은 `gpt-5-mini`(안정하나 reasoning으로 느림), `claude-haiku-4-5`(빠르나 입력 토큰 큼). 호출 방식은 responseSchema(네이티브 구조화 출력) — function-calling은 2.5-flash에서 malformed. E 구현 `/context/process` 판정부에 이 모델 + 확정 프롬프트를 투입한다(ai#6에서 반영).

## 검증

- eval 하네스 A/B/C-1 실행 + `REPORT.md`(근거 수치 원본). 팀이 프리셋 안 보고 쓴 샘플로 교체 시 재측정하면 유효성이 올라간다(현 샘플은 self-reference 편향, 경향 해석).
