# WORKLOG — AI 파트

시간순 작업 로그입니다. 유형별 폴더(spec/proposals/implements/troubleshooting) 분산으로 인한 "내 작업 추적" 비용을 시간축 인덱스로 상쇄합니다. **이후 작업마다 한 줄씩 추가**합니다.

| 날짜 | 작업 | 관련 문서 |
|---|---|---|
| 2026-07-23 | AI 공용 설계를 docs `static/05` 단일 원본으로 확립 (docs#2) | [proposals](proposals/README.md) (P16) |
| 2026-07-23 | FastAPI AI 서버 구현 명세 작성 + version→deletion race 리네임 (ai#1) | [spec/](spec/) |
| 2026-07-23 | Keyword Preset seed 27개 (ai#2) | [implements](implements/2026-07-23-keyword-preset-seed.md), [spec/keyword-preset.md](spec/keyword-preset.md) |
| 2026-07-23 | architecture 구조도(Mermaid) 4종 (ai#4) | [implements](implements/2026-07-23-architecture-diagrams.md), [spec/architecture.md](spec/architecture.md) |
| 2026-07-23 | Keyword 매칭 평가 A/B/C-1 — 하한 0.30·프롬프트 확정 (test/keyword-matching-eval) | [implements](implements/2026-07-23-keyword-matching-eval.md), [P26](proposals/P26-keyword-preset-judgment.md) |
| 2026-07-23 | 작업 기록 신설 + 문서 재구조화(spec/proposals/implements/troubleshooting + WORKLOG, ADR→P) (ai#4) | 이 트리 전체 |
| 2026-07-23 | eval C-2 3사 모델 비교 완료 — 판정 모델 `gemini-2.5-flash`(thinkingBudget=0) 확정, M4 종결 (ai#3) | [implements](implements/2026-07-23-keyword-matching-eval.md), [P26](proposals/P26-keyword-preset-judgment.md) |
| 2026-07-23 | FastAPI 서버 scaffold + 개인 검색(`/search`) + Preset 부트스트랩 + 운영 비용 추정 (ai#5) | [spec/personal-search.md](spec/personal-search.md), [spec/cost-estimate.md](spec/cost-estimate.md) |
| 2026-07-23 | `/context/process` 처리 파이프라인 + 상태머신(부분 재개·저장 불변식·gemini-2.5-flash 판정) (ai#6) | [spec/context-processing.md](spec/context-processing.md), [spec/state-machine.md](spec/state-machine.md) |
