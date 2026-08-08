# Keyword 매칭 평가 — 프리셋 27개 검증을 통과하고 판정 모델을 gemini-2.5-flash 로 확정했다 (요약·포인터)

- **상태**: 완료
- **날짜**: 2026-07-23
- **관련 PR**: [ai#3](https://github.com/Team-PinLog/ai/pull/3) — 하네스 + A/B/C 실행 + 판정 모델 확정
- **상세 원본**: `tools/keyword_eval/REPORT.md` (수치·비교표 원본, ai#3로 병합됨)

> 이 문서는 요약과 포인터다. 수치 원본과 하네스 코드는 `tools/keyword_eval/` 이 소유한다. 같은 내용을 두 곳에서 관리하지 않기 위해 여기에는 결론과 이 레포 문서와의 연결만 남긴다. 테스트 A/B/C 를 모두 완료했고, 판정 모델을 `gemini-2.5-flash` 로 확정했다(미결 사항 M4 종결).

## 목표

프리셋 27개([2026-07-23-keyword-preset-seed.md](2026-07-23-keyword-preset-seed.md))가 실제 임베딩·판정에서 건전하게 동작하는지를 FastAPI 서버·DB 없이 미리 검증한다. 구현(E) 전에 검증해야 하는 이유는, 프리셋 결함이 구현 후에 발견되면 재적재·재분류 비용이 들기 때문이다. 특히 테스트 C 가 확정하는 판정 프롬프트는 `/context/process` 에 그대로 투입되므로, 이 검증은 구현의 일부를 미리 끝내는 작업이기도 하다.

## 하네스

`tools/keyword_eval/` 에 커밋했다. 팀이 실제 샘플로 재실행할 수 있게 하기 위해서다. 구성은 다음과 같다.

- `embed.py` — GMS 임베딩 호출. 결과를 디스크에 캐시한다.
- `samples.yaml` — 임시 맥락 35개. 프리셋을 아는 작성자가 만든 샘플이라 self-reference 편향이 있으므로, 수치는 절대값이 아니라 경향으로 해석한다.
- `test_a/b/c` — 테스트 3종 실행 코드.
- `prompts/keyword_judgment.md` — 판정 프롬프트.
- `REPORT.md` — 수치 원본.

## 결과

### A — 프리셋 자기 중복 검사

프리셋끼리 임베딩이 지나치게 비슷해 병합해야 하는 쌍이 있는지 검사했다. cosine 유사도 0.9 이상인 병합 후보는 0건이었다. 프리셋들이 서로 독립적이므로 병합이나 삭제는 필요 없다.

### B — 커버리지

후보 상한 K=10, 유사도 하한(floor) 0.30 조건으로 쟀다. 어느 프리셋에도 매칭되지 않은 샘플의 비율(미매칭율)은 2.9%였다. 특정 프리셋으로의 쏠림도 크지 않았다. 가장 많이 매칭된 프리셋의 점유율(max-share)이 10%였고, 매칭 분포의 불균형을 나타내는 Gini 계수는 0.286이었다. 어떤 샘플에서도 후보로 등장하지 않는 프리셋(사각지대)은 0개였다.

### C-1 — 판정 프롬프트 안정화 (gpt-5-mini)

프롬프트를 반복 실행하며 다듬어, 스키마 위반·파싱 실패·과잉 선택이 각각 0건이 되도록 안정화했다. LLM 판정이 임베딩 후보의 노이즈를 교정하는 것도 확인했다(임베딩이 올린 `WITH_FAMILY` 후보를 판정이 `WITH_PARTNER` 로 바로잡았다). 이 과정에서 부대시설 제외 규칙을 프롬프트에 추가했고, 후보 하한 0.30 은 유지하기로 확정했다.

### C-2 — 판정 모델 비교

확정한 프롬프트로 3사 4모델(gpt-5-mini, gpt-5-nano, claude-haiku-4-5, gemini-2.5-flash)을 GMS 경로에서 실행해 비교했다. 정확도(스키마 준수·선택 분포)는 4모델이 사실상 동일했다. 따라서 경량 tier 모델로 충분하다. 그중 가장 빠르고(1.12s) 토큰을 가장 적게 쓴(25314) `gemini-2.5-flash`(thinkingBudget=0)를 확정했다. gpt-5-nano 는 지연이 가장 길고 토큰을 가장 많이 써서 탈락했다. 모델이 반환하는 confidence 값은 모든 모델에서 변별력이 낮아 랭킹 신호로 사용하지 않는다. Gemini 는 function-calling 응답이 malformed 로 나와서, 대신 `responseSchema` 방식으로 호출한다.

확정 사항은 [P26](../proposals/P26-keyword-preset-judgment.md)에 반영했다(M4 종결).

## 남은 것

- 팀원이 프리셋을 보지 않고 작성한 실제 샘플로 B/C 를 다시 측정해야 한다. 현재 샘플은 self-reference 편향이 있어, Recall 과 트리키 케이스가 실제로 유효한지는 새 샘플로만 검증할 수 있다.
- GMS 모델별 크레딧 단가표가 나오면 토큰 사용량을 비용으로 환산해야 한다([spec/cost-estimate.md](../spec/cost-estimate.md) §4 공식에 대입).

## 관련

- [P26 프리셋·후보 하한·판정 프롬프트](../proposals/P26-keyword-preset-judgment.md)
- [preset seed 리포트](2026-07-23-keyword-preset-seed.md)
- 구현 계약: [`keyword-preset.md`](../spec/keyword-preset.md), [`model-profile.md`](../spec/model-profile.md)
