# 프리셋 `description`·`examples` 개정 측정

`S15P11A705-228`. 결론과 수치는
[구현 리포트](../../docs/implements/2026-08-03-preset-description.md)에 있고, 이 문서는
**어떻게 다시 돌리는가**만 적는다.

선행이 셋이다. 판정 경로의 세 층을 각각 재고 전부 「바꾸지 않는다」로 끝났다.

| | 층 | 결론 | 왜 |
|---|---|---|---|
| [`-210`](../../docs/implements/2026-07-31-candidate-threshold.md) | 후보 선정(τ) | τ=0.30 유지 | 적합·부적합의 유사도 분포가 겹친다 |
| [`-219`](../../docs/implements/2026-07-31-judge-prompt-rule.md) | 판정 문구 | 프롬프트 무변경 | 오분류가 고정된 표적이 아니다 |
| [`-223`](../../docs/implements/2026-07-31-judge-vote.md) | 판정 분산 | n=1 유지 | 흔들리는 것은 다 지웠는데 7행이 남는다 |

**셋이 서로 다른 층에서 같은 7행을 가리켰다.** 그 7행을 프리셋으로 접으면 5종이고,
그 5종의 `description`·`examples` 를 고치는 것이 이 하네스가 재는 것이다.

## 왜 앞의 하네스를 그대로 못 쓰나

`description` 은 **두 곳**에 들어간다.

```
① 임베딩 입력    app/client/embedding_client.py:36   f"{display_name}. {description} {examples}"
② 판정 프롬프트   app/service/keyword_service.py:98   cand_dicts 에 그대로 실린다
```

`prompt_ab` 는 후보 집합을 조건 사이에 **완전히 같게** 두는 것이 설계의 핵심이었다
(조건이 프롬프트뿐이어야 하므로 `.tau/matrix.json` 하나를 공유한다). 여기서는 조건이
①을 바꾸므로 **행렬이 조건마다 있어야 한다.** 그래서 `matrix.py` 가 따로 있다.

집계는 `prompt_ab/score_ab.py` 를 **그대로 부른다.** 새로 짜면 `-219`·`-223` 과 같은
자를 대고 있다는 보장이 사라진다.

## 파일

| | |
|---|---|
| `variants.py` | 조건 정본(`base`·`base2`·`D`·`E`·`DE`). 개정 내용과 **무엇을 왜 걷었는가** |
| `matrix.py` | 조건별 프리셋 27건 재임베딩 + 유사도 행렬. **GMS 임베딩을 부르는 유일한 파일** |
| `cands.py` | 조건별 후보 집합 이동. 파일만 읽는다 |
| `run.py` | 조건 하나를 N회 판정. **GMS 판정을 부르는 유일한 파일** |
| `split_score.py` | 고친 5종 / 안 고친 22종을 갈라 센다. 자기충족 진단 |

## 실행

```bash
cd ai
export DATABASE_URL="postgresql://pinlog:pinlog-local@localhost:15432/pinlog"
PY=.venv/Scripts/python.exe

$PY tools/preset_desc/variants.py                       # 규칙 검사 + 전후 diff. 호출 없음
$PY tools/preset_desc/matrix.py                         # 임베딩 5회(조건당 1배치)
$PY tools/preset_desc/cands.py                          # 호출 없음

for c in base D DE; do $PY tools/preset_desc/run.py --cond $c --reps 10; done
$PY tools/preset_desc/run.py --cond E --reps 5           # 판정 합 1,470회

$PY tools/prompt_ab/score_ab.py --runs .preset_desc/runs --base base --cond DE \
    --matrix .preset_desc/matrix-DE.json --out .preset_desc/score-DE.json
$PY tools/preset_desc/split_score.py --base base --cond DE

$PY tools/tau_grid/sweep.py --matrix .preset_desc/matrix-DE.json --out .preset_desc/tau-sweep-DE.json
$PY tools/tau_grid/score.py --matrix .preset_desc/matrix-DE.json --out .preset_desc/tau-score-DE.json
```

`DATABASE_URL` 을 반드시 준다 — `.env` 는 07-27 잔재로 `:5433` 을 가리키고 그쪽에는
데이터가 없다(T33). `matrix.py` 가 포트를 검사해 멈춘다.

`.venv/Scripts/python.exe` 를 쓴다. `python` 은 시스템 Python 을 타고 의존성이 없다(T29).
worktree 에서 돌린다면 `.env` 를 복사해 온다(T41).

`run.py` 와 `matrix.py` 는 **이미 있는 산출물을 건너뛴다** — 중단되면 이어 달린다.

## 조건을 왜 넷으로 두나

```
base    현행 yaml. 대조군
base2   base 와 글자 하나까지 같다 — **조건이 아니라 바닥**
D       description 만
E       examples 만
DE      둘 다
```

`base2` 가 이 하네스에만 있는 것이다. 임베딩 API 가 결정적이지 않아서(T68) 같은
텍스트를 다시 떠도 유사도가 최대 `0.0044` 흔들리고 rank 가 43쌍에서 바뀐다. 그 흔들림이
후보 집합을 바꾸는지 재지 않으면 D·E·DE 의 후보 변화를 전부 개정의 몫으로 읽게 된다.

**실측은 후보 집합이 42건 전부 같다.** 흔들림이 τ=0.30 절단선을 넘지 않았다. `base2` 는
판정 회차를 돌리지 않는다 — 후보가 base 와 같으므로 그 판정은 base 재판정과 구분되지
않고, 그러면 회차만 늘리는 것과 같다.

## 회차 수를 왜 그렇게 정했나

`-219` 가 남긴 경고가 이 설계를 규정한다 — *"5회에서 멈추고 기준을 낮췄다면 부풀린
값을 채택했을 것"*(Δ -2.60 이 10회에서 -1.60 으로 줄었다).

```
base·D·DE   10회씩   채택 판단이 걸린 비교라 -219 와 같은 표본 크기를 쓴다
E            5회     원인 규명용이다. 후보 층에서 이미 방향이 갈렸고(§cands)
                     채택 후보가 아니다. 유망하면 그때 10회로 늘린다
```

## 라벨을 넓힐 때

`tau_grid/labels.yaml`(83행) + `prompt_ab/labels_extra.yaml`(24행)을 **한 줄도 고치지
않고** 그대로 쓴다. 기준이 바뀌면 `-210`·`-219`·`-223` 과 비교가 끊긴다.

라벨 밖 행은 `score_ab.py` 가 `unlabeled` 로 따로 세고, 낙관·비관 양극단으로 결론이
뒤집히는지 본다. **이 티켓은 라벨을 더하지 않았다** — 개정안을 설계한 사람이 그 개정안이
새로 고른 행에 라벨을 붙이면 `-219` 가 한계로 남긴 편향이 누적된다.
