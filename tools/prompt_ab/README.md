# 판정 프롬프트 A/B 측정

`S15P11A705-219`. 결론과 수치는
[구현 리포트](../../docs/implements/2026-07-31-judge-prompt-rule.md)에 있고, 이 문서는
**어떻게 다시 돌리는가**만 적는다.

선행은 `S15P11A705-210`([τ 리포트](../../docs/implements/2026-07-31-candidate-threshold.md))이다.
후보 선정 층(유사도 임계값)으로는 오분류를 못 푼다는 것을 실측하고 끝났고, 그 §6 이
「판정 프롬프트로 옮긴다」를 후속으로 남겼다. **라벨과 데이터를 그대로 물려받는다** —
기준이 바뀌면 `-210` 과 비교가 불가능해지기 때문이다.

## 왜 τ 하네스를 그대로 못 쓰나

`tau_grid` 는 유사도 행렬을 한 번 떠 두고 **임의의 τ 를 GMS 호출 없이 재구성**한다.
프롬프트는 그렇게 못 한다 — LLM 을 실제로 불러야 답이 나온다. 그래서 이 하네스는
호출을 전제로 짰고, 그 전제가 설계 전부를 규정한다.

```
회차마다 즉시 파일로      중단되면 그 회차까지는 남는다. GMS 호출은 되돌릴 수 없다
이미 있는 회차는 건너뜀    재개가 곧 이어달리기다
후보는 matrix.json 고정    재임베딩·재시딩 없음. A·B 의 후보 집합이 완전히 같아야 한다
벤더 단일 고정            폴백이 살면 회차마다 다른 모델이 섞인다
```

## 파일

| | |
|---|---|
| `variants.py` | 조건 정본. A(현행)·B(개정) 시스템 프롬프트 **전문**과 무엇을 왜 더했는지 |
| `run.py` | 조건 하나를 N회 돌린다. **GMS 를 부르는 유일한 파일** |
| `score_ab.py` | 회차들을 라벨에 붙여 집계. 비결정성과 조건 효과를 같은 자로 낸다 |
| `labels_extra.yaml` | 재판정에서 새로 나온 행의 라벨. `tau_grid/labels.yaml` 은 건드리지 않는다 |

## 실행

`tau_grid/matrix.py` 가 먼저다. 후보와 본문이 거기서 온다.

```bash
cd ai
export DATABASE_URL="postgresql://pinlog:pinlog-local@localhost:15432/pinlog"

.venv/Scripts/python.exe tools/tau_grid/matrix.py            # GMS 호출 없음. DB 만 읽는다
.venv/Scripts/python.exe tools/prompt_ab/run.py --variant A --reps 5   # LLM 210회
.venv/Scripts/python.exe tools/prompt_ab/run.py --variant B --reps 5   # LLM 210회
.venv/Scripts/python.exe tools/prompt_ab/score_ab.py         # 파일만 읽는다
```

`DATABASE_URL` 을 반드시 준다. `.env` 는 07-27 잔재로 `:5433` 을 가리키고 그쪽에는
프리셋이 없다 — `run.py` 가 프리셋 0개면 멈춘다(T33 계열).

`.venv/Scripts/python.exe` 를 쓴다. `python` 은 시스템 Python 을 타고 의존성이 없다(T29).

worktree 에서 돌린다면 `.env` 를 복사해 온다. `.tau/` 와 `.prompt_ab/` 는 gitignore 라
worktree 마다 새로 만들어진다.

### 호출량을 먼저 어림한다

Context 42건 전부가 τ=0.30 에서 후보를 갖는다(`-210` §2). 그러므로 **회차 하나가 정확히
42회**이고 조건 둘 × 5회 = 420회다. 실측 회차당 70초 안팎이라 전체 12분 정도.

### 왜 5회인가

1회 비교로는 「오분류가 2건 줄었다」가 개선인지 흔들림인지 갈리지 않는다 — `-210` 이
같은 프롬프트 재판정만으로 Context 26% 가 달라지는 것을 실측했다(T39).

5회는 회차 5개씩 열 관측을 만든다. 그러면 **두 조건의 관측 범위가 아예 겹치지 않는가**를
볼 수 있고, 효과가 없는데 그렇게 갈릴 확률은 1/C(10,5) ≈ 0.4% 다. 3회면 그 확률이 5% 로
올라 흔들림과 구분이 안 되고, 10회면 GMS 840회를 쓰면서 판정 기준은 그대로다.

## 라벨을 넓힐 때

`tau_grid/labels.yaml` 은 **현행 판정 83행만** 덮는다. 재판정하면 그 목록에 없는 조합이
나오므로(`-210` §4 의 `+9`) 그대로는 `score_ab.py` 가 `unlabeled` 로 센다. 라벨이 필요한
행은 이렇게 뽑는다.

```bash
.venv/Scripts/python.exe tools/prompt_ab/score_ab.py --dump-unlabeled
```

**어느 조건이 고른 행인지는 일부러 찍지 않는다.** 그것을 보고 라벨을 붙이면 B 가 새로
고른 것에 후해지고 A 만 고른 것에 박해진다 — 라벨이 결론을 따라간다. 본문과 프리셋
`description` 만 보고 붙이고, 판정 기준은 `labels.yaml` 머리말 그대로 쓴다.

`labels.yaml` 원본은 고치지 않는다. 고치면 `-210` 의 수치와 비교가 끊긴다.
