# 작업 리포트 — Keyword 매칭 평가 (요약·포인터)

- **날짜**: 2026-07-23
- **관련 PR**: [ai#3](https://github.com/Team-PinLog/ai/pull/3) — 하네스 + A/B/C 실행 + 판정 모델 확정
- **상세 원본**: `tools/keyword_eval/REPORT.md` (수치·비교표 원본, ai#3로 병합됨)

> 이 문서는 **요약과 포인터**다. 수치 원본·하네스 코드는 `tools/keyword_eval/`가 소유하며, 중복 관리를 피하려고 여기서는 결론과 이 레포 문서와의 연결만 남긴다. **A/B/C 완료 — 판정 모델 `gemini-2.5-flash` 확정(M4 종결).**

## 목표

프리셋 27개([reports/2026-07-23-keyword-preset-seed.md](2026-07-23-keyword-preset-seed.md))가 실제 임베딩·판정에서 건전한지, FastAPI·DB 없이 **선행 검증**한다. E(구현) 전에 해야 프리셋 결함 발견 시 재적재·재분류 비용을 피한다. 특히 **테스트 C가 확정하는 판정 프롬프트가 `/context/process`에 그대로 투입**되므로 구현 일부를 미리 끝내는 셈이다.

## 하네스

`tools/keyword_eval/` — 팀이 실제 샘플로 재실행할 수 있게 커밋. `embed.py`(GMS 임베딩, 디스크 캐시), `samples.yaml`(임시 맥락 35개, self-reference 편향 → 경향 해석), `test_a/b/c`, `prompts/keyword_judgment.md`, `REPORT.md`.

## 결과 (A/B/C)

- **A 자기 중복**: cosine ≥ 0.9 병합 후보 **0건**. 프리셋 독립성 OK → 병합·삭제 불필요.
- **B 커버리지**(K=10, floor 0.30): 미매칭율 2.9%, 쏠림 max-share 10%·Gini 0.286, 사각지대 0.
- **C-1 프롬프트 안정화**(gpt-5-mini): 스키마 위반·파싱 실패·과잉 선택 각 0. 판정이 임베딩 후보 노이즈를 교정(`WITH_FAMILY`→`WITH_PARTNER`). **부대시설 제외 규칙 추가**, **하한 0.30 유지** 확정.
- **C-2 판정 모델 비교**: 확정 프롬프트로 3사 4모델(gpt-5-mini/nano, claude-haiku-4-5, gemini-2.5-flash) GMS 경로 실행. 정확도(스키마·선택 분포)는 4모델 사실상 동일 → 경량 tier로 충분. **`gemini-2.5-flash`(thinkingBudget=0) 확정** — 최속(1.12s)·최소 토큰(25314). gpt-5-nano 탈락(최장 지연·최다 토큰). confidence는 전 모델 변별력 낮음(랭킹 신호 미사용). Gemini는 function-calling이 malformed → `responseSchema`로 호출.

→ 확정 사항은 [P26](../proposals/P26-keyword-preset-judgment.md)에 반영(M4 종결).

## 남은 것

- 팀 실제 샘플(프리셋 안 보고 작성)로 B/C 재측정 → Recall·트리키 케이스 유효 검증(현 샘플은 self-reference 편향).
- GMS 모델별 크레딧 단가표로 토큰→비용 확정([spec/cost-estimate.md](../spec/cost-estimate.md) §4 공식에 대입).

## 관련

- [P26 프리셋·후보 하한·판정 프롬프트](../proposals/P26-keyword-preset-judgment.md)
- [preset seed 리포트](2026-07-23-keyword-preset-seed.md)
- 구현 계약: [`keyword-preset.md`](../spec/keyword-preset.md), [`model-profile.md`](../spec/model-profile.md)
