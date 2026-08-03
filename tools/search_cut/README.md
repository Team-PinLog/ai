# 검색 결과 컷 측정 — `τ_abs` × `r`

`S15P11A705-213`. 결론과 수치는
[구현 리포트](../../docs/implements/2026-07-31-search-cut.md)에 있고, 이 문서는
**어떻게 다시 돌리는가**만 적는다.

## 왜 `tau_grid` 를 그대로 쓰지 않았나

`tools/tau_grid` 는 대상이 `(Context, Preset)` 이고 현행 LLM 판정을 기준점으로 삼는다.
검색은 대상이 `(질의, Record)` 이고 **판정이 없다** — 기준점이 `demo_data.yaml` 의
기대 정답이다. 구조(행렬을 한 번 떠서 굳히고 오프라인으로 훑는다)는 같지만 재는 것이
다르므로 새로 짰다.

한 가지가 더 낫다. `tau_grid` 의 재구성은 **근사**였다(후보가 줄면 LLM 판정이 뒤집힐 수
있다). 검색 경로에는 LLM 이 없고 임베딩은 결정적이라 **이쪽 재구성은 정확하다** —
`verify_live.py` 가 실서버와 27/27 일치를 확인한다.

## 파일

| | |
|---|---|
| `matrix.py` | 질의를 임베딩하고 질의별 Record 전량의 유사도를 `.search/matrix.json` 으로 굳힌다. **GMS 임베딩 배치 1회** |
| `labels.yaml` | 정답 아닌 141행 중 `plausible` 인 것. 판정 기준이 머리말에 있다 |
| `sweep.py` | 분포와 `τ_abs × r` 격자. **DB 도 GMS 도 부르지 않는다** |
| `label_sheet.py` | 라벨을 손으로 채우기 위한 시트(본문 포함). 출력물은 커밋하지 않는다 |
| `verify_live.py` | 오프라인 재구성이 실서버 응답과 같은지. **정확 일치를 요구한다** |
| `recall_probe.py` | 「본문에 있는 말로 검색해도 안 나온다」의 원인 판별(`S15P11A705-255`). 질의 22건 × Record 전량. **GMS 임베딩 배치 1회** |
| `word_matrix.py` | **단어형** 질의 54건 × 소유자 3명의 행렬(`S15P11A705-266`). 기대 정답을 손으로 짝짓지 않고 **본문 문자열 포함으로 계산**한다. **GMS 임베딩 배치 1회** |
| `word_sweep.py` | 단어형·문장형을 **한 표에** 놓고 격자를 훑는다. **DB 도 GMS 도 부르지 않는다** |

`matrix.json` · `recall_probe.json` · `word_grid.json` 은 **커밋한다.** 다시 뜨려면 GMS 를
부르고, `tau_grid` 의 것과 달리 Context 본문을 담지 않는다(장소명까지).

## 단어형 컷 격자 (`S15P11A705-266`)

`recall_probe.py` 와 대상이 다르다 — 저쪽은 **세 이슈의 원인을 가르려고** 질의 표현을
바꿔 가며 같은 Record 를 추적하고, 이쪽은 **컷 값을 정하려고** 질의를 늘려 대역을 잰다.
그래서 무관 통제가 저쪽은 1건(`치과`)이고 이쪽은 45행이다 — 컷 값 판단은 「무관 대역이
어디까지 올라오는가」가 전부이므로 그 대역을 1점으로 재면 안 된다.

```bash
.venv/Scripts/python.exe tools/search_cut/word_matrix.py    # GMS 배치 1회. DB 를 읽는다
.venv/Scripts/python.exe tools/search_cut/word_sweep.py     # 파일 둘만 읽는다
```

`word_sweep.py` 는 `word_grid.json`(단어형)과 `matrix.json`(문장형)을 **함께** 읽는다.
따로 내면 「한 값이 둘 다를 만족하는가」에 답할 수 없기 때문이다.

`word_matrix.py` 는 재기 전에 둘을 확인하고 **어긋나면 GMS 를 부르지 않고 멈춘다.**

```
무관 통제가 본문에 있다             그 행은 통제가 아니라 정답 있는 질의다
단어형 질의가 어느 소유자에게도 없다   정답 누락 분모에서 조용히 빠진다
```

둘째 가드가 실제로 `초밥` 을 잡았다 — 「진우네 초밥」의 **장소명**에만 있고 본문은
「일식집」이다. 임베딩이 받는 것은 `context` 하나뿐이라 장소명을 정답 기준에 넣으면
모델에 주지 않은 정보를 기대하게 된다.

결론은 [구현 리포트](../../docs/implements/2026-08-03-word-query-cut.md)에 있다.

## 재현율 프로브 (`S15P11A705-255`)

`matrix.py` 와 대상이 다르다 — 저쪽은 **격자를 훑기 위해** 검증 질의 12건의 전량 유사도를
굳히고, 이쪽은 **질의 표현을 바꿔 가며** 같은 Record 가 어떻게 움직이는지 본다.

```bash
.venv/Scripts/python.exe tools/search_cut/recall_probe.py                          # GMS 배치 1회
.venv/Scripts/python.exe tools/search_cut/recall_probe.py --replay .search/recall_probe.json
.venv/Scripts/python.exe tools/search_cut/recall_probe.py --lengths .search/recall_probe.json
```

`--replay` 는 **판정 규칙만** 다시 낸다(DB·GMS 미호출). 컷도 판정도 유사도에 걸릴 뿐
임베딩에 걸리지 않으므로, `RECOVER_RANK` 나 컷 값을 바꿔 볼 때 GMS 를 다시 부르지 않는다.
`--lengths` 는 본문 길이와 유사도의 순위 상관을 낸다(DB 만 읽는다 — 행렬이 본문을 담지
않으므로 길이는 DB 에서 온다).

결론은 [구현 리포트](../../docs/implements/2026-08-03-search-recall-probe.md)에 있다.

## 실행

```bash
cd ai
export DATABASE_URL="postgresql://pinlog:pinlog-local@localhost:15432/pinlog"

.venv/Scripts/python.exe tools/search_cut/matrix.py   # GMS 임베딩 1배치. DB 를 읽는다
.venv/Scripts/python.exe tools/search_cut/sweep.py    # 파일만 읽는다
```

`DATABASE_URL` 을 반드시 준다. `.env` 는 07-27 잔재로 `:5433` 을 가리키는데 그쪽에는
데이터가 없어 **재고 「컷이 아무것도 자르지 않는다」를 결론으로 낸다.** `matrix.py` 가
`:15432` 가 아니면 멈춘다(T33).

`.venv/Scripts/python.exe` 를 쓴다. `python` 은 시스템 Python 을 타고 의존성이 없다(T29).

**worktree 에서 돌린다면 `.env` 를 그쪽에도 둔다.** `get_settings()` 의 `env_file` 은
CWD 기준이고 `.env` 는 gitignore 라 worktree 에 없다 — `GMS_API_KEY` 부터 없어서 죽는다(T41).

### 실서버 대조

서버를 **이 브랜치 코드로** 띄워야 한다. 다른 워킹트리의 서버를 재면 컷이 없는 코드를
재고 「구현이 안 먹는다」를 결론으로 낸다.

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8002 > .search/uvicorn.log 2>&1 &
.venv/Scripts/python.exe tools/search_cut/verify_live.py --ai http://127.0.0.1:8002
```

`word_grid.json` 이 있으면 단어형도 함께 던진다 — 다만 **두 하한(단어형·문장형)에서
결과가 갈리는 행만** 고른다. 갈리지 않는 행은 서버가 분기를 타든 안 타든 통과하므로
GMS 호출만 늘고 재는 값이 늘지 않는다. 재구성 쪽에도 길이 분기를 **다시 적어** 두었다 —
구현을 `import` 하면 서버가 옛 단일값 경로를 돌아도 검증이 통과한다.

로그는 파이프가 아니라 리디렉션으로 받는다 — 파이프는 프로세스가 사는 동안 0바이트다(T30).
질의 수만큼 GMS 임베딩을 부른다(요청당 1회).

## 데이터가 바뀌면

`record_id` 는 시딩 시점에 정해진다. 재시딩하면 `labels.yaml` 의 참조가 어긋나고
`sweep.py` 가 **재지 않고 멈춘다**(라벨과 행렬의 대조를 먼저 한다). 그때는 `matrix.py` 를
다시 돌리고 `label_sheet.py` 로 본문을 보며 라벨을 다시 맞춘다.

라벨에 이의가 있으면 해당 행만 고치고 `sweep.py` 를 다시 돌리면 된다. GMS 는 부르지 않는다.
