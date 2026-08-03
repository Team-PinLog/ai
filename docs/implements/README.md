# 구현 리포트 (Implements)

무엇을 만들었고 어떻게 검증했는지 기록합니다. `spec/`이 "무엇을 만들 것인가"라면, 여기는 "어떻게 만들었나"와 검증 결과입니다.

## 보존 원칙

이 폴더는 구현 이력을 기록합니다. **완료된 항목도 삭제하지 않고 상태 표시만 갱신합니다.** 회고·복기에서 "무엇을 어떻게 만들었는가"를 추적하기 위함입니다.

- 완료 → 문서 유지 + `상태: 완료`
- 무효화 → 문서 유지 + `상태: 무효(사유)` 표기
- 삭제 → 하지 않음. 잘못 작성된 문서도 정정으로 처리

※ `spec/`은 현재 유효한 명세이므로 이 원칙의 대상이 아닙니다(낡은 내용은 갱신·삭제).

## 개별 리포트

| 번호 | 문서 | 유형 | 내용 |
|---|---|---|---|
| I9 | [2026-07-23-keyword-preset-seed.md](2026-07-23-keyword-preset-seed.md) | 구현 | Keyword Preset 27개 산출·검증 (ai#2) |
| I10 | [2026-07-23-architecture-diagrams.md](2026-07-23-architecture-diagrams.md) | 구현 | architecture.md 구조도(Mermaid) 4종 (ai#4) |
| I14 | [2026-07-23-keyword-matching-eval.md](2026-07-23-keyword-matching-eval.md) | 검증 | Keyword 매칭 평가 A/B/C 요약·포인터 (판정 모델 gemini-2.5-flash 확정) |
| I19 | [2026-07-23-fastapi-implementation.md](2026-07-23-fastapi-implementation.md) | 구현 | FastAPI scaffold + /context/process + /search 구현·검증 (ai#5·#6) |
| I20 | [2026-07-24-e3-test-harness.md](2026-07-24-e3-test-harness.md) | 구현 | E3 통합 테스트 하네스 + 저수준 27케이스 + 파이프라인 20 + Dockerfile + ai-ci 정비 (ai#14·#16·#18) |
| I21 | [2026-07-27-e2e-verification.md](2026-07-27-e2e-verification.md) | 검증 | E2E 실경로 — 실제 GMS 프리셋 적재·파이프라인·검색 품질·하네스 동등성·권한 경계 |
| I22 | [2026-07-28-s1-implementation-recovery.md](2026-07-28-s1-implementation-recovery.md) | 구현 | S1 세션 구현 판단 맥락 복원 — 설계선택 19·불변식·spec↔구현 불일치(구현결함)·인프라 미복원 |
| I23 | [2026-07-29-dev-deployment-gates.md](2026-07-29-dev-deployment-gates.md) | 구현 | dev 배포 게이트 3종 — `/ready`·`GMS_BASE_URL` fail-fast·GMS 양방향 스모크 (ai#33) |
| I25 | [2026-07-29-demo-seeding.md](2026-07-29-demo-seeding.md) | 구현 | 데모 시딩 — back API 경로 시딩(`tools/demo_seed/`)·GMS 건수 판단·E2E 재확인 (S15P11A705-58) |
| I41 | [2026-07-30-retry-and-error-classification.md](2026-07-30-retry-and-error-classification.md) | 구현 | 외부 API 재시도·오류 분류 정합화 — 429/LLM 4xx 오분류 정정·짧은 재시도·오류 경로 테스트 (S15P11A705-121) |
| I26 | [2026-07-30-coverage-gate.md](2026-07-30-coverage-gate.md) | 구현 | app coverage 게이트 활성화 — line·branch 각각 80% 차단·부트스트랩/기동 계층 신설·§4.2 계층 구분 명문화 (S15P11A705-110) |
| I24 | [2026-07-29-sealed-secret-handoff.md](2026-07-29-sealed-secret-handoff.md) | 구현 | Runtime Secret handoff — Environment 경계 계약·공급망 pin (S15P11A705-154). **상태: 대체** — 봉인 실행은 Infra 공용 action으로 이관, `S15P11A705-96` 판 설계 근거는 같은 문서에 보존 |
| I43 | [2026-07-30-real-data-e2e.md](2026-07-30-real-data-e2e.md) | 검증 | 실사용자 데이터 E2E — 검색 10/12·피드·Keyword PASS, 시딩 15분 8초·37건, GMS 32,912 토큰 (S15P11A705-174) |
| I44 | [2026-07-30-judge-vendor-fallback.md](2026-07-30-judge-vendor-fallback.md) | 구현 | 판정 LLM 벤더 폴백 — 429가 프로바이더 경로별로 걸린다는 실측·어댑터 3종·시도 예산 공유·응답 벤더 기록 (S15P11A705-175) |
| 없음 | [2026-07-31-ticket-audit-96-77.md](2026-07-31-ticket-audit-96-77.md) | 감사 | 티켓 대조 — `-96` 완료 조건 5개·`-77` 정정 요청 8개를 `ai`·`infra`·`docs` 실물과 대조해 해소/미해소/무효 판정. 둘 다 닫을 수 있음 (S15P11A705-96·-77). **번호 없음** — 이 문서 자신이 "구현 기록이 아니라 대조·판정 기록이다. 만든 것이 아니라 확인한 결과"라고 명시한다(전수 표는 산출을 세는 곳이다) |
| I27 | [2026-07-31-embedding-grid.md](2026-07-31-embedding-grid.md) | 검증 | 임베딩 4조건 실경로 측정 — 입력 구성 × 모델 교차. 1위 일치 A 10 · B 9 · C 10 · D 10 / 12, top-3 넷 다 12/12. `-174` 의 8번 진단이 틀렸음을 B·C 대비가 보인다 (S15P11A705-191) |
| I28 | [2026-07-31-gms-call-observability.md](2026-07-31-gms-call-observability.md) | 구현 | GMS 호출·재선점 로그 계측 — 호출 1회당 벤더·모델·상태·결과 분류·지연, 60초 창 실패율, 만료 `PROCESSING` 재선점. httpx 가 요청 URL 을 INFO 로 흘리던 것을 함께 차단 (S15P11A705-197) |
| I39 | [2026-07-31-seed-guard.md](2026-07-31-seed-guard.md) | 구현 | 시연 도구 결함 3건 — 쓰기 컬럼 계약·고아 집계·JWT 키 실검증을 `--reset` 앞에 두는 preflight. 셋 다 어긋내 RED 확인 (S15P11A705-198) |
| I30 | [2026-07-31-candidate-threshold.md](2026-07-31-candidate-threshold.md) | 검증 | 후보 유사도 임계값 τ 재검증 — 현행 `0.30` 유지. `fit min 0.3001` 과 `unfit max 0.4225` 가 겹쳐 τ 로는 「붙여도 되는가」를 가를 수 없다 |
| I29 | [2026-07-31-search-cut.md](2026-07-31-search-cut.md) | 검증 | 검색 결과 컷 `τ_abs=0.30 · r=0.60` — 정답 누락 0/12 · 빈 결과 0/12 · 꼬리 76.3% 제거 · 무관 질의 11/15 침묵. **무관 질의를 1건에서 15건으로 늘리자 §6 의 「간격 +0.2120」이 -0.0176 으로 뒤집혀** 컷 미적용 판단을 개정 (S15P11A705-213) |
| I40 | [2026-07-31-judge-prompt-rule.md](2026-07-31-judge-prompt-rule.md) | 검증 | 판정 프롬프트 「본문에 근거 없으면 미선택」 개정안 둘 — **둘 다 채택하지 않는다.** 오분류 감소가 같은 프롬프트를 다시 돌렸을 때의 변동을 넘지 못했고, 사용자가 보는 손실(`fit 0건 Context` 8.00)은 세 조건이 같다 (S15P11A705-219) |
| I31 | [2026-07-31-search-error-contract.md](2026-07-31-search-error-contract.md) | 구현 | 검색 API 오류 응답 계약 — `TransientError→503` · `PermanentError→502`, 500 을 「우리 코드의 결함」으로 비워 둔다. 운영 버그 `ai#69`(임베딩 502 → 검색 500). **`back` 은 500·503 을 구분하지 않으므로 바뀌는 것은 사용자 화면이 아니라 관측** (S15P11A705-220) |
| I34 | [2026-07-31-gms-error-body-redaction.md](2026-07-31-gms-error-body-redaction.md) | 구현 | 게이트웨이 오류 본문 마스킹 — 응답 본문 200자가 예외 메시지를 타고 **다섯 곳의 로그와 트레이스백**으로 나가던 경로를 원천에서 막는다. 실제 GMS 로 네 경로에 오류 19건을 넣어 실측: **자격 증명은 한 건도 에코되지 않고**, endpoint 는 맨 호스트로 실리며, **OpenAI 는 요청 값을 앞뒤 3자만 남기고 잘라 되돌린다** (S15P11A705-205) |
| I32 | [2026-07-31-db-error-classification.md](2026-07-31-db-error-classification.md) | 구현 | DB 실패의 오류 분류 — `-220` 이 남긴 500 을 메운다. SQLSTATE 군 단위 경계, **미분류(500) 목록이 분류 목록만큼 중요**. `-220` 의 핸들러를 고치지 않고 하위 타입으로 받는다 (S15P11A705-221) |
| I33 | [2026-07-31-judge-vote.md](2026-07-31-judge-vote.md) | 구현 | 판정 n회 다수결 `PINLOG_JUDGE_VOTE_N` — 비결정성 24%→12% · 흔들리던 오분류 13종 완전 제거 · 정상 판정 손실 0. **그런데 오분류 행은 거의 안 준다**(10.13→9.17, p=0.210) — 다수결은 소수의견을 지우는 대신 다수의견을 굳힌다. **n=1 유지** (S15P11A705-223) |
| I35 | [2026-07-31-docs-index-check.md](2026-07-31-docs-index-check.md) | 구현 | 문서 색인 정합을 `ai-ci / check` 로 옮긴다 — 번호 중복 · 고아/누락 둘만. 07-31 사고 1·2·3 을 실제 `docs/` 사본으로 재현해 잡는 것과, **결번·두 표 불일치를 통과시키는 것**을 함께 고정. 착수 시점 `dev` 위반 3건 정리. 표 이중화는 **줄일 수 없다**(두 표의 집합이 다르다) (티켓 없음) |
| I36 | [2026-08-03-gms-vision-probe.md](2026-08-03-gms-vision-probe.md) | 검증 | GMS 게이트웨이 멀티모달(이미지) 지원 재고 — **지원한다.** 1×1 PNG 를 세 벤더 스펙(OpenAI·Gemini·Anthropic) 그대로 실어 각 1회씩 총 3호출, 셋 다 200 이고 이미지 내용에 실제로 답함. `back#138` 이 이미지 분석 흐름의 유일한 선행 조건으로 남긴 것을 해소 (S15P11A705-227) |
| I42 | [2026-08-03-docs-index-oneway.md](2026-08-03-docs-index-oneway.md) | 구현 | 구현 리포트 파일 표에 번호 컬럼을 넣어 반대 방향 누락(⑤)을 잡는다 — `-226` 이 한 방향만 켜고 남긴 구멍. 번호 없던 7건 판정(5건 신규 번호·1건 기존 I30 링크 보완·1건 「없음」 명시), 병합 중 실제로 난 번호 충돌(T56)도 재현·해소 (S15P11A705-230) |
| I37 | [2026-08-03-dead-config-keys.md](2026-08-03-dead-config-keys.md) | 구현 | 죽은 설정 키 전수조사 — `PINLOG_JUDGE_MODEL`(`-175` 가 대체)·`PRESET_CACHE_TTL_SEC`(`ai#24` 가 이미 제거) 둘 다 저장소는 이미 정리돼 있었고 개발자 로컬 `.env` 잔재만 남아 있었음을 런타임 sentinel 주입으로 확인(grep 만으로 끝내지 않음). 배포 Secret(8키) 은 애초에 둘 다 담지 않음. `.env` 13 vs `.env.example` 12 는 단일 차집합이 아니라 양방향(2:1)이었고 둘 다 정상이라 example 변경 불필요. `-197` 계측이 벤더·모델을 이미 로그로 남김을 확인 — 보강 불필요 (S15P11A705-224) |
| I38 | [2026-08-03-error-wording-split.md](2026-08-03-error-wording-split.md) | 구현 | 오류 응답 문구 분리 — `-221` 이 상태 코드·로그는 맞혔지만 본문은 `embedding upstream ...` 그대로였다. `DatabaseTransientError`/`DatabasePermanentError` 를 보고 `database unavailable`/`database rejected the request` 로 가르되 원인 값은 여전히 안 싣는다. `static/05` 는 문구를 소유하지 않고 `back` 의 `AiSearchClient.translate` 가 본문을 안 읽어 **반영하지 않음** (S15P11A705-229) |
| I45 | [2026-08-03-preset-description.md](2026-08-03-preset-description.md) | 검증 | 프리셋 `description`·`examples` 개정 3조건 측정 — **`examples` 만 채택(`E`), `description` 개정은 기각.** 네 티켓 만에 처음 채택한다. 두 필드가 들어가는 곳이 달라(`examples` 는 임베딩만, `description` 은 임베딩+판정 프롬프트) 갈라 쟀고, 갈라 재지 않았으면 `D` 의 해로움이 `DE` 안에 묻혔다. `E` 오분류 11.00→6.90행(p=0.0001)·정상 손실 -0.80(p=0.16)·교환비 0.20·**순이득 부호가 양극단 모두 양수**(네 티켓 중 유일). `D` 는 `fit 0건 Context` 8.20→10.00 으로 **악화**(범위 분리)하고 안 고친 22종이 정상 -2.50 를 치른다. 자기충족 방어 셋(라벨 무관 수정 원칙·안 고친 22종 대조·라벨 밖 홀드아웃 30건). T68(임베딩 비결정성)·T69(τ 격자가 rank 로 밀린 행을 안 셈) 발견·우회 (S15P11A705-228) |

> **유형**: 구현(무엇을 만들었나) / 검증(어떻게 검증했나) / 감사(티켓·문서가 실물과 맞는가). 검증 성격 문서가 늘면 이 컬럼이 분류 기준이 된다.
> **분리 트리거**: 리포트가 15개를 넘고 검증 유형이 절반 이상이면 `verification/` 분리를 검토한다.

## 구현·산출 — 전수 (AI 소유)

| I | 산출 | 반영처 |
|---|---|---|
| I1 | AI 공용 설계 단일 원본 `static/05_AI_설계.md`(836줄, 21 테스트 시나리오) | docs#2 |
| I2 | `static/05-1` 파트간 요구사항(front/infra) | docs#3 |
| I3 | API 상세명세 `draft/11`(디자인 화면→엔드포인트) | docs#4·#5 |
| I4 | AI 구현 명세 10문서 | [spec/](../spec/) |
| I5 | `version-race-control` → `deletion-race-control` 리네임·재작성 | [spec/deletion-race-control.md](../spec/deletion-race-control.md) |
| I9 | Keyword Preset seed 27개 | [preset-seed 리포트](2026-07-23-keyword-preset-seed.md) |
| I10 | architecture 구조도 4종 | [구조도 리포트](2026-07-23-architecture-diagrams.md) |
| I11 | 세 PR 초안(docs/ai/back 제목·본문·리뷰포인트) | docs#2·ai#1·back#1 |
| I12 | MINYONG 공유 코멘트(결정 4건) | docs#2 |
| I13 | eval 하네스 A/B/C (`tools/keyword_eval/`) | `test/keyword-matching-eval` |
| I14 | eval REPORT(A/B/C-1) — 보정 불필요·프롬프트 확정·하한 0.30 | [eval 리포트](2026-07-23-keyword-matching-eval.md) |
| I15 | `/search` 응답 `contextId` 추가(DISTINCT ON, Spring matchedContext 조립) — **소규모 변경이라 전용 리포트 없이 인벤토리만** | ai#11·docs#10, [P40](../proposals/README.md), [spec/personal-search.md](../spec/personal-search.md) |
| I16 | AI 작업기록 문서(구조도+ADR 4+트러블슈팅+리포트 5) | 이 트리 |
| I17 | 문서화 규약 메모리 | (로컬 메모리) |
| I18 | 누적 계획 파일 | (로컬 plans) |
| I19 | FastAPI 구현(scaffold + `/context/process` + `/search` + Preset 부트스트랩) | [FastAPI 리포트](2026-07-23-fastapi-implementation.md) |
| I20 | E3 통합 테스트 하네스 + 저수준 27 + 파이프라인 20 + Dockerfile + ai-ci 정비(Python 3.12·lock·Jira 검증) | [E3 리포트](2026-07-24-e3-test-harness.md), PR ai#14·#16·#18 |
| I21 | E2E 실경로 검증 + 검증 드라이버(`tools/e2e/`) — 문서 마찰 F1·F2·F5·F6, 하네스 동등성 실측, 권한 경계 실증(`-61` 근거), 시딩 가능 범위 | [E2E 리포트](2026-07-27-e2e-verification.md), [tools/e2e/](../../tools/e2e/) |
| I22 | S1 구현 판단 맥락 복원 — 설계선택 19·불변식·spec↔구현 불일치(구현결함 A·F절)·실행 인프라 미복원 | [S1 복원 리포트](2026-07-28-s1-implementation-recovery.md) |
| I23 | dev 배포 게이트 3종 — `GET /ready`(DB+Preset, GMS 미호출) · `GMS_BASE_URL` `/gmsapi/` fail-fast · `app.smoke.gms_roundtrip`(embedding+judge 실호출, 한쪽 실패 시 exit 1) | [배포 게이트 리포트](2026-07-29-dev-deployment-gates.md), ai#33 ← [ai#32](https://github.com/Team-PinLog/ai/pull/32) 요청 |
| I24 | Runtime Secret handoff — 평문 없이 Infra 에 전달. `S15P11A705-96` 판은 Actions Secret 7종을 `kubeseal --raw` 로 직접 봉인(`pinlog-dev/ai-owner-secrets`, scope strict), `S15P11A705-154` 에서 Environment 경계 + Infra 공용 action 으로 이관하고 앱 Secret 3종으로 축소. **설계 근거는 문서에 보존** | [handoff 리포트](2026-07-29-sealed-secret-handoff.md) ← [ai#32](https://github.com/Team-PinLog/ai/pull/32) 요청 ① |
| I26 | app coverage 게이트 `tools/check_coverage_gate.py` — line·branch 를 **따로** 판정(합산 비율은 statement 수에 가려 branch 미달을 통과시킨다), 임계값은 스크립트 상수라 CI 인자로 덮을 수 없음. 부트스트랩·기동 계층 테스트 신설(둘 다 기준선 0%·58%), 146→181 tests, line 88.80→99.74% · branch 82.08→98.11%. RED 4종 실측 | [게이트 리포트](2026-07-30-coverage-gate.md), [tools/check_coverage_gate.py](../../tools/check_coverage_gate.py) |
| I25 | 데모 시딩 도구 `tools/demo_seed/` — back API 경로로 member 5·Context 14·Collection 9 생성, `--reset` 재현, `verify.py` 시연 3종 판정. GMS 429(분당 약 2건) 실측과 그에 맞춘 회수 루프 | [데모 시딩 리포트](2026-07-29-demo-seeding.md), [tools/demo_seed/](../../tools/demo_seed/) |
| I27 | 임베딩 4조건 측정 하네스 `tools/emb_grid/` — 조건 정본 하나(`conditions.py`)를 셸에 `eval` 로 넘겨 값이 갈라지지 않게 하고, 조건이 환경과 어긋나면 **재기 전에 멈춘다**(profile·차원·프리셋 적재 여부·FastAPI 도달). `alter_dim.py` 는 로컬 DB 차원을 바꾸고 되돌린다 — Flyway 를 만들지 않는 것이 이 측정의 계약이다 | [4조건 측정](2026-07-31-embedding-grid.md), [tools/emb_grid/](../../tools/emb_grid/) |
| I28 | GMS 호출·재선점 로그 계측 — 호출 1회당 벤더·모델·상태·결과 분류·지연, 60초 창 실패율 집계, 만료 `PROCESSING` 재선점. `_usage.py`(토큰)와 합치지 않은 근거와 `try_start` CTE 재작성. **httpx 가 요청 URL 을 INFO 로 흘리던 것을 함께 차단** | [GMS 호출 관측](2026-07-31-gms-call-observability.md), [failure-recovery §2.4](../spec/failure-recovery.md) |
| I29 | 검색 결과 컷 하네스 `tools/search_cut/` — 질의를 한 번 임베딩해 굳히고 `τ_abs × r` 격자를 오프라인으로 훑는다(GMS 배치 1회). **정답이 없는 무관 질의 15건을 별도 축으로 둔다** — 검증 질의만 재면 `r` 이 무관 질의를 침묵시키지 못한다는 사실이 보이지 않는다. `verify_live.py` 가 실서버와 27/27 정확 일치를 확인(`-210` 과 달리 근사가 아니라 대조군 불필요) | [컷 측정](2026-07-31-search-cut.md), [tools/search_cut/](../../tools/search_cut/) |
| I30 | 후보 임계값 τ 측정 하네스 `tools/tau_grid/` — 42×27 유사도 행렬을 한 번 떠서 임의의 τ 를 **GMS 호출 없이 재구성**한다. 라벨 83행(`labels.yaml`)이 채점 기준이고 `unclear` 를 따로 둬 낙관·비관 양 끝을 함께 낸다 | [τ 재검증 리포트](2026-07-31-candidate-threshold.md), [tools/tau_grid/](../../tools/tau_grid/) |
| I41 | 외부 API 재시도·오류 분류 정합화 `app/core/errors.py`(`classify_http_status` 단일화)·`app/client/retry.py`(`RetryPolicy`) — 두 클라이언트가 상태 코드를 정반대로 분류하던 구현 결함(429→Transient, LLM 400/401/403→Permanent)을 고쳤다. `SchemaViolationError` 로 구조화 출력 위반을 하위 타입 처리, `PermanentError → _fail()` 결선 추가 (S15P11A705-121) | [재시도·오류 분류 리포트](2026-07-30-retry-and-error-classification.md) |
| I43 | 실사용자 데이터 E2E 검증 — 가공 14건 + 실사용자 기록 23건 = 37건 통합 경로. 검색 정확도 10/12(83.3%) · 탐색 피드 PASS · Keyword PASS, 시딩 42초 · 토큰 32,912(판정이 임베딩의 17배) (S15P11A705-174) | [실데이터 E2E 리포트](2026-07-30-real-data-e2e.md) |
| I44 | 판정 LLM 벤더 폴백 `app/client/vendors.py`(신설)·`PINLOG_JUDGE_CHAIN` — 429가 프로바이더 경로별로 걸린다는 실측 위에 벤더 어댑터 3종(OpenAI·Gemini·Anthropic)을 두고 시도 예산을 공유, 실제로 답한 벤더를 `model_profile`에 기록 (S15P11A705-175) | [벤더 폴백 리포트](2026-07-30-judge-vendor-fallback.md) |
| I39 | 시연 도구 결함 3건 방어 `tools/demo_seed/preflight.py`(신규) — 쓰기 컬럼 계약 대조·미적용 마이그레이션 차단·JWT 키 실검증을 `--reset` 앞에 두는 preflight. 셋 다 일부러 어긋내 RED 확인 (S15P11A705-198) | [시연 도구 결함 리포트](2026-07-31-seed-guard.md), [tools/demo_seed/preflight.py](../../tools/demo_seed/preflight.py) |
| I40 | 판정 프롬프트 「본문에 근거 없으면 미선택」 개정안 둘 — 둘 다 채택하지 않는다. 오분류 감소가 같은 프롬프트를 다시 돌렸을 때의 변동을 넘지 못했고, 사용자가 보는 손실(`fit 0건 Context`)은 세 조건이 같다 (S15P11A705-219) | [판정 프롬프트 리포트](2026-07-31-judge-prompt-rule.md) |
| I31 | 검색 API 오류 응답 계약 — `app/main.py` 예외 핸들러 2종과 그 계약 테스트 `tests/test_api_error_contract.py`(MockTransport→실제 client→router→응답을 한 요청으로 관통). 분류·재시도는 `-121` 이 이미 맞혔고 **비어 있던 것은 예외가 응답이 되는 지점**이었다. 로컬 스텁으로 502 를 만들어 `origin/dev` 500 ↔ 이 브랜치 503 을 대조 | [오류 응답 계약](2026-07-31-search-error-contract.md), [failure-recovery §2.5](../spec/failure-recovery.md), [ai#69](https://github.com/Team-PinLog/ai/issues/69) |
| I32 | DB 실패의 오류 분류 `app/core/db_errors.py` — `-220` 이 남긴 항목. **접속 실패는 `asyncpg` 예외가 아니라 stdlib `OSError`** 라서 티켓 문구대로 asyncpg 만 분류했으면 본체를 놓쳤다(T53). 경계는 *"서버·연결의 상태 때문인가, 우리가 보낸 질의 때문인가"* 한 줄이고 **미분류(500) 목록이 분류 목록만큼 중요**하다. 분류를 세션 경계(`db.py`)에 걸어 획득·질의를 함께 덮고, 하위 타입(`DatabaseTransientError`)으로 `-220` 의 핸들러를 그대로 재사용. 전용 컨테이너를 `docker stop` 해 `origin/dev` 500 ↔ 이 브랜치 503 을 대조 | [DB 오류 분류](2026-07-31-db-error-classification.md), [failure-recovery §2.5](../spec/failure-recovery.md), [T53~T55](../troubleshooting/2026-07-31-db-error-pitfalls.md) |
| I33 | 판정 n회 다수결 `PINLOG_JUDGE_VOTE_N` + 하네스 `tools/judge_vote/` — 엄격 다수결(`votes*2 > n`), **분모는 성공 수가 아니라 n**(낮추면 n 을 켠 채 n=1 이 실행된다), 정족수 미달이면 저장하지 않고 `PROCESSING` 유지, 짝수 n 은 기동 차단(바로 아래 홀수에 지배당한다). 측정은 **새로 부르지 않고 접는다** — 회차 30개(1,260호출)를 n=1/3/5 로 묶어 2,940호출어치를 얻고, 다수결 규칙은 서비스 코드(`judge_vote.combine`)를 그대로 부른다. `run_live.py` 가 실경로 대조. **다수결은 작동했다**(비결정성 24%→12%, 흔들리는 오분류 13종 완전 제거, 정상 판정 손실 0 — 교환비 처음으로 음수) **그런데 오분류 행은 안 준다**(10.13→9.17, 범위 겹침, p=0.210) — 지운 13종이 원래 드물었고 동시에 70~97%이던 7종을 **100%로 굳혔기** 때문. 남은 7종은 `-219` 가 프롬프트로 못 움직인 것과 **같은 목록**이라 판정 층이 아니라 프리셋 `description` 축(`back#136`). `fit 0건 Context` 8.00 — 세 티켓 연속 불변. **n=1 유지** | [다수결 측정](2026-07-31-judge-vote.md), [T57~T60](../troubleshooting/2026-07-31-judge-vote.md), [tools/judge_vote/](../../tools/judge_vote/) |
| I35 | 문서 색인 정합 검사 `tools/check_docs_index.py` — 전수 표의 `T##`·`I##` 중복, 파일 표 ↔ 파일 시스템의 고아/누락, **전수 표에만 있고 파일 표에 없는 문서**(사고 5)를 `ai-ci / check` 스텝으로 판정. **연속성은 검사하지 않는다**(T9·T10 은 back 레포에 있고 결번은 정상) **두 표가 일치하는지도 검사하지 않는다 — 누락만 본다**(표 이중화가 07-31 사고 3 의 *원인*이라 문구까지 맞추라고 하면 문제를 규칙으로 승격시킨다. 구조가 해소되면 이 검사는 지운다). **누락 검사는 한 방향뿐이다** — 반대 방향(파일 표에 있는데 전수 표가 안 가리킨다)은 트러블슈팅 전수 표가 설계상 문서를 안 가리켜 정상 문서 22건이 위반으로 나온다. 셸이 아니라 Python 인 것은 `pytest` 가 같은 함수를 불러 CI 와 로컬이 같은 코드를 쓰기 위함. 새 잡이 아니라 `check` 안의 스텝인 것은 필수 상태 검사가 두 이름뿐이라 잡을 늘리면 실패해도 병합을 못 막기 때문. 메시지는 **다음 빈 번호와 붙여 넣을 표 행까지** 준다. 착수 시점 `dev` 위반 3건(고아) + 표 렌더링 6곳 정리. 조사 결과 **표 이중화는 축소 불가** — 트러블슈팅 전수 표는 문서 링크가 0종이고 구현 전수 표는 파일 표 24개 중 17개만 덮으며 13행은 문서가 아닌 산출이다 | [색인 검사 리포트](2026-07-31-docs-index-check.md), [T64·T65](../troubleshooting/2026-07-31-docs-index-check.md), [tools/check_docs_index.py](../../tools/check_docs_index.py) |
| I34 | 게이트웨이 오류 본문 마스킹 `app/core/redact.py` — 응답 본문 200자가 예외 메시지를 타고 **다섯 곳의 로그와 트레이스백**으로 나가던 것을 원천에서 막았다. 실제 GMS 로 네 경로에 오류 19건을 넣어 본문을 실측 — **자격 증명은 한 건도 에코되지 않았고**(401 은 게이트웨이 고정 문구) endpoint 는 맨 호스트로 실리며, **OpenAI 는 요청 값을 앞뒤 3자만 남기고 잘라 되돌린다**(T57). 그래서 규칙은 「관측된 것을 지운다」가 아니라 「되돌아올 수 있는 자리를 막는다」다. 자격 증명이 endpoint 보다, 마스킹이 절단보다 먼저다. 본문은 지우지 않는다 — 실측한 벤더 400 본문에 마스킹 대상이 한 글자도 없었다 | [오류 본문 마스킹](2026-07-31-gms-error-body-redaction.md), [failure-recovery §2.6](../spec/failure-recovery.md), [T57~T59](../troubleshooting/2026-07-31-log-redaction-pitfalls.md) |
| I36 | GMS 게이트웨이 멀티모달 재고 — `back#138` 이 이미지 분석 흐름(`Front → Spring → FastAPI`)의 유일한 선행 조건으로 남긴 것. 1×1 PNG 를 OpenAI·Gemini·Anthropic 세 경로에 벤더 스펙 그대로(각 1회, 총 3호출) 실어 **지원한다**로 확정 — 셋 다 200 이고 이미지 내용에 실제로 답함(빈 응답·거부 아님). `-205` 의 실측 절차(작은 요청으로 크기 요인 배제, 자격 증명 마스킹 안전망)를 그대로 재사용 | [비전 재고](2026-08-03-gms-vision-probe.md), [back#138](https://github.com/Team-PinLog/back/issues/138) |
| I42 | 구현 리포트 파일 표 번호 컬럼 `docs/implements/README.md` + 단방향 검사 ⑤ `tools/check_docs_index.py` — `-226` 이 "④는 한 방향뿐"이라 명시적으로 남긴 반대 방향(파일 표에 번호가 있는데 전수 표에 없다)을 구현 리포트에서만 켰다. 번호 없던 7건 중 5건 신규 번호(I37~I41)·1건 기존 I30 링크 보완(`candidate-threshold`)·1건 「없음」 명시(`ticket-audit-96-77`, 문서 자신이 "산출이 아니라 확인 결과"라고 규정). 트러블슈팅은 그대로 — 전수 표가 설계상 문서를 안 가리켜 컬럼을 넣어도 대조 출처가 못 된다. 병합 중 병렬 PR(`#81`)과 I36 충돌이 실제로 나 T56 그대로 재현·해소 | [단방향 검사 리포트](2026-08-03-docs-index-oneway.md), [tools/check_docs_index.py](../../tools/check_docs_index.py) |
| I37 | 죽은 설정 키 전수조사 — `.env`·`.env.example`·`config.py`·배포 SealedSecret(8키) 대조 + 15개 `Settings` 필드 전부 런타임 sentinel 주입으로 반영 확인. **읽히지 않는 키 2개**(`PINLOG_JUDGE_MODEL`·`PRESET_CACHE_TTL_SEC`) 는 저장소는 이미 정리돼 있었고 로컬 `.env` 잔재만 남아 있었다. `.env` 13 vs `.env.example` 12 전제 재확인 — 단일 차집합이 아니라 양방향(2:1)이고 둘 다 정상이라 example 변경 불필요. `-197` 계측이 벤더·모델을 이미 로그로 남김을 확인 | [죽은 설정 키 리포트](2026-08-03-dead-config-keys.md), [T66·T67](../troubleshooting/2026-08-03-dead-config-key-audit.md) |
| I38 | 오류 응답 문구 분리 `app/main.py` — `-221` 이 남긴 한계. 두 핸들러 안에서 `isinstance(exc, DatabaseTransientError\|DatabasePermanentError)` 하나로 `database ...`/`embedding upstream ...` 를 가른다. 새 핸들러도 상태 코드 변경도 없다. `static/05_AI_설계.md` 에는 이 문구 자체가 없고(포인터만 있음) `back` `AiSearchClient.translate` 가 `502`/`503` 본문을 상태 코드만 보고 버리는 것을 직접 확인해 **공용 계약 미반영**으로 판단 | [오류 문구 분리 리포트](2026-08-03-error-wording-split.md), [failure-recovery §2.5 「응답 본문」](../spec/failure-recovery.md) |
| I45 | 프리셋 `description`·`examples` 개정 측정 `tools/preset_desc/`(6스크립트) + `data/keyword_preset.yaml` 5종 `examples` 개정 — **`E`(examples 만) 채택 · `D`(description 만) 기각 · `DE` 보류.** `-210`(τ)·`-219`(프롬프트)·`-223`(다수결)이 셋 다 같은 오분류 7행을 가리키고 끝난 뒤 **네 번째 층(프리셋 텍스트)** 을 잰 것이고, 네 티켓 만에 처음으로 채택이 나왔다. **설계의 핵심은 두 필드를 갈라 잰 것** — `examples` 는 임베딩 입력에만, `description` 은 임베딩+판정 프롬프트 양쪽에 들어가므로 합쳐 재면 어느 층이 움직였는지 모른다. 실제로 `D` 는 **해로웠다**(`fit 0건 Context` 8.20→10.00, 범위 분리 — 세 티켓 동안 σ=0 이던 지표가 처음 움직였는데 나쁜 쪽) 이고 그 해로움이 `DE` 안에서는 `E` 의 이득에 묻혀 안 보인다. `E` 는 오분류 11.00→6.90행(Δ-4.10·p=0.0001)·정상 손실 -0.80(p=0.16, 겹침)·교환비 0.20(`-210` 이 기각한 1.22 의 1/6)·**순이득 부호가 양극단 모두 양수**(+1.30/+5.30 — `-219`·`-223` 이 한 번도 못 넘은 줄). 사전 기준(범위 비중첩)은 `base` 최솟값 9 = `E` 최댓값 9 로 **못 넘었고 그 자를 무르지 않은 채** 독립 증거 넷(후보 층 정상 손실 0·홀드아웃 30건·부호 안정·5→10회 확장에서 효과 증가)으로 판단. 자기충족 방어 셋 — 수정 내용을 라벨과 무관한 원칙으로 정함(`variants.py`)·안 고친 22종 대조(`split_score.py`)·라벨 42건 밖 홀드아웃(`probe.py`). **T68**(임베딩 API 가 같은 배치로도 비결정적 — `base2` 바닥 조건으로 상시 관측, 후보 집합은 안 흔들림 확인)·**T69**(`tau_grid/score.py` 가 `rank>K` 로 밀린 행을 어느 칸에도 안 셈 — `score.py` 는 `-210` 기준점이라 안 고치고 `rank_loss.py` 로 갈라 셈) 발견. 라벨은 한 줄도 안 더함 | [프리셋 개정 리포트](2026-08-03-preset-description.md), [T68·T69](../troubleshooting/2026-08-03-preset-description.md), [tools/preset_desc/](../../tools/preset_desc/) |

> I6·I7·I8은 백엔드 아티팩트라 **back 레포** `docs/ai/implements`에 있습니다.
