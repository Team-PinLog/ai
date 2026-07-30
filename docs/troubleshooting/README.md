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

> T9(H2·pgvector)·T10(flyway.schemas)은 백엔드 아티팩트라 **back 레포** `docs/ai/troubleshooting`에 있습니다.
