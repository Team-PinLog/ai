# 작업 리포트 — Keyword 매칭 평가 (요약·포인터)

- **날짜**: 2026-07-23 (진행 중 — C-2 남음)
- **브랜치**: `test/keyword-matching-eval` ← `main` (별도 세션 진행 중)
- **커밋**: `3279497`(하네스+프롬프트 초안) → `be29bf5`(C 2단계 확장) → `0d23118`(A/B/C-1 실행+REPORT)
- **상세 원본**: 해당 브랜치 `tools/keyword_eval/REPORT.md` (병합 시 이 레포로 들어옴)

> 이 문서는 **요약과 포인터**다. 수치 원본·하네스 코드는 `test/keyword-matching-eval` 브랜치가 소유하며, 이 트랙은 다른 세션에서 계속 진행 중이다. 중복 관리를 피하려고 여기서는 결론과 이 레포 문서와의 연결만 남긴다.

## 목표

프리셋 27개([reports/2026-07-23-keyword-preset-seed.md](2026-07-23-keyword-preset-seed.md))가 실제 임베딩·판정에서 건전한지, FastAPI·DB 없이 **선행 검증**한다. E(구현) 전에 해야 프리셋 결함 발견 시 재적재·재분류 비용을 피한다. 특히 **테스트 C가 확정하는 판정 프롬프트가 `/context/process`에 그대로 투입**되므로 구현 일부를 미리 끝내는 셈이다.

## 하네스

`tools/keyword_eval/` — 팀이 실제 샘플로 재실행할 수 있게 커밋. `embed.py`(GMS 임베딩, 디스크 캐시), `samples.yaml`(임시 맥락 35개, self-reference 편향 → 경향 해석), `test_a/b/c`, `prompts/keyword_judgment.md`, `REPORT.md`.

## 결과 (A/B/C-1)

- **A 자기 중복**: cosine ≥ 0.9 병합 후보 **0건**. 프리셋 독립성 OK → 병합·삭제 불필요.
- **B 커버리지**(K=10, floor 0.30): 미매칭율 2.9%, 쏠림 max-share 10%·Gini 0.286, 사각지대 0.
- **C-1 프롬프트 안정화**(gpt-5-mini): 스키마 위반·파싱 실패·과잉 선택 각 0. 판정이 임베딩 후보 노이즈를 교정(`WITH_FAMILY`→`WITH_PARTNER`). **부대시설 제외 규칙 추가**, **하한 0.30 유지** 확정.

→ 확정 사항은 [ADR-004](../decisions/ADR-004-keyword-preset-judgment.md)에 반영.

## 남은 것 (C-2, 진행 중)

- **판정 모델 비교** — 확정 프롬프트로 gpt-5-mini / gpt-5-nano / gemini-flash 등을 GMS 경로로 실행, 5개 지표(스키마 준수·선택 개수·confidence 변별력·지연·크레딧)로 판정 모델 최종 확정.
- Gemini는 네이티브 `generateContent` 포맷이라 어댑터 필요(현 provider는 GPT 계열).
- 팀 실제 샘플로 B/C 재측정 시 Recall이 유효해짐(현 샘플은 편향).

## 관련

- [ADR-004 프리셋·후보 하한·판정 프롬프트](../decisions/ADR-004-keyword-preset-judgment.md)
- [preset seed 리포트](2026-07-23-keyword-preset-seed.md)
- 구현 계약: [`keyword-preset.md`](../keyword-preset.md), [`model-profile.md`](../model-profile.md)
