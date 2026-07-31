# 트러블슈팅 (Troubleshooting)

구현·문서 작업 중 겪은 문제와 그 해결을 재현 가능한 형태로 남깁니다.

## 보존 원칙

이 폴더는 문제 해결 과정과 구현 이력을 기록합니다. **해결·완료된 항목도 삭제하지 않고 상태 표시만 갱신합니다.** 회고와 복기에서 "무엇을 어떻게 해결했는가"를 추적하기 위함입니다.

- 해결됨 → 문서 유지 + `상태: 해결됨` + 해결 경로·링크 추가
- 무효화 → 문서 유지 + `상태: 무효(사유)` 표기
- 삭제 → 하지 않음. 잘못 작성된 문서도 정정으로 처리

※ `spec/`은 현재 유효한 명세이므로 이 원칙의 대상이 아닙니다(낡은 내용은 갱신·삭제).

## 개별 문서

| 문서 | 내용 |
|---|---|
| [mermaid-headless-validation.md](mermaid-headless-validation.md) | Mermaid 다이어그램 브라우저리스 문법 검증 (T7·T8) |
| [2026-07-23-fastapi-local-verification.md](2026-07-23-fastapi-local-verification.md) | FastAPI 로컬 검증 중 런타임/드라이버 이슈 (T16~T18) |
| [2026-07-24-e3-ci-and-search-path.md](2026-07-24-e3-ci-and-search-path.md) | E3 CI·런타임 이슈 — lock 플랫폼 종속·pytest pythonpath·search_path (T19~T21) |
| [2026-07-27-e2e-env-issues.md](2026-07-27-e2e-env-issues.md) | E2E 검증 환경 이슈 — `.env` CRLF·register_vector 미등록·한글 인코딩 (T22~T24) |
| [2026-07-28-shared-worktree-and-env-cache.md](2026-07-28-shared-worktree-and-env-cache.md) | 멀티세션 워킹트리 오염 · import 시점 `.env` 캐시 (T25·T26) |
| [2026-07-30-seeding-quota-and-encoding.md](2026-07-30-seeding-quota-and-encoding.md) | GMS 판정 쿼터·콘솔 인코딩으로 인한 시딩 중단 (T27·T28) |
| [2026-07-31-local-e2e-and-ci-pitfalls.md](2026-07-31-local-e2e-and-ci-pitfalls.md) | 로컬 E2E·CI 함정 — venv·로그 버퍼·jar 낙후·포트·로그인 쿠키 (T29~T36) |
| [2026-07-31-tau-measurement.md](2026-07-31-tau-measurement.md) | 후보 임계값 τ 측정 — 틀린 진단·인코딩 재발·대조군 부재 (T37~T39) |
| [2026-07-31-search-cut-measurement.md](2026-07-31-search-cut-measurement.md) | 검색 결과 컷 측정 — 반대 방향 질의 부재·worktree `.env`·배치 구성과 임베딩 재현성 (T40~T42) |
| [2026-07-31-judge-prompt-ab.md](2026-07-31-judge-prompt-ab.md) | 판정 프롬프트 A/B — 죽은 설정 키·라벨 커버리지·조건 노출·사전 기준 (T40~T43) |
| [2026-07-31-judge-prompt-ab.md](2026-07-31-judge-prompt-ab.md) | 판정 프롬프트 A/B — 죽은 설정 키·라벨 커버리지·조건 노출·사전 기준·1회 분포 (T40~T44) |

## 문제 해결 — 전수 (AI 소유)

| T | 증상 | 해결 |
|---|---|---|
| T1 | main에 직접 커밋됨 | 백업→revert→피처 브랜치 재적용, Conventional Commits 채택 |
| T2 | draft/06·07·09 rebase 충돌(MINYONG 독립 반영) | Option B rebase, 비-AI 개선 보존 + AI 스키마 교체, force-with-lease |
| T3 | PR3 "가드 수정경로 적용 금지" ↔ PR1 모순 | insert-first면 가드 자연통과 → "그대로 통과·특례 금지"로 통일 |
| T4 | `updated_at` 확정/미결 엇갈림 | MINYONG 안 채택으로 확정 통일(제거) |
| T5 | `gh: command not found`(bash) | `C:\Program Files\GitHub CLI\gh.exe` 전체경로 호출 |
| T6 | ai 레포 기본 브랜치가 피처 브랜치 | `gh repo edit`로 main 변경 |
| T7 | Mermaid 검증 실패(@mermaid-js/parser는 flowchart 미지원, jsdom navigator getter-only) | mermaid@11+jsdom, `Object.defineProperty`로 navigator 우회 → 4/4 valid |
| T8 | Mermaid 추출 파싱 오류(CRLF) | 정규식 `.replace(/\r\n/g,"\n")` |
| T11 | 마이그레이션 실검증 필요 | pgvector 컨테이너에서 V1→V102 순차·PK·extension·중복스키마 실패 확인 |
| T12 | preset YAML 규칙 위반 방지 | Python 스크립트로 개수/배분/유일/visibility/examples 검증 |
| T13 | docs `11_개발_컨벤션.md` 삭제 부작용(pgvector 검토 유일 출처) | 소실 내용 식별·고지 |
| T14 | `docs/ai-architecture-diagrams` 브랜치가 eval 하네스 커밋 위에 오정렬 | main 기준 재정렬(`rebase --onto`) |
| T15 | back ADR "소유 파트: AI 파트" 혼란(표준 ADR엔 소유파트 필드 없음) | 주도(Driver)로 격하 + 레포 스코프 명시(P36) |
| T16 | `.env` UTF-8 BOM으로 첫 키(`DATABASE_URL`) 파싱 실패 | BOM 없이 기록(`UTF8Encoding($false)`), 첫 3바이트 확인 |
| T17 | pgvector가 VECTOR 컬럼을 `Vector` 객체로 반환 → `np.asarray` TypeError | `to_numpy()`/`to_list()` 변환(디코드 방향만) |
| T18 | asyncpg `now() - $2` interval 타입 추론 실패(`timestamptz < interval`) | `$2::interval` 명시 캐스트 |
| T19 | Windows `uv pip compile` lock이 마커 없이 `pywin32` 고정 → ubuntu CI 설치 실패 | `uv pip compile --universal`(sys_platform 마커) |
| T20 | CI 러너에서 pytest가 `app`/`tests` import 실패(로컬은 PYTHONPATH 우회) | `pyproject [tool.pytest.ini_options] pythonpath=["."]` |
| T21 | `search_path=ai` 단독이 public 제외 → VECTOR 타입·`register_vector`가 멀티 커넥션에서 실패 | `ai, public`으로 확장(public=vector 확장 소재, core는 경로 밖 유지) |
| T22 | `.env`가 CRLF라 셸로 값을 뽑으면 `\r`이 섞여 JSON 파싱 실패(일부 요청만 깨져 혼동) | `tr -d '\r\n'` 또는 셸 파싱 금지·`get_settings()` 사용 (T16 계열) |
| T23 | raw `asyncpg` 연결은 `register_vector` 미등록 → VECTOR가 문자열로 디코딩되어 `PresetCache` 적재 실패 | 스크립트도 `app.core.db.Database` 사용(search_path+타입 등록 캡슐화). **시딩 재발 주의** |
| T24 | Git Bash + `curl`에서 한글 본문 인코딩 깨짐(ASCII 본문은 통과 → T22와 증상 동일) | 한글 요청은 Python `httpx`로 전송, `curl`은 ASCII 경로에만 |
| T25 | 멀티세션이 단일 git 워킹트리·인덱스·HEAD 공유 → 3파일 커밋에 타 세션 15파일 섞여 push | 격리 `git worktree` 기본, `git add` 개별(`-A` 금지)·커밋 전 브랜치 확인 |
| T26 | `main.py` 모듈 레벨 `create_app()` import 시점 `.env` 캐시 → API 3건만 401(로컬 `.env` 우연 일치로 은폐) | `settings` fixture에서 `get_settings()` 캐시 재설정 + conftest placeholder env 선주입 |
| T27 | **GMS 판정 쿼터는 상수가 아니다** — 공용 게이트웨이라 시점·프로바이더 경로별로 다르다. 07-29 분당 2건 → 07-30 분당 30건 이상 | `--pace` 기본값 1. 방어는 `retry.py` 백오프 + 회수 루프. 근본 대책은 벤더 폴백(`-175`) |
| T28 | 콘솔이 cp949면 `—` 한 글자에 `UnicodeEncodeError` → **`--reset` 직후 죽어 데이터만 지워진 상태**가 됨 | `sys.stdout.reconfigure(utf-8)` + `log()` 최후 방어. 호출자가 `PYTHONIOENCODING`을 기억하지 않게 (T22·T24 계열) |
| T29 | `python -m uvicorn` 이 시스템 Python 을 타서 `No module named uvicorn` — **exit 0 이라 「완료」로 보인다** | `.venv/Scripts/python.exe -m uvicorn` |
| T30 | 백그라운드 파이프(`\| tail`)가 서버 로그를 버퍼에 가둔다 — 살아 있는 동안 0바이트 | 파이프 대신 `> file 2>&1` |
| T31 | `gms window` 가 안 나오는 것을 계측 실패로 오판 — **창은 타이머가 아니라 다음 호출이 닫는다** | 60초 뒤 1건 더 넣으면 나온다 |
| T32 | `back` jar 이 낡으면 Flyway 가 `Detected applied migration not resolved locally: 6` 으로 기동 거부 | `git pull` 뒤 `./gradlew bootJar` |
| T33 | `ai/.env` 의 `DATABASE_URL` 이 07-27 잔재 `:5433` — 시연 정본은 `:15432` | preflight 가 BLOCK 한다. 기본값은 그대로 |
| T34 | 브라우저 로컬 테스트 로그인은 `logged_in=1` **정확 일치** — `true` 는 안 걸린다 | `access_token` + `logged_in=1` + `XSRF-TOKEN` 셋을 심는다 |
| T35 | 검색이 소유자별로 갈려 계정을 잘못 잡으면 데이터가 없는 것처럼 보인다 | `social_account` 로 member 별 Context 수를 먼저 센다 |
| T36 | CI 검사를 `dev` 에만 넣으면 **`main` 기반 PR 에 적용되지 않는다**(PR CI 는 head 워크플로로 돈다) | `hotfix/*` 로 `main` 에도 올린다 |
| T37 | 「후보 임계값이 없어서 오분류가 난다」는 진단이 **틀렸다** — `_topk` 에 이미 있었고 테스트도 둘 있었다. 후보도 10개가 아니라 평균 7.4개 | 「기능이 없어서」는 grep 한 줄로 확인하고 쓴다. 실제 원인은 적합·부적합 유사도 분포의 겹침 |
| T38 | 스크립트에만 T28 방어를 넣으면 탐색용 `python -c` 한 줄에서 다시 죽는다 — **앞줄은 이미 찍혀서 완료로 보인다** | 한 줄에는 `PYTHONIOENCODING=utf-8`, 반복할 것이면 파일로 옮긴다 |
| T39 | 재판정 차이를 대조군 없이 읽으면 전부 조건 탓이 된다 — **같은 τ 로 다시 판정만 해도 Context 26% 가 흔들린다** | 판정(LLM) 계층 측정은 대조군을 같이 돌린다. 임베딩은 결정적이라 불필요 |
| T40 | **정답이 있는 질의만 재면 컷의 절반이 안 보인다** — `r` 이 무관 질의를 15건 중 0건도 침묵시키지 못한다는 사실이 검증 질의 12건에서는 드러나지 않는다 | 걸러져야 하는 입력을 표본에 넣는다. 「0건」의 부호가 축마다 반대이므로 표를 가른다 |
| T41 | worktree 에 `.env` 가 없어 `get_settings()` 가 `GMS_API_KEY` 부터 죽는다(`env_file` 은 CWD 기준·gitignore) | `.env` 를 worktree 에 복사하고 `DATABASE_URL` 만 환경변수로 덮는다. `.demo/` 키 분기(`-198`)와 같은 원인 |
| T42 | **임베딩 배치 구성이 바뀌면** 같은 텍스트의 유사도가 `10⁻⁴` 규모로 흔들린다(0.5264→0.5258). 「임베딩은 결정적」은 같은 배치일 때의 이야기다 | 그 규모 차이가 결론을 가르는 값을 채택하지 않는다. 재현용으로 유사도 행렬을 커밋한다 |
| T40 | `.env` 의 `PINLOG_JUDGE_MODEL` 은 `-175` 가 대체해 **읽히지 않는데** 값이 그럴듯해(체인 2순위) 리포트의 판정 모델을 잘못 적게 한다. 실제 응답은 체인 1순위 `gpt-4o-mini` | 측정 도구는 벤더를 인자로 받고, 읽은 값이 아니라 **답한 값**(`JudgeResult.model`)을 남긴다 |
| T41 | `labels.yaml` 은 **현행 판정 83행만** 덮는다 — τ 스윕과 달리 재판정은 없던 행을 만들어 표 밖으로 나간다(24종). 빼고 세면 조건 비교가 기운다 | 원본은 고치지 않고 `labels_extra.yaml` 로 넓힌다. 남는 것은 `unlabeled` 로 세어 양극단으로 돌린다 |
| T42 | 라벨을 붙일 때 **어느 조건이 고른 행인지 보이면** 라벨이 결론을 따라간다 — 증상이 없고 개선 폭만 부푼다 | 덤프에서 조건·회차를 뺀다. 본문과 `description` 만 보고 붙인다 |
| T43 | 사전 기준(범위 비중첩)을 못 넘으면 자를 바꾸고 싶어진다. 표본이 늘면 그 기준은 **오히려 통과하기 어려워져** 교체가 정당한데, 교체 시점이 결과를 본 뒤다 | 표본을 늘리고 자를 **더한다**(순열검정). 앞의 자를 지우지 않고 둘 다 찍는다 |
| T44 | DB 에 저장된 판정 **1회분**의 `confidence` 가 라벨을 깨끗이 가르는 것처럼 보인다(fit min 0.70 · unfit 0.30~). 한 번 더 판정받으면 분리가 사라진다 — **confidence 도 비결정적이다.** DB 조회가 공짜라 여러 번 볼 생각을 안 하게 된다 | 저장된 판정에서 발견한 문턱은 후속으로 올리기 전에 같은 조건으로 한 번 더 받아 대조한다(42회·70초). 확인용 회차는 `--outdir` 를 나눈다 |

> T9(H2·pgvector)·T10(flyway.schemas)은 백엔드 아티팩트라 **back 레포** `docs/ai/troubleshooting`에 있습니다.
