# P26: Keyword 프리셋 구성·후보 하한·판정 프롬프트

- **상태**: Accepted
- **날짜**: 2026-07-23
- **관련 PR/커밋**: [ai#2](https://github.com/Team-PinLog/ai/pull/2) (`de6e995` preset seed), [ai#3](https://github.com/Team-PinLog/ai/pull/3) eval 하네스(`test/keyword-matching-eval`, C-2 판정 모델 확정)
- **주도(Driver)**: AI 파트
- **근거 리포트**: [2026-07-23-keyword-matching-eval.md](../implements/2026-07-23-keyword-matching-eval.md)

## 맥락

Context 본문에 붙일 Keyword 는 고정 프리셋에서 고른다. 세 가지를 정해야 했다. (1) 프리셋 구성(개수·범주·공개 등급), (2) 임베딩 후보 검색 파라미터(TOP-K 와 유사도 하한), (3) LLM 판정 프롬프트와 출력 스키마. 이 결정들은 문서상 추정으로 정하지 않고 **eval 하네스로 실측**해 보정했다.

## 결정

### 프리셋 구성 (27개)
- 범주는 `COMPANION`(6) / `ACTIVITY`(8) / `ATMOSPHERE`(7) / `SITUATION`(6)의 네 가지다. **지역·장소 범주는 제외**한다.
- 각 프리셋의 필드는 `id`(명시적 고정) · `code` · `display_name` · `category` · `description`(의미 범위) · `examples` · `visibility`다. `examples`는 구어체 문장 3~5개이고, 그중 키워드 단어가 등장하지 않는 문장을 1개 이상 포함한다.
- 공개 등급은 `PUBLIC` / `PRIVATE_ONLY` / `BLOCKED`의 세 단계다. **MVP 에는 BLOCKED 등급인 프리셋이 없다.** 개인을 유추할 소지가 있는 `WITH_COLLEAGUES`·`ANNIVERSARY`는 `PRIVATE_ONLY`로 둔다.

### 후보 검색
- `KEYWORD_CANDIDATE_TOP_K = 10`으로 하고, **유사도 하한은 0.30**으로 한다.
- 후보가 0개면 LLM 을 호출하지 않고 선택 0개로 정상 완료 처리한다.

### LLM 판정
- 출력은 구조화 형식 `{selected: [{keywordId, confidence}]}`를 강제한다. `keywordId`는 **후보로 전달한 id 의 enum 으로 제약**하고, 후보에 없는 id 가 반환되면 오류로 처리하지 않고 폐기한다.
- 프롬프트에 **부대시설/서비스 언급 제외 규칙**을 포함한다. 예를 들어 "주차가 넓어서"라는 언급만으로 `SPACIOUS`를 선택하는 것을 금지한다.

## 근거 (eval 실측)

- **프리셋 간 독립성이 확인됐다.** 테스트 A 에서 cosine 유사도 0.9 이상으로 겹쳐 병합 대상이 되는 프리셋 쌍은 **0건**이었다. 가장 가까운 쌍인 `WITH_PARTNER`와 `DATE_COURSE`도 유사도 0.578 로 서로 별개 개념으로 구분됐다. 따라서 프리셋 병합이나 삭제는 필요 없다.
- **커버리지가 건전하다.** 테스트 B 에서 어느 프리셋에도 매칭되지 않은 샘플 비율(미매칭율)은 2.9%였다. 특정 프리셋으로의 쏠림은 최대 점유율 10%, Gini 계수 0.286 으로 낮았고, 어떤 샘플도 받지 못하는 사각지대 프리셋은 0개였다. top-1 유사도 분포를 보면 실제로 맞는 매칭은 대체로 0.45 이상에 있고, 무관한 입력은 0.30 부근에서 갈렸다.
- **하한은 0.30 을 유지한다.** 하한을 0.35 로 올리면 "시험기간에 살다시피" 같은 간접 표현(정답은 STUDY_WORK)이 유실된다. 이런 표현의 임베딩 유사도가 0.30~0.35 구간에 있기 때문이다. 하한을 올려 정밀도를 얻는 대신 **프롬프트로 정밀도를 보완**하는 쪽을 택했다.
- **판정 계층이 임베딩의 오류를 교정한다.** 테스트 C-1 에서 "여자친구랑…"이라는 입력의 top-1 후보는 `WITH_FAMILY`(유사도 0.485)였다. LLM 판정이 이를 기각하고 `WITH_PARTNER`를 선택했다. 스키마 위반, 파싱 실패, 셋을 넘는 과잉 선택(>3)은 각 0건이었다.
- **판정 모델을 테스트 C-2 로 확정했다.** 확정 프롬프트로 3사 4모델을 35샘플로 비교했다. 스키마 준수와 선택 분포로 본 정확도는 4모델이 사실상 동일했다. 태스크가 "주어진 후보에서 고르기"라서 경량 tier 모델로도 충분하기 때문이다. `gemini-2.5-flash`(thinkingBudget=0)가 최단 지연(1.12s)과 최소 토큰(25314)으로 가장 우수했다. gpt-5-nano 는 지연이 가장 길고 토큰을 가장 많이 써서 탈락했다. confidence 값은 모든 모델에서 변별력이 낮았다.

## 채택하지 않은 대안

- **유사도 하한 0.35 이상**: 정밀도는 오르지만 간접 표현의 재현율이 급락한다. 프롬프트를 정밀화하는 쪽이 더 나은 절충점이다.
- **confidence 를 강한 랭킹 신호로 사용**: 판정 모델(gpt-5-mini)의 confidence 가 평균 0.94 로 과신 경향을 보였고 변별력이 낮아, MVP 에서 신뢰할 신호로 부적합하다.

## 영향

- 확정 프롬프트는 eval 브랜치의 `tools/keyword_eval/prompts/keyword_judgment.md`에 있다. E 구현의 `/context/process` 판정부에 그대로 투입한다.
- 후보 검색 하한과 TOP-K 는 `/search` 및 키워드 후보 생성에 반영한다.
- **판정 모델은 `gemini-2.5-flash`(thinkingBudget=0)로 확정했다.** 테스트 C-2 의 5개 지표(스키마·선택 분포·confidence·지연·토큰) 기준으로 최적이었다. 차선은 `gpt-5-mini`(안정적이지만 reasoning 때문에 느림)와 `claude-haiku-4-5`(빠르지만 입력 토큰이 큼)다. 호출 방식은 responseSchema(네이티브 구조화 출력)를 쓴다. function-calling 방식은 2.5-flash 에서 형식이 깨진 응답(malformed)이 나왔기 때문이다. E 구현 `/context/process` 판정부에 이 모델과 확정 프롬프트를 투입한다(ai#6에서 반영).

## 검증

- eval 하네스 A/B/C-1 을 실행했고 근거 수치 원본은 `REPORT.md`에 있다. 현재 샘플은 프리셋을 알고 있는 작성자가 만든 것이라 self-reference 편향이 있으므로 경향 해석에 한정한다. 팀이 프리셋을 보지 않고 쓴 샘플로 교체해 재측정하면 유효성이 올라간다.
