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

`matrix.json` 은 **커밋한다.** 다시 뜨려면 GMS 를 부르고, `tau_grid` 의 것과 달리 Context
본문을 담지 않는다(장소명까지).

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

로그는 파이프가 아니라 리디렉션으로 받는다 — 파이프는 프로세스가 사는 동안 0바이트다(T30).
질의 수만큼 GMS 임베딩을 부른다(요청당 1회).

## 데이터가 바뀌면

`record_id` 는 시딩 시점에 정해진다. 재시딩하면 `labels.yaml` 의 참조가 어긋나고
`sweep.py` 가 **재지 않고 멈춘다**(라벨과 행렬의 대조를 먼저 한다). 그때는 `matrix.py` 를
다시 돌리고 `label_sheet.py` 로 본문을 보며 라벨을 다시 맞춘다.

라벨에 이의가 있으면 해당 행만 고치고 `sweep.py` 를 다시 돌리면 된다. GMS 는 부르지 않는다.
