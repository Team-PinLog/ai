# PinLog AI 파트 문서

FastAPI AI 서버의 **설계·결정·구현 기록**입니다. 공용 계약의 단일 원본은 `Team-PinLog/docs`의 `static/05_AI_설계.md`이며, 여기서는 계약을 참조만 하고 구현 방법을 다룹니다.

모든 문서가 공유하는 전제는 **Context 불변성**입니다(계약 §4.2):

```text
동일한 context_id는 항상 동일한 Context 본문을 의미한다.
```

수정은 구 Context 삭제와 신 Context 생성의 조합이며 새 `context_id`를 받습니다. 따라서 본문 세대를 구분하는 버전 개념이 없고, 수정 경합은 삭제 경합 방어로 흡수됩니다.

## 구역

| 구역 | 내용 |
|---|---|
| [`spec/`](spec/) | 설계·구현 명세 — "무엇을 만들 것인가" |
| [`proposals/`](proposals/) | 제안·결정(P 번호) + 미결 — "왜 그렇게 정했나" |
| [`implements/`](implements/) | 구현 리포트 — "어떻게 만들었나" |
| [`troubleshooting/`](troubleshooting/) | 문제 해결 |
| [`WORKLOG.md`](WORKLOG.md) | 시간순 작업 로그 |

## spec — 읽는 순서 (구현 예정 명세)

1. [architecture.md](spec/architecture.md) — 어디에 무엇이 있는지 (모듈·계층·DB 세션 경계, 구조도)
2. [context-processing.md](spec/context-processing.md) — 주 파이프라인 `POST /internal/v1/context/process`
3. [state-machine.md](spec/state-machine.md) · [deletion-race-control.md](spec/deletion-race-control.md) · [partial-resume.md](spec/partial-resume.md) — 정합성 3종
4. [keyword-preset.md](spec/keyword-preset.md) · [personal-search.md](spec/personal-search.md) · [model-profile.md](spec/model-profile.md) — 기능별 상세
5. [failure-recovery.md](spec/failure-recovery.md) · [integration-tests.md](spec/integration-tests.md) — 운영과 검증

> 구현 명세와 계약이 어긋나면 계약이 우선하며, 계약 변경은 이 레포가 아니라 docs 레포에서 합니다.
