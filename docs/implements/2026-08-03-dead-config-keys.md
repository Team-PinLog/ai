# 읽히지 않는 설정 키 전수조사 — 죽은 키 2개를 확인하고 로컬 `.env` 잔재를 정리했다

- **티켓**: S15P11A705-224
- **상태**: 완료

이 문서에서 「죽은 키」는 설정 파일에 이름이 남아 있지만 코드가 더 이상 읽지 않는
키를 뜻한다.

## 배경

발단은 `-219`가 T43(`docs/troubleshooting/2026-07-31-judge-prompt-ab.md`)으로 남긴
사실이다. `.env`의 `PINLOG_JUDGE_MODEL`은 `-175`가 `PINLOG_JUDGE_CHAIN`으로 대체해
더 이상 읽히지 않는다. 그런데 그 값이 체인 2순위(`gemini-2.5-flash`)와 같아 그럴듯해
보였다. 그 값을 읽은 것으로 보이는 `-210` 리포트 §7의 조건 표는 실제 응답 모델(체인
1순위 `gpt-4o-mini`, `-219` 실측 1,092회 전 회차)을 잘못 적었다.

이 티켓은 `PINLOG_JUDGE_MODEL` 하나가 아니라 읽히지 않는 설정 키 전체를 찾는다.
`.env`·`.env.example`·`app/core/config.py`·배포 Secret 키 목록을 대조하고, 근거는
grep이 아니라 런타임 검증으로 남긴다.

## 방법 — grep으로 끝내지 않는다

pydantic Settings는 `populate_by_name=True`이고 필드마다 `alias=`를 명시로 지정하므로,
alias 문자열이 코드에 리터럴로 있는지는 grep으로 확인할 수 있다. 하지만 그 리터럴이
실제로 `Settings()` 인스턴스에 반영되는지는 별개다. 필드가 제거됐는데 옛
`.env.example` 주석·문서에만 이름이 남아 grep이 걸리는 경우가 있고
(`PINLOG_JUDGE_MODEL`이 바로 이 경우다), 반대로 값이 반영되는데 코드에서 문자열이
다른 곳에 있어 grep이 놓치는 경우도 있다. `-210`이 "임계값이 없다"고 적었다가
실제로는 `config.py:114`에 있었던 사고가 같은 종류다.

그래서 각 후보 키에 대해 가짜 sentinel 값을 주입해 `Settings()`를 실제로 생성하고,
필드 값에 그 sentinel이 반영되는지 확인했다(스크립트는 세션 스크래치패드에 있으며
휘발성이다. 아래 "산출물" 참고). 값은 전부 `fake-*`·`sentinel-*` 형태의 무의미한
문자열이며 실제 Secret을 다루지 않았다.

## 전수조사 대상과 대조

| 출처 | 키 개수 | 비고 |
|---|---|---|
| `ai/.env` (로컬, gitignored) | 13 | 개발자 로컬 파일 |
| `.env.example` | 12 | 저장소 추적 |
| `app/core/config.py` `Settings` 필드 (alias 기준) | 15 | 정본 |
| K8s SealedSecret `ai-owner-secrets`(dev) | 7 | `infra/secrets/dev/ai-owner-secrets.sealedsecret.yaml` — 키 이름만(값은 암호화) |
| K8s SealedSecret `ai-db-credentials`(dev) | 1 | `infra/secrets/dev/ai-db-credentials.sealedsecret.yaml` |
| `infra/policy/sealedsecrets/ai-dev.yaml` `ownerSecretKeys` | 7 | 봉인 정책 — 위 7개와 일치 확인 |

`Settings`의 15개 필드는 다음과 같다(런타임 sentinel 주입으로 전부 읽힘을 확인했다):

```
database_url · gms_api_key · gms_base_url · internal_shared_secret
embedding_model · embedding_dimension · embedding_distance · embedding_profile
judge_chain · judge_vote_n
keyword_candidate_top_k · similarity_floor
search_similarity_floor · search_top_ratio
processing_expiry_sec
```

## 발견 — 읽히지 않는 키 2개

| 키 | 상태 | 근거 | 처리 |
|---|---|---|---|
| `PINLOG_JUDGE_MODEL` | **읽히지 않음** | `Settings.model_fields`에 이 alias를 가진 필드가 없음(런타임 확인). `-175`가 `PINLOG_JUDGE_CHAIN`으로 대체했고 `config.py:108`·`.env.example:53`에 이미 "옛 키는 이제 무시된다" 주석이 있음. `-219`(T43)가 실측으로 확인(-210 §7 오기 원인) | `ai/.env`(로컬)에서만 남아 있던 잔재. 이번에 주석 처리(아래 "조치" 참고). 저장소 추적 파일에는 애초에 없었음 |
| `PRESET_CACHE_TTL_SEC` | **읽히지 않음** | `Settings.model_fields`에 해당 필드 없음(런타임 확인). `2026-07-27-e2e-verification.md` F6이 이미 발견해 `ai#24`(2026-07-27, 커밋 `7d82618`)에서 `config.py`·`.env.example`에서 제거하고 `architecture.md §5`를 "재시작으로만"으로 정정함. `app/bootstrap/load_presets.py`도 `get_settings()`만 거치고 별도 `os.environ` 조회가 없어 다른 경로로도 안 읽음 | `ai/.env`(로컬)에서만 남아 있던 잔재 — **2026-07-27 정리 이후에도 로컬 파일에는 반영 안 된 채 6일 넘게 남아 있었다.** 이번에 주석 처리 |

두 키 모두 저장소 코드와 `.env.example`에서는 이미 정리가 끝나 있었다. 남은 문제는
개발자 로컬 `.env`(gitignored, 저장소 추적 밖)뿐이었다.

배포 Secret(`ai-owner-secrets`·`ai-db-credentials`, 합 8키)에는 두 키 다 없다. 즉
dev 배포는 애초에 이 죽은 값을 실어 나른 적이 없다. 배포 환경은 인프라 소관이라 값은
고치지 않았고, 확인 결과만 남긴다(계약).

## 전수조사 결과 — 이 둘 외에는 없다

나머지 13개(`.env`의 13키 중 11개는 `Settings` alias와 1:1 — `database_url`·`gms_api_key`·
`gms_base_url`·`internal_shared_secret`·`keyword_candidate_top_k`·`similarity_floor`·
`processing_expiry_sec`·`embedding_model`·`embedding_dimension`·`embedding_distance`·
`embedding_profile`)는 전부 런타임 sentinel 주입으로 반영을 확인했다. `Settings`에는
있지만 `.env`·`.env.example` 어디에도 없는 `judge_vote_n`·`search_similarity_floor`·
`search_top_ratio`도 마찬가지로 sentinel 주입으로 반영을 확인했다. 이 셋은 코드
기본값으로만 동작 중이며 죽은 키가 아니다(단순히 예시 파일에 없을 뿐이다).

## 부수 확인 — `.env` 13키 vs `.env.example` 12키의 실제 관계

계약의 전제가 정확하지 않았다. "13 vs 12이므로 example에 없는 키 하나를 찾아
필요하면 추가한다"는 단일 방향 차집합을 가정했지만, 실제로는 양방향이다.

- `.env`에만 있고 `.env.example`에는 없는 키: `PINLOG_JUDGE_MODEL`,
  `PRESET_CACHE_TTL_SEC` (2개. 위에서 확인한 대로 둘 다 죽은 키다)
- `.env.example`에만 있고 `.env`에는 없는 키: `PINLOG_JUDGE_CHAIN` (1개. 코드가 읽는
  살아 있는 키다. `.env`가 이 키를 생략하고 코드 기본값 `DEFAULT_JUDGE_CHAIN`에 의존
  중이라는 뜻이며 문제는 아니다)

11(공통) + 2(`.env`만) = 13, 11(공통) + 1(`.env.example`만) = 12로 두 총계가 각각
맞아떨어진다. 결론적으로 `.env.example`에 추가할 키는 없다. `.env`의 나머지 2키는
코드가 읽지 않아 "필요한 키"가 아니고, `.env.example`이 `.env`보다 하나 더 가진
방향은 이미 올바른 상태(살아 있는 키를 예시로 문서화)다.

## `-197` 계측 점검 — 실제 응답 모델을 로그에서 확인할 수 있는가

`app/client/_calls.py`(`S15P11A705-197`)가 이미 벤더·모델을 로그에 남긴다.

- 개별 호출: `log.log(level, "gms call kind=%s vendor=%s model=%s status=%s outcome=%s ms=%.0f", ...)`
  — 성공(`OK`)은 `DEBUG`, 실패는 `WARNING` 이상.
- 60초 창 요약: `log.info("gms window %s", ...)` — `route`(`kind:vendor`)별 outcome 카운트를
  포함.

기본 로그 레벨은 `INFO`(`app/core/logging.py`)라 개별 호출의 `model=` 필드(`DEBUG`)는
기본 배포 로그에는 보이지 않는다. 다만 창 요약은 벤더 단위로는 INFO에 항상
남고(`[judge:openai ok=N ...]`), 벤더→모델 매핑은 `PINLOG_JUDGE_CHAIN`(공개 값,
정본이 코드, P45)이 1:1로 고정하므로, 창 요약과 배포 시점 체인 설정을 대조하면 판정
모델을 특정할 수 있다.

결론은 보강 불필요다. 필요한 정보는 이미 로그에 남는다. 다만 정확한 모델명이
필요하면 로그 레벨을 일시적으로 `DEBUG`로 올리거나(운영 중 바꾸는 설정은 없다.
`-197` 설계상 의도적이다), 창 요약의 벤더와 그 시점 `PINLOG_JUDGE_CHAIN` 값을
대조해야 한다. `-210`류 오기를 막는 유일한 방법은 `.env`의 죽은 키 값이 아니라 이
로그(또는 저장 경로의 `JudgeResult.model`)를 근거로 리포트를 쓰는 것이다. T43이 이미
이 규칙을 남겼다.

## 조치

1. `ai/.env`(로컬, gitignored, **저장소에 커밋되지 않음**)에서 두 죽은 키를 주석 처리하고
   사유를 적었다:
   ```
   # [S15P11A705-224] 읽히지 않음 — PINLOG_JUDGE_CHAIN 이 대체(-175). Settings 에 이 alias 필드가 없다. 주석 처리.
   #PINLOG_JUDGE_MODEL=...
   # [S15P11A705-224] 읽히지 않음 — config.py 에서 이미 제거됨(ai#24, 2026-07-27). 로컬 .env 잔재. 주석 처리.
   #PRESET_CACHE_TTL_SEC=...
   ```
   이 파일은 이 작업자의 로컬 환경에만 적용된다. 다른 개발자의 로컬 `.env`에 같은
   잔재가 있다면 각자 정리해야 한다(README·`.env.example`은 이미 올바르므로 새로
   만드는 `.env`는 이 문제가 없다).
2. 저장소 파일(`config.py`·`.env.example`)은 변경하지 않았다. 이미 올바른 상태였음을
   이번 조사로 확인했다.
3. 배포 Secret(`ai-owner-secrets`·`ai-db-credentials`)도 변경하지 않았다. 인프라
   소관이며, 애초에 죽은 키를 담고 있지 않았다.
4. `-197` 계측·`-210`/`-219` 리포트 모두 수정하지 않았다(계약 — 확정 판단).

## 검증

- `ruff check .`
- `python -m compileall app tools`
- `pytest --cov=app --cov-branch ...` + `tools/check_coverage_gate.py`
- 위 런타임 sentinel 주입 검증(세션 스크래치패드 스크립트, 휘발 — 재현 방법은 이 문서
  "방법" 절에 남겼으므로 스크립트 자체가 없어도 재현 가능)

## 산출물과 수명

| 산출물 | 경로 | 수명 |
|---|---|---|
| 이 리포트 | `docs/implements/2026-08-03-dead-config-keys.md` | 커밋 — 영구 |
| 로컬 `.env` 주석 처리 | `ai/.env`(gitignored) | 휘발 — 이 작업자 로컬에만, 저장소에 없음 |
| 런타임 검증 스크립트 | 세션 스크래치패드 | 휘발 — 재현 절차는 이 문서에 텍스트로 보존 |
| `docs/implements/README.md`·`docs/troubleshooting/README.md` 갱신 | 저장소 | 커밋 — 영구 |
