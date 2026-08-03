# GMS 이미지 측정 — 생성(축 A) · 분석(축 B)

`S15P11A705-253`. 결론과 수치는
[구현 리포트](../../docs/implements/2026-08-03-gms-image-probe.md)에 있고, 이 문서는
**어떻게 다시 돌리는가**만 적는다.

## 파일

| | |
|---|---|
| `synth.py` | 합성 PNG. **치수와 바이트를 따로 움직인다.** GMS 를 부르지 않는다 |
| `gateway.py` | 자격 증명 로드 · 마스킹 · 호출 상한 · 한 건씩 append |
| `probe_gen.py` | 축 A — 이미지 생성 경로 탐색·프로파일. **GMS 호출** |
| `probe_vision.py` | 축 B — 크기별 토큰·지연·거부 임계. **GMS 호출** |
| `report.py` | 표와 가설 판정. **GMS 를 부르지 않는다** |

`.gms_image/axis-a.jsonl` · `axis-b.jsonl` 은 **커밋한다.** 다시 뜨려면 공용 게이트웨이
쿼터를 또 쓰고, 담는 것은 합성 이미지의 지문·usage·상태 코드뿐이다.

## 다시 돌리기

```bash
.venv/Scripts/python.exe tools/gms_image/synth.py                 # 조건별 바이트 확인. 호출 0
.venv/Scripts/python.exe tools/gms_image/probe_vision.py --plan   # 계획만. 호출 0
.venv/Scripts/python.exe tools/gms_image/report.py --replay       # 판정. 호출 0
```

**호출하는 것은 위 둘뿐이다.** 상한이 코드에 박혀 있고(`MAX_CALLS` 축 A 20 · 축 B 30)
넘으면 예외로 멈춘다. 이어서 돌릴 때는 `--used` 로 누적을 넘긴다.

```bash
.venv/Scripts/python.exe tools/gms_image/probe_gen.py --stage discover
.venv/Scripts/python.exe tools/gms_image/probe_gen.py --stage profile --probe openai:gpt-image-1 --reps 4 --used 7
.venv/Scripts/python.exe tools/gms_image/probe_vision.py --run main
.venv/Scripts/python.exe tools/gms_image/probe_vision.py --run ladder --used 20
.venv/Scripts/python.exe tools/gms_image/probe_vision.py --adhoc openai:px512-n45 --used 28
```

기록 파일은 **덮어쓰지 않고 이어 붙인다.** 같은 이름으로 다시 돌리면 회차가 쌓인다 —
지우고 싶으면 파일을 직접 지운다. 쌓이는 쪽을 기본으로 둔 것은 **잃는 쪽이 더 비싸기**
때문이다(GMS 호출은 다시 뜨면 쿼터를 또 쓴다).

## 실제 이미지를 쓰지 않는다

측정에 대화 캡처·실제 사진을 넣지 않는다. 개인정보이기도 하고, **실제 사진으로는 이
측정이 애초에 성립하지 않는다** — 사진은 치수가 커지면 바이트도 같이 커져서 `-253` 의
세 가설이 전부 같은 곡선을 낸다. `synth.py` 머리말에 그 설계가 있다.

## 다음 사람에게

**수치를 상수로 읽지 마라.** GMS 쿼터·상한은 시점과 프로바이더 경로별로 다르다(T27).
리포트의 수치에는 측정 시각(KST)과 경로가 붙어 있다 — 다시 재려면 그것부터 보라.

판정 규칙만 고칠 때는 `report.py --replay` 로 끝난다. **다시 부르지 마라** — 같은 규칙
변경과 쿼터 변동이 섞이면 결론이 왜 바뀌었는지 알 수 없다.
