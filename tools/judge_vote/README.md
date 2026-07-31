# 판정 n회 다수결 측정

`S15P11A705-223`. 결론과 수치는
[구현 리포트](../../docs/implements/2026-07-31-judge-vote.md)에 있고, 이 문서는
**어떻게 다시 돌리는가**만 적는다.

선행이 둘이다. [τ](../../docs/implements/2026-07-31-candidate-threshold.md)(`-210`)와
[프롬프트](../../docs/implements/2026-07-31-judge-prompt-rule.md)(`-219`)가 각각 다른
이유로 막혔고, 둘을 막은 것은 같다 — **판정 자체가 흔들린다.** 라벨과 데이터를 그대로
물려받는다. 기준이 바뀌면 앞의 둘과 비교가 끊긴다.

## 이 하네스의 수법

**새로 부르지 않고 접는다.** n회 다수결 1회분은 「같은 조건에서 독립으로 뽑은 판정 n개를
다수결한 것」이고, `-219` 하네스(`tools/prompt_ab/run.py`)의 회차 하나가 정확히 그
「독립으로 뽑은 판정」이다. 그러므로 회차 3개를 묶어 다수결한 것과 n=3 을 실제로 한 번
돌린 것은 같은 확률변수다.

```
따로 돌리면   n=1 ×10 + n=3 ×10 + n=5 ×6  =  420 + 1,260 + 1,260  =  2,940 호출
접으면        회차 30개 = 1,260 호출로 셋을 전부 얻는다
```

`-210` 이 유사도 행렬 하나로 임의의 τ 를 재구성한 것과 같다. 저쪽은 τ 가 임베딩을 안
바꿔서 됐고, 이쪽은 회차가 서로 독립이라 된다.

**다수결 규칙은 서비스 코드를 그대로 부른다** — `app.service.judge_vote.combine`. 여기
같은 식을 다시 적으면 「우리가 잰 규칙」과 「서버가 쓰는 규칙」이 갈라진다
(`tau_grid/matrix.py` 가 `_topk` 를 그대로 부르는 것과 같은 이유).

## 파일

| | |
|---|---|
| `compose.py` | 단일 회차들을 n회 다수결 회차로 접는다. **GMS 를 부르지 않는다** |
| `run_live.py` | 실제 `KeywordService._judge_n` 을 n회 호출로 돌린다. 접은 값의 대조군 |

채점은 `-219` 의 `tools/prompt_ab/score_ab.py` 를 그대로 쓴다. 두 도구의 출력 형식이
`run.py` 와 같아서 새 채점 기준을 만들지 않는다.

## 실행

```bash
cd ai
export DATABASE_URL="postgresql://pinlog:pinlog-local@localhost:15432/pinlog"

.venv/Scripts/python.exe tools/tau_grid/matrix.py                        # DB 만 읽는다
.venv/Scripts/python.exe tools/prompt_ab/run.py --variant A --reps 30    # LLM 1,260회
.venv/Scripts/python.exe tools/judge_vote/compose.py --n 1 3 5           # 호출 없음
.venv/Scripts/python.exe tools/prompt_ab/score_ab.py \
    --runs .judge_vote/runs --base n1 --cond n3 --out .judge_vote/score-n3.json
.venv/Scripts/python.exe tools/prompt_ab/score_ab.py \
    --runs .judge_vote/runs --base n1 --cond n5 --out .judge_vote/score-n5.json
```

`run.py` 는 이미 있는 회차를 건너뛴다 — 중단되면 이어 달린다. 회차 하나가 42호출·70초
안팎이므로 30회에 35분쯤 걸린다.

`DATABASE_URL` 을 반드시 준다. `.env` 는 07-27 잔재로 `:5433` 을 가리키고 그쪽에는
프리셋이 없다(T33 계열). `.venv/Scripts/python.exe` 를 쓴다 — `python` 은 시스템
Python 을 타고 의존성이 없다(T29).

### 실경로 대조

접은 값이 맞다는 근거는 「회차가 독립」이라는 가정 하나뿐이고 **접어서는 그 가정을
확인할 수 없다.**

```bash
.venv/Scripts/python.exe tools/judge_vote/run_live.py --n 3 --reps 3     # LLM 378회
.venv/Scripts/python.exe tools/prompt_ab/score_ab.py \
    --runs .judge_vote/all --base n3 --cond live3                        # 같은 폴더에 모아서
```

`run_live.py` 는 `KeywordService._judge_n` 을 그대로 부른다 — 동시 호출·정족수·다수결이
전부 그 안에 있다. Context 1건당 실제 지연(`sec_per_context`)도 여기서만 나온다.

### 순열검정을 걸 때

`score_ab.py` 의 순열검정은 **전수 계산**이라 관측 수가 크면 끝나지 않는다
(30 대 10 이면 C(40,10) ≈ 8.5억). `--cap` 으로 조건당 관측 수를 맞춘다.

```bash
.venv/Scripts/python.exe tools/judge_vote/compose.py --n 1 3 --cap 10    # C(20,10)=184,756
```

## 접은 값의 한계 — 숨기지 않는다

* **조건들이 같은 호출 풀을 공유한다.** n=1 관측과 n=3 관측이 독립 표본이 아니다.
  평균·범위 비교는 그대로 유효하지만(짝지어진 설계라 오히려 잡음이 준다), 순열검정은
  표본 독립을 가정하므로 **참고값으로만** 읽는다
* **분할 방식이 결과를 만들 수 있다.** `--shuffle SEED` 로 재분할해 확인한다
* **회차 사이의 시간 드리프트를 못 본다.** 30회가 35분에 걸쳐 돌므로 그 사이 게이트웨이
  쪽이 바뀌면 앞뒤 회차가 같은 분포가 아니다. 회차 파일의 `models` 로 사후 확인한다
