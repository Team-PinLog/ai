# WORKLOG — AI 파트

시간순 작업 로그입니다. 유형별 폴더(spec/proposals/implements/troubleshooting) 분산으로 인한 "내 작업 추적" 비용을 시간축 인덱스로 상쇄합니다. **이후 작업마다 한 줄씩 추가**합니다.

| 날짜 | 작업 | 관련 문서 |
|---|---|---|
| 2026-07-23 | AI 공용 설계를 docs `static/05` 단일 원본으로 확립 (docs#2) | [proposals](proposals/README.md) (P16) |
| 2026-07-23 | FastAPI AI 서버 구현 명세 작성 + version→deletion race 리네임 (ai#1) | [spec/](spec/) |
| 2026-07-23 | Keyword Preset seed 27개 (ai#2) | [implements](implements/2026-07-23-keyword-preset-seed.md), [spec/keyword-preset.md](spec/keyword-preset.md) |
| 2026-07-23 | architecture 구조도(Mermaid) 4종 (ai#4) | [implements](implements/2026-07-23-architecture-diagrams.md), [spec/architecture.md](spec/architecture.md) |
| 2026-07-23 | Keyword 매칭 평가 A/B/C-1 — 하한 0.30·프롬프트 확정 (test/keyword-matching-eval) | [implements](implements/2026-07-23-keyword-matching-eval.md), [P26](proposals/P26-keyword-preset-judgment.md) |
| 2026-07-23 | 작업 기록 신설 + 문서 재구조화(spec/proposals/implements/troubleshooting + WORKLOG, ADR→P) (ai#4) | 이 트리 전체 |
| 2026-07-23 | eval C-2 3사 모델 비교 완료 — 판정 모델 `gemini-2.5-flash`(thinkingBudget=0) 확정, M4 종결 (ai#3) | [implements](implements/2026-07-23-keyword-matching-eval.md), [P26](proposals/P26-keyword-preset-judgment.md) |
| 2026-07-23 | FastAPI 서버 scaffold + 개인 검색(`/search`) + Preset 부트스트랩 + 운영 비용 추정 (ai#5) | [spec/personal-search.md](spec/personal-search.md), [spec/cost-estimate.md](spec/cost-estimate.md) |
| 2026-07-23 | `/context/process` 처리 파이프라인 + 상태머신(부분 재개·저장 불변식·gemini-2.5-flash 판정) (ai#6) | [spec/context-processing.md](spec/context-processing.md), [spec/state-machine.md](spec/state-machine.md) |
| 2026-07-23 | FastAPI 구현 리포트(I19) + spec 9종 "구현 반영" 표시 갱신 (ai#7) | [implements](implements/2026-07-23-fastapi-implementation.md), [spec/](spec/) |
| 2026-07-23 | 문서 gap 마감 — eval 리포트 C-2 반영 + 구현 트러블슈팅 T16~T18 등재 (ai#8) | [implements](implements/2026-07-23-keyword-matching-eval.md), [troubleshooting](troubleshooting/2026-07-23-fastapi-local-verification.md) |
| 2026-07-24 | 파트간 요구사항 참조 `static/05-1`로 갱신 (ai#9) | [spec/](spec/) |
| 2026-07-24 | M2 종결 — Context 목록 `created_at` A안 확정(백엔드 V2~ 블로커 해소) | [proposals](proposals/README.md) |
| 2026-07-24 | `/search` 응답에 `contextId` 추가(DISTINCT ON) — Spring matchedContext 조립용, 구현+spec 동반 | [spec/personal-search.md](spec/personal-search.md) |
| 2026-07-24 | ai 레포 협업 컨벤션 이식(.github 템플릿·CI·ruff) (ai#12) | [proposals](proposals/README.md) (P15) |
| 2026-07-24 | M5 종결 — `09_유저플로우` draft/static 중복 해소 반영 (ai#13) | [proposals](proposals/README.md) |
| 2026-07-23 | E3-PR1 — 테스트 하네스(Testcontainers)+저수준 27케이스+Dockerfile+ai-ci 정비(Jira/lock)+Python 3.12 통일 | [tests/](../tests/README.md), [Dockerfile](../Dockerfile) |
| 2026-07-24 | troubleshooting·implements 기록 보존 원칙 + 상태 헤더 소급 (ai#15) | [implements](implements/README.md), [troubleshooting](troubleshooting/README.md) |
| 2026-07-24 | ai-ci 핫픽스 — lock 플랫폼 종속(pywin32) 마커 + pytest pythonpath (ai#16) | [troubleshooting](troubleshooting/2026-07-24-e3-ci-and-search-path.md), [implements](implements/2026-07-24-e3-test-harness.md) |
| 2026-07-27 | 전수 조사 갭 정합화 — 절번호 드리프트 3·인덱스·WORKLOG·proposals P40·P41·시나리오5 주석 (ai#19) | 이 트리 |
| 2026-07-27 | 문서↔코드 정합 감사 + M3 계약 개정 — spec 5종 코드 대조·정정(architecture §6.2·§5 등), M3 `COMPLETED→PENDING` 운영 재처리(state-machine + 계약 §6.3·§7.3) (ai#21·docs#13) | [spec/](spec/), [state-machine.md](spec/state-machine.md) |
| 2026-07-27 | E2E(-58) 발견 합류 — README 기동 절차(Flyway·DSN·docker run·psql) 정정 + 검색 컷오프 실측 + 판정 비결정성 명시 + implements 유형 컬럼 (ai#22) | [README](../README.md), [spec/keyword-preset.md](spec/keyword-preset.md) |
| 2026-07-27 | E3-PR2 — 파이프라인 시나리오 20개(`test_pipeline.py`, 19함수/20시나리오) (ai#18) | [spec/integration-tests.md](spec/integration-tests.md) |
| 2026-07-27 | 코드 주석 §참조·`search_path` 서술 정정 (ai#20) | [troubleshooting](troubleshooting/2026-07-24-e3-ci-and-search-path.md) |
| 2026-07-27 | E3-PR2 완료 반영(리포트·spec 헤더 갱신) + `preset_cache_ttl_sec` dead config 제거(§5 정합) (ai#24) | [implements](implements/2026-07-24-e3-test-harness.md), [spec/integration-tests.md](spec/integration-tests.md) |
| 2026-07-27 | E2E 실경로 검증(-58) — 실제 GMS 프리셋 27 적재·파이프라인 8건·검색 품질(분리도 +0.2120)·하네스 동등성 9/10·권한 경계 실증(`-61` 근거) + 검증 드라이버 `tools/e2e/` | [implements](implements/2026-07-27-e2e-verification.md), [troubleshooting](troubleshooting/2026-07-27-e2e-env-issues.md) (T22~T24), [tools/e2e/](../tools/e2e/) |
| 2026-07-28 | ai#25(인프라 CI 계약 테스트 6) 병합으로 pytest 46→52 확대 → 문서 정합(수치 AI 검증 46 + CI 계약 6·CI 계약 각주·pgvector 불일치 표시) (ai#28) | [tests/README](../tests/README.md), [spec/integration-tests.md](spec/integration-tests.md) |
| 2026-07-28 | S1 구현 판단 맥락 복원 — 설계선택 19·불변식·구현결함 불일치·인프라 미복원(I22), 워킹트리·env캐시(T25·T26), 판단변경·기각(P43), partial-resume §3 재조회 판단변경 (ai#29) | [implements](implements/2026-07-28-s1-implementation-recovery.md), [troubleshooting](troubleshooting/2026-07-28-shared-worktree-and-env-cache.md), [P43](proposals/P43-s1-judgment-recovery.md) |
| 2026-07-28 | AI 레포 협업 운영 기준과 Jira→PR 리뷰 절차 수립, `app` branch coverage 비차단 측정 도입 (S15P11A705-108) | [CONTRIBUTING](../CONTRIBUTING.md), [P44](proposals/P44-ai-repository-governance.md), [development](development/) |
| 2026-07-29 | dev 배포 게이트 3종(I23) — `GET /ready`(DB `SELECT 1` + Preset ≥1, GMS 미호출) · `GMS_BASE_URL` `/gmsapi/` 기동 fail-fast(값 미노출 위해 `SettingsError`) · `app.smoke.gms_roundtrip` 양방향 실호출, pytest 52→66 (ai#33 ← [ai#32](https://github.com/Team-PinLog/ai/pull/32) 인프라 요청) | [implements](implements/2026-07-29-dev-deployment-gates.md), [README](../README.md) |
| 2026-07-29 | AI 소유 값의 클러스터 전달 경로를 만들었다(I24). GitHub Actions Secret 은 Pod 에 자동 전달되지 않으므로 `kubeseal --raw` 로 값 7종을 개별 봉인해 `encryptedData` 만 artifact 로 넘긴다 — `kubectl create secret --dry-run` 경로는 중간에 평문 base64 YAML 을 만들어 쓰지 않았다. **EMBEDDING 넷은 비밀이 아니지만**(정본 P32) Infra 가 주입 경로를 하나로 요구해 같은 경로로 다룬다. 앱의 기동 검사(`/gmsapi/` 형식·profile 정합)를 봉인 시점으로 앞당겨 배포 전에 실패시키고, controller 인증서는 SHA-256 지문으로 고정했다 — 엉뚱한 공개키로 봉인하면 복호화 실패가 배포 시점에야 드러난다. **실제 봉인은 미실행**(Actions Secret 등록 후 가능) (S15P11A705-96) | [handoff 리포트](implements/2026-07-29-sealed-secret-handoff.md), [ai#32](https://github.com/Team-PinLog/ai/pull/32) |
| 2026-07-29 | 봉인 workflow 의 kubeseal 설치가 체크섬 검증에서 죽던 것을 고쳤다. `curl -o kubeseal.tar.gz` 로 받아 놓고 manifest 는 `kubeseal-0.27.1-linux-amd64.tar.gz` 를 가리켜, `sha256sum -c` 가 manifest 에 적힌 이름으로 파일을 열다 실패했다 (`No such file or directory` / `FAILED open or read`). 다운로드·검증·해제가 같은 변수를 쓰도록 파일명을 하나로 묶었다. **Infra 가 실제로 실행해서 발견했다** — 병합 전 검증이 YAML 파싱과 `bash -n` 까지였고 그 둘은 이 결함을 잡지 못한다. 이번엔 실제 다운로드·검증·해제를 로컬에서 돌려 통과를 확인했고, 원 버전이 같은 오류로 죽는 것도 재현했다 (S15P11A705-96) | [handoff 리포트](implements/2026-07-29-sealed-secret-handoff.md), [ai#32](https://github.com/Team-PinLog/ai/pull/32) |
