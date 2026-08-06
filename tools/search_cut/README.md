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
| `boundary_matrix.py` | 단어형 **경계 정의** 두 가지를 가르는 행렬(`S15P11A705-273`). 본문 인접 어절쌍을 `spaced`/`joined` 짝으로 낸다. **GMS 임베딩 배치 3회** |
| `boundary_sweep.py` | `_is_word_query` 정의 6종을 같은 행렬에 걸어 비교한다. **DB 도 GMS 도 부르지 않는다** |
| `layer_probe.py` | 질의가 **어느 층에서** 몇 건을 잃는지. 후보·LIMIT·τ·r·**실서버**를 한 줄에 놓는다 |
| `rank_score.py` | 검색 **순위** 지표 baseline(P48 0단계, I52). 컷 기준 지표로는 순위 변화가 안 보여서 따로 둔다. Hit·Recall·MRR·nDCG 를 컷 전/후 · 단어형/문장형 · 정답/무관으로 갈라 낸다. **DB 도 GMS 도 부르지 않는다** |
| `fusion.py` | keyword 신호 fusion **순수 로직**(P48 1단계). DB·GMS·파일을 읽지 않고 인자만 받는다 — `tests/test_search_fusion.py` 가 픽스처로 검증한다 |
| `keyword_matrix.py` | keyword 신호 artifact 생성 — 질의별 전체 활성 Preset 코사인 + Context 별 keyword·confidence·상태. **GMS 임베딩 배치 1회 + DB 읽기** |
| `fusion_sweep.py` | fusion 방식(binary·confidence·idf·RRF)×가중치×floor×RRF cutoff 격자 — **P48 구조**(후보 합집합 후 병합 점수에 컷). `keyword_matrix.json` 과 행렬 셋만 읽는다 — **DB 도 GMS 도 부르지 않는다** |
| `fusion_rerank_sweep.py` | keyword 재정렬 전용 병합 격자 — **P49 §4 구조**(컷 통과 집합 고정·순서만 조정). BASE·binary(floor×weight)·RRF 비교. 같은 파일만 읽는다 — **DB 도 GMS 도 부르지 않는다** |
| `lexical_matrix.py` | 문자열 매치 artifact — 본문에 질의가 그대로 있는지를 (질의×소유자×Record)로 굳힌다. 본문은 저장하지 않는다. **스냅샷 DB(:25432) 읽기 · GMS 0회** |
| `lexical_sweep.py` | 문자열 병합 규칙 격자 — 게이트 3단×병합 3종. `lexical_matrix.json` 과 행렬 셋만 읽는다 — **DB 도 GMS 도 부르지 않는다** |

`matrix.json` · `recall_probe.json` · `word_grid.json` · `keyword_matrix.json` 은 **커밋한다.**
다시 뜨려면 GMS 를 부르고, `tau_grid` 의 것과 달리 Context 본문을 담지 않는다(장소명까지).

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

### 값을 다시 정하기 전에 — 재현성부터 본다

`T68`(`-228`)이 임베딩 API 가 **같은 배치 구성으로도** 결정적이지 않음을 실측했다
(`|Δsim|` 최대 **0.0044**). 그 크기가 격자 간격(0.01)과 같은 자릿수라, **그대로면 인접
τ 값의 차이가 실제 차이인지 재측정 운인지 갈리지 않는다.**

```bash
.venv/Scripts/python.exe tools/search_cut/word_matrix.py --out .search/word_grid_run2.json
.venv/Scripts/python.exe tools/search_cut/word_sweep.py \
    --repro .search/word_grid.json,.search/word_grid_run2.json
```

회차 간 흔들림과 **같은 τ 에서 판정이 회차마다 같은지**를 낸다. `-266` 시점 실측은
상한 `0.000209`(회차 3개)로 격자 간격보다 두 자릿수 작았고 전 구간 판정이 일치했다.
**갈리기 시작하면 그 구간의 인접 값 비교를 신뢰하지 말고 결론을 보류한다** — `T68` 이
`base2` 를 상시 조건으로 둔 것과 같은 이유다.

`τ` 격자를 **`0.01` 보다 촘촘하게 두지 않는다**(`T68` 의 조언).

결론은 [구현 리포트](../../docs/implements/2026-08-03-word-query-cut.md)에 있다.

## 단어형 경계 정의 (`S15P11A705-273`)

`word_matrix.py` 와 대상이 다르다 — 저쪽은 **컷 값**을 정하려고 1어절 질의의 대역을
재고, 이쪽은 **경계 정의**(글자 수인가 어절 수인가)를 가르려고 **공백만 다른 짝**을
만든다. `-266` 이 잰 질의가 전부 1어절 2~5자라 두 정의가 같은 답을 냈고, 그 한계를
스스로 후속으로 남겼다.

```bash
.venv/Scripts/python.exe tools/search_cut/boundary_matrix.py --dry   # 대역 분포만. GMS 미호출
.venv/Scripts/python.exe tools/search_cut/boundary_matrix.py         # GMS 배치 3회. DB 를 읽는다
.venv/Scripts/python.exe tools/search_cut/boundary_sweep.py --focus  # 파일 둘만 읽는다
```

`boundary_sweep.py` 는 `boundary_grid.json`(짝)과 `word_grid.json`(1어절)을 **함께**
읽는다 — 「1어절 대역에서 옳은 규칙이 2어절 대역에서도 옳은가」가 이 티켓의 질문이다.

`--focus` 는 내용어 쌍 16종의 지표를 전량 옆에 낸다. **전량에는 기능어 쌍이 섞이고**
(`당시 자주`·`거의 없고`) 그것이 정답 누락 수를 부풀린다. 재량을 없애는 대신 **재량의
영향을 눈에 보이게 두는** 설계다 — `-266` 이 「어떤 말을 뽑을지는 재량이었다」로 남긴
것에 대한 답이다.

### 층별 탈락

「어느 층이 얼마를 걸러냈는가」는 컷 값 격자로 답할 수 없다. 서버를 띄우고 던진다.

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8003 > .search/uvicorn.log 2>&1 &
.venv/Scripts/python.exe tools/search_cut/layer_probe.py --ai http://127.0.0.1:8003
```

`--no-live` 면 서버 없이 ①~④ 만 낸다. **재구성이 서버와 건수까지 같은지**가 이 도구의
검증이고, 다르면 「우리가 이해한 코드」와 실물이 갈린 것이다.

결론은 [구현 리포트](../../docs/implements/2026-08-05-short-query-boundary.md)에 있다.

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

**`--replay` 는 기본적으로 파일을 쓰지 않는다.** 재판정은 화면으로 읽는 것이 목적이고,
기본 출력 경로를 두면 위 명령을 그대로 돌린 사람이 **커밋된 행렬을 판정 결과로 덮어쓴다** —
행렬은 GMS 를 불러야 다시 뜨므로 그 손실이 판정보다 무겁다. 남기려면 `--out` 에 **다른**
경로를 준다(입력과 같으면 쓰지 않고 멈춘다).

이 프로브의 질의는 **단어형**이라 `τ_abs` 가 질의별로 갈린다(`S15P11A705-266`). 단일 하한을
쓰면 이미 고쳐진 증상(`ai#87` 의 `그네`·`스팟`)을 계속 「① 컷이 잘랐다」로 보고한다 —
**진단 도구가 닫힌 증상을 미해결로 읽는다.**
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

### keyword fusion (P48 1단계, `S15P11A705-336` 실측)

```bash
.venv/Scripts/python.exe -m pytest tests/test_search_fusion.py tests/test_keyword_matrix_parse.py -q
                                                          # 픽스처 검증. DB·GMS 0회
.venv/Scripts/python.exe tools/search_cut/rank_score.py   # 순위 baseline. 파일만 읽는다
.venv/Scripts/python.exe tools/search_cut/keyword_matrix.py   # GMS 배치 1회 + DB 읽기
.venv/Scripts/python.exe tools/search_cut/fusion_sweep.py --rrf-cutoff-grid "0,0.004,0.008,0.016" --floor 0.35
                                                          # 파일만 읽는다
```

**floor 를 반드시 축으로 훑는다** — 기본값(0.25)은 관련 없는 질의의 결과가 노출되는
대역이라, 그 값만 보면 무관 노출 조건을 잘못 통과시킨다(T73). `word_matrix.py` 와
`recall_probe.py` 는 행에 `context_id` 를 포함해야 keyword 조인이 성립한다 — 낡은
행렬이면 `fusion_sweep.py` 의 가드가 재지 않고 멈춘다. 결과 판정 기준은 P48 §6.1,
실측 기록은 [구현 리포트 I53](../../docs/implements/2026-08-05-fusion-measurement.md).

### keyword 재정렬 전용 병합 (P49 작업 4, `S15P11A705-339`)

```bash
.venv/Scripts/python.exe tools/search_cut/fusion_rerank_sweep.py   # 파일만 읽는다
.venv/Scripts/python.exe tools/search_cut/fusion_rerank_sweep.py \
    --json .search/fusion_rerank_sweep.json                        # 결정성 회차용 보존
```

`fusion_sweep.py` 와 **병합 의미가 다르다** — 저쪽은 P48 구조(후보 합집합을 만들고
컷을 병합 점수에 건다)이고, 이쪽은 P49 §4 가 확정한 구조(현행 컷이 후보를 먼저
확정하고 keyword 신호는 그 안의 순서만 조정한다)다. I53 의 채택값은 P48 구조의
관측이므로 이 구조의 채택값은 이 스크립트로 다시 쟀다.

재정렬 전후의 후보 Record id 집합이 다르면 **수치를 내지 않고 멈춘다** — 무관 질의
노출이 구조적으로 불변이라는 P49 §5 의 전제를 스크립트 스스로 검사한다. artifact 의
profile·preset_version 이 현행과 어긋나도 멈춘다(`--expect-*` 로 현행 값을 덮어쓴다).

결정성 회차는 `--json` 을 서로 다른 경로로 두 번 돌려 파일이 같은지로 본다 — 입력이
파일뿐이라 같은 입력·같은 인자면 같은 출력이어야 하고, 다르면 하네스가 비결정적인
것이다. 신규 출력은 `.search/fusion_rerank_` 접두로 만들어 기존 artifact 를 덮지
않는다. 결과 판정은 [구현 리포트](../../docs/implements/) 의 `-339` 리포트에 있다.

### 문자열 병합 규칙 (P49 작업 3)

```bash
export DATABASE_URL="postgresql://pinlog:pinlog-local@localhost:25432/pinlog"   # 스냅샷 DB
.venv/Scripts/python.exe tools/search_cut/lexical_matrix.py   # DB 읽기. GMS 0회
.venv/Scripts/python.exe tools/search_cut/lexical_sweep.py    # 파일만 읽는다
```

검색 고도화 트랙의 측정은 시연 DB(:15432)가 아니라 **스냅샷 DB(:25432)** 에서 한다 —
브랜치는 코드만 격리하고 DB 는 격리하지 않는다(P49 §6). `lexical_matrix.py` 가 포트를
검사해 다르면 멈춘다. 결과 판정과 규칙 확정안은
[구현 리포트 I54](../../docs/implements/2026-08-06-lexical-merge-rule.md).

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
