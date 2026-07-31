# 후보 유사도 임계값 τ 측정

`S15P11A705-210`. 결론과 수치는
[구현 리포트](../../docs/implements/2026-07-31-candidate-threshold.md)에 있고, 이 문서는
**어떻게 다시 돌리는가**만 적는다.

## 왜 `emb_grid` 를 그대로 쓰지 않았나

`tools/emb_grid` 는 조건마다 **전량 재시딩**한다. 임베딩 모델·차원이 조건이라 벡터를
다시 만들 수밖에 없기 때문이다. **τ 는 임베딩을 바꾸지 않는다** — 후보 선정에만 걸린다.
그대로 쓰면 조건마다 GMS 임베딩 42회를 이유 없이 다시 쓴다.

대신 벡터를 한 번 떠서 굳히고 τ 를 오프라인으로 훑는다. 구조(조건 정본·사전 검증·JSON
산출)는 `emb_grid` 를 따랐다.

## 파일

| | |
|---|---|
| `matrix.py` | DB 에서 42×27 유사도와 현행 판정을 떠서 `.tau/matrix.json` 으로 굳힌다 |
| `sweep.py` | 분포와 τ 격자. **DB 도 GMS 도 부르지 않는다** |
| `labels.yaml` | 현행 판정 83행의 적합성 라벨. 판정 기준이 머리말에 있다 |
| `score.py` | 라벨을 붙여 **오분류 감소분과 정상 판정 누락분을 함께** 센다 |
| `probe_gate.py` | 무관·짧은정상 본문을 임베딩해 Context 게이트 γ 가 둘을 가르는지 본다 |
| `verify_reconstruction.py` | 오프라인 재구성이 실제 재판정과 얼마나 어긋나는지. **대조군을 함께 돌린다** |

## 실행

```bash
cd ai
export DATABASE_URL="postgresql://pinlog:pinlog-local@localhost:15432/pinlog"

.venv/Scripts/python.exe tools/tau_grid/matrix.py       # GMS 호출 없음. DB 만 읽는다
.venv/Scripts/python.exe tools/tau_grid/sweep.py        # 파일만 읽는다
.venv/Scripts/python.exe tools/tau_grid/score.py        # 파일만 읽는다
```

`DATABASE_URL` 을 반드시 준다. `.env` 는 07-27 잔재로 `:5433` 을 가리키는데 그쪽에는
데이터가 없어 **재고 「0건」을 결론으로 낸다.** `matrix.py` 가 `:15432` 가 아니면 멈춘다(T33).

`.venv/Scripts/python.exe` 를 쓴다. `python` 은 시스템 Python 을 타고 의존성이 없다(T29).

### GMS 를 부르는 둘

```bash
.venv/Scripts/python.exe tools/tau_grid/probe_gate.py                      # 임베딩 16회
.venv/Scripts/python.exe tools/tau_grid/verify_reconstruction.py --tau 0.34  # 판정 81회
```

`verify_reconstruction.py` 는 **DB 를 건드리지 않는다.** 재시딩도 서버 기동도 없이
`judge` 를 직접 부르고 비교만 한다. 대조군(τ=0.30 재판정)이 절반을 차지하는데
**그것을 빼면 안 된다** — 같은 τ 로도 Context 26% 가 흔들리므로 대조군 없이는 차이가
τ 때문인지 가릴 수 없다(T39).

## 데이터가 바뀌면

`context_id` 는 시딩 시점에 정해진다. 재시딩하면 `labels.yaml` 의 참조가 어긋나고
`score.py` 가 **재지 않고 멈춘다**(라벨과 판정의 대조를 먼저 한다). 그때는 `matrix.py` 를
다시 돌리고 본문 기준으로 라벨을 다시 맞춘다.

라벨에 이의가 있으면 `labels.yaml` 의 해당 행만 고치고 `score.py` 를 다시 돌리면 된다.
GMS 는 부르지 않는다.
