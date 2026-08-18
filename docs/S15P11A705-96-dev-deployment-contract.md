# S15P11A705-96 dev AI 배포 계약 — 담당자 승인 요청

- 상태: Draft / AI 담당자 승인 대기
- 범위: dev 전용 AI serving 계약. 운영 배포 계약이 아니다.
- AI owner: 이정헌
- GitHub reviewer candidate: `colosair`
- 소스 기준: `Team-PinLog/ai` `origin/main` commit `85f02f7159e567c2b820842e47290a1df904ce3e`

이 문서는 저장소에서 확인한 현재 동작과 배포 전에 owner가 결정할 항목을 분리한다. 값이 없는
항목은 임의로 채우지 않는다. 특히 자격증명, endpoint, model/profile 합의, bootstrap 증거가 하나라도
없으면 해당 Infra activation gate는 `false`를 유지한다.

## 1. Jira 고정 배포 경계

다음은 애플리케이션 소스에서 추론한 값이 아니라 S15P11A705-96의 배포 요구사항이다.

- dev 환경에만 배포한다.
- Kubernetes Service는 internal `ClusterIP`만 허용한다. Ingress, LoadBalancer, 외부 직접 노출은
  허용하지 않는다.
- DB는 별도 `pinlog_dev` DB를 사용한다. 실제 DSN이나 자격증명 값은 문서, Git, 로그에 남기지
  않고 Secret 참조로만 전달한다.
- GMS endpoint와 API key도 값을 Git에 기록하지 않고 승인된 Secret-safe 주입 경로로 전달한다.
- 이 문서는 Kubernetes/Argo CD 리소스를 생성하거나 Secret 이름·키 구조를 임의로 확정하지 않는다.

## 2. 소스에서 확인된 현재 동작

### 2.1 이미지와 실행 계약

- `.github/workflows/ai-ci.yml`의 `image-publish` job은 `main` push의 정확한 source SHA를 확인하고
  `ghcr.io/team-pinlog/ai:<40-char source SHA>`를 게시한다. `Verify published image digest`와
  `Create verified image provenance` step은 registry digest와 `source_repository`, `source_sha`,
  `image_repository`, `digest` provenance를 검증한다.
- `Dockerfile`은 `python:3.12-slim`, non-root UID `10001`, port `8000`, command
  `uvicorn app.main:app --host 0.0.0.0 --port 8000`을 정의한다.
- 저장소에는 Kubernetes 또는 Argo CD 배포 manifest가 없다. CPU/GPU request/limit, replica 수,
  rollout, PDB, cache/storage volume 계약도 현재 소스에는 없다. 이미지에는 GPU runtime/model
  artifact가 없고 외부 GMS 호출형 클라이언트만 있다(`app/client/embedding_client.py:EmbeddingClient`,
  `app/client/llm_client.py:LLMClient`). 따라서 현 소스 기준 GPU scheduling을 요구할 근거는 없다.

### 2.2 source-derived 환경변수 이름

단일 읽기 지점은 `app/core/config.py:Settings`이다. 아래는 **이름만** 기록하며 값은 이 문서의
계약이 아니다.

| 분류 | 정확한 환경변수 이름 | 현재 소스 동작 / 주입 경계 |
|---|---|---|
| DB Secret | `DATABASE_URL` | 필수. `app/core/db.py:Database`에 전달된다. dev에서는 `pinlog_dev`를 가리키는 Secret 참조여야 한다. |
| GMS Secret | `GMS_API_KEY` | 필수. embedding에는 Bearer header, judge에는 `x-goog-api-key`로 사용된다. 값 출력 금지. |
| GMS endpoint | `GMS_BASE_URL` | 필수. embedding base URL이며 judge native URL의 root도 여기서 파생한다. 값은 승인된 secret-safe handoff로만 주입한다. |
| Embedding 설정 | `PINLOG_EMBEDDING_MODEL` | 필수, 기본값 없음. |
| Embedding 설정 | `PINLOG_EMBEDDING_DIMENSION` | 필수, 기본값 없음. 응답 vector 길이 검증에 쓰인다. |
| Embedding 설정 | `PINLOG_EMBEDDING_DISTANCE` | 필수, 기본값 없음. |
| Embedding 설정 | `PINLOG_EMBEDDING_PROFILE` | 필수, 기본값 없음. model/dimension/distance 토큰 불일치 시 기동 실패한다. |
| Judge 설정 | `PINLOG_JUDGE_MODEL` | 소스 기본값이 존재하지만 dev 배포값은 owner 승인 전 확정하지 않는다. |
| 후보 설정 | `KEYWORD_CANDIDATE_TOP_K` | 소스 기본값이 존재한다. 배포 override 여부는 owner 결정이다. |
| 후보 설정 | `SIMILARITY_FLOOR` | 소스 기본값이 존재한다. 배포 override 여부는 owner 결정이다. |
| 복구 설정 | `PROCESSING_EXPIRY_SEC` | 소스 기본값이 존재한다. Spring 재스캔 값과 동일해야 한다는 주석 계약이다. |
| 내부 인증 Secret | `INTERNAL_SHARED_SECRET` | 필수. `/internal/*` 요청의 `X-Internal-Secret`과 비교한다(`app/core/security.py:SharedSecretMiddleware`). 값 출력 금지. |

Infra의 현재 `ai-runtime-secrets` 계약은 필수 8개 key(`DATABASE_URL`, `GMS_API_KEY`,
`GMS_BASE_URL`, `PINLOG_EMBEDDING_MODEL`, `PINLOG_EMBEDDING_DIMENSION`,
`PINLOG_EMBEDDING_DISTANCE`, `PINLOG_EMBEDDING_PROFILE`, `INTERNAL_SHARED_SECRET`)만
허용한다. 소스가 추가로 소비하는 `PINLOG_JUDGE_MODEL`, `KEYWORD_CANDIDATE_TOP_K`,
`SIMILARITY_FLOOR`, `PROCESSING_EXPIRY_SEC`는 이 Secret에 임의 추가하지 않는다. AI/Infra
owner가 각 항목을 source default로 둘지 비민감 ConfigMap으로 주입할지, 그리고 변경 시 restart
영향을 승인해야 한다. 이 4개 항목의 배치가 정해지기 전에는 exact runtime configuration schema가
완료됐다고 간주하지 않는다.

`GMS_BASE_URL` 호환성은 문자열 형식까지 포함한다. `EmbeddingClient._embed_batch`는 base 뒤에
`/embeddings`를 붙이고, `LLMClient.__init__`은 `/gmsapi/` 경계를 기준으로 native root를 파생한 뒤
`LLMClient.judge`가 Gemini `generateContent` 경로를 조립한다. 따라서 endpoint를 변경하거나 다른
gateway 형식을 주입하는 결정은 AI owner가 두 client 경로 모두와의 호환성을 확인해야 한다.

### 2.3 model/profile 소유권과 fail-closed 동작

- `app/core/config.py:Settings._profile_consistency`는 embedding model, dimension, distance가 profile
  문자열에 모두 포함되지 않으면 기동을 실패시킨다.
- `app/client/embedding_client.py:EmbeddingClient._embed_batch`는 응답 차원이 설정과 다르면 저장
  전 실패한다.
- `app/repository/keyword_preset_repo.py:_LOAD_ACTIVE`는 활성 상태이면서 현재 embedding profile과
  일치하는 preset만 읽는다.
- `app/main.py:lifespan`은 DB 연결과 preset cache 적재를 수행하며 적재 0건이면 기동을 중단한다.
- 저장소 명세상 Context와 Preset은 같은 embedding profile을 사용해야 한다
  (`docs/spec/model-profile.md`의 "MVP 모델", "Profile 불일치 시 동작"). judge model 식별은 embedding
  profile과 별도이다(`docs/spec/keyword-preset.md`의 `model_profile`).

배포값과 호환성의 승인 owner는 이정헌이다. Infra는 값을 선택하거나 소스 기본값을 승인으로
간주하지 않는다. AI owner의 명시적 model/profile/judge 승인과 GMS 실호출 호환성 증거가 없으면
model/profile activation gate는 `false`다.

### 2.4 Keyword Preset bootstrap, 멱등성, provenance, 복구

소스가 증명하는 정확한 command는 다음 하나다(`app/bootstrap/load_presets.py` module docstring과
`README.md` "Preset 부트스트랩").

```bash
python -m app.bootstrap.load_presets
```

현재 구현의 provenance 흐름은 다음과 같다.

1. metadata source는 `data/keyword_preset.yaml`이다.
2. embedding 입력 구성은 `app/client/embedding_client.py:preset_embed_text`가 정한다.
3. embedding model/dimension과 GMS handoff는 `app/core/config.py:Settings`에서 주입된다.
4. 저장 profile은 `PINLOG_EMBEDDING_PROFILE`, preset version은 YAML 항목의 `version` 또는 소스 기본
   처리로 결정된다(`app/bootstrap/load_presets.py:load`).
5. `_UPSERT`는 현재 YAML에 존재하는 `id` 충돌 시 metadata, embedding, profile, visibility, active
   flag, version을 갱신하므로 같은 입력의 **PK-upsert 반복 실행**은 가능하다.

`load()`는 모든 embedding 응답을 받은 뒤 DB transaction을 열어 preset 전체를 UPSERT한다.
GMS 단계 실패 시 DB 쓰기를 시작하지 않고, DB 단계 실패 시 transaction이 rollback된다
(`app/core/db.py:Database.transaction`). 따라서 실패 원인을 해결한 뒤 같은 exact command를 다시
실행하는 것이 source-proven recovery 절차다. 서비스 startup 자체는 bootstrap을 실행하지 않으며,
cache 0건이면 실패한다.

그러나 이것은 exact-set 멱등성을 증명하지 않는다. YAML에서 제거된 ID를 비활성화하거나 삭제하지
않으므로 stale active row가 남을 수 있다. 감사 기준 source의 `data/keyword_preset.yaml`은 27개
preset이고 SHA-256은
`204824bd37e6e1f056f1636ec1bb86d2585994a8cdbfd99bb188096cfca04034`지만, loader는 이 hash나
source commit을 계산·저장하지 않고 expected count/hash를 사후 검증하지도 않는다. 따라서
exact-set reconciliation, SHA-derived version persistence, provenance receipt와 post-write verification이
구현되거나 owner가 별도 안전 절차를 승인하기 전에는 versioned/idempotent bootstrap이 완료됐다고
표현하지 않으며 bootstrap gate는 `false`다.

다만 저장소에는 배포 시 command 실행 주체(Argo hook, Job 등), 동시 실행 잠금, retry 횟수/backoff,
완료 증거의 보관 위치가 없다. Infra는 owner가 승인한 immutable image digest/source SHA와 seed source,
profile, command 성공 결과를 연결한 실행 증거 없이는 bootstrap gate를 `false`로 유지한다. 자격증명
값이나 endpoint 값은 그 증거에 포함하지 않는다.

### 2.5 startup/readiness/liveness/metrics

- 현재 존재하는 endpoint는 `app/main.py:create_app`의 unauthenticated `GET /health` 하나이며 응답은
  정적 `{"status":"ok"}`다. `SharedSecretMiddleware`는 `/internal/*`에만 적용된다.
- FastAPI lifespan 완료 전에는 DB pool 연결과 현재 profile의 non-empty preset cache가 요구된다.
  그러나 `/health` handler 자체는 요청마다 DB, GMS 또는 cache 상태를 재검증하지 않는다.
- 소스에는 readiness와 liveness를 구분한 endpoint가 없고 `/metrics` route나 Prometheus exporter도
  없다. 따라서 `/health`를 readiness/liveness/startup 모두에 재사용할지, 별도 endpoint를 앱에
  요청할지는 owner 결정이 필요하다. `/metrics` scrape gate는 현재 `false`다.
- GMS 연결은 startup에서 검증하지 않는다. bootstrap 또는 실제 요청에서만 외부 호출이 발생한다.

### 2.6 외부 API retry/error classification known gap

현재 두 GMS client는 httpx 요청을 한 번만 수행하며 retry/backoff가 없다. 또한
`EmbeddingClient._embed_batch`는 `429`를 permanent error로 분류하고,
`LLMClient.judge`는 인증 오류를 포함한 모든 non-200을 transient error로 분류한다.
`docs/implements/2026-07-28-s1-implementation-recovery.md`는 이를 spec과의 구현 불일치로 기록하고
별도 Bug `S15P11A705-121`로 추적한다. 이 Draft는 해당 동작을 정상 계약으로 승인하지 않는다.
배포 전 수용 여부, 수정 선행 여부와 관측/alert 기준을 AI owner가 결정해야 한다.

## 3. 배포 전 AI owner 체크리스트

이정헌 owner는 아래를 명시적으로 승인하거나 수정 요청해야 한다.

- [ ] dev가 `ClusterIP` only이며 client 직접 접근/Ingress가 없음을 승인
- [ ] `DATABASE_URL`이 별도 `pinlog_dev`를 가리키되 값은 Secret 참조로만 전달됨을 승인
- [ ] 위 source-derived 환경변수 이름이 완전하며 key rename/추가가 없음을 승인
- [ ] source-default 4개 설정의 ConfigMap/default 배치와 restart 영향을 승인
- [ ] `GMS_BASE_URL`과 `GMS_API_KEY`의 비공개 handoff 방식 및 두 client의 URL/auth 호환성을 승인
- [ ] embedding model/dimension/distance/profile 조합과 judge model을 명시적으로 승인
- [ ] Spring과 `PINLOG_EMBEDDING_PROFILE`, `PROCESSING_EXPIRY_SEC` 호환 책임/검증 주체를 승인
- [ ] exact bootstrap command, 실행 주체/시점, retry 정책, provenance와 성공 증거 형식을 승인
- [ ] exact-set reconciliation과 SHA-derived version/provenance가 없음을 해결하거나 안전한 선행 절차를 승인
- [ ] `/health`의 startup/readiness/liveness 사용 여부와 probe timing/failure threshold를 승인
- [ ] `/metrics` 부재를 수용할지 앱 변경을 요청하고 필요한 metric/scrape 계약을 제시
- [ ] 외부 API retry/error classification gap(`S15P11A705-121`)의 수정 선행 또는 dev 수용 기준을 결정
- [ ] CPU request/limit, memory request/limit, replica/rollout/rollback 기준을 제시 또는 승인

## 4. Owner 응답 표

리뷰어 `colosair`는 아래 표에 owner 답변과 근거 링크를 확인한다. Secret 또는 내부 endpoint 값은
답변에 쓰지 않는다.

| 승인 항목 | Owner 응답 (`승인` / `수정 필요` / `보류`) | 비밀값 없는 결정·근거 | Infra gate |
|---|---|---|---|
| dev-only, internal `ClusterIP`, no Ingress |  |  | `false` |
| 별도 `pinlog_dev` DB와 Secret-ref handoff |  |  | `false` |
| source-derived env key names |  |  | `false` |
| source-default 4개 설정의 ConfigMap/default 배치 |  |  | `false` |
| GMS endpoint/API key handoff와 client 호환성 |  |  | `false` |
| embedding model/dimension/distance/profile |  |  | `false` |
| judge model 및 compatibility ownership |  |  | `false` |
| preset bootstrap 실행·retry·provenance 증거 |  |  | `false` |
| preset exact-set/version provenance gap |  |  | `false` |
| startup/readiness/liveness probe 계약 |  |  | `false` |
| `/metrics` 부재 수용 또는 앱 변경 요청 |  |  | `false` |
| 외부 API retry/error classification gap |  |  | `false` |
| resource/replica/rollout/rollback 운영값 |  |  | `false` |

## 5. Activation 규칙

Infra는 표의 각 gate를 독립적으로 검증한다. 특히 다음은 예외 없이 fail-closed다.

- credential의 승인된 Secret-ref handoff가 없거나 Secret 값 노출 위험이 있으면 배포하지 않는다.
- model/profile/judge 합의와 endpoint 호환성 증거가 없으면 배포하지 않는다.
- bootstrap provenance와 성공 증거가 없으면 serving rollout을 활성화하지 않는다.
- stale preset을 배제하는 exact-set 검증과 source SHA/version 증거가 없으면 bootstrap 완료로 판정하지 않는다.
- retry/error classification gap을 숨기거나 정상 동작으로 간주하지 않는다.
- readiness/liveness/metrics 부재를 임의 endpoint나 가정으로 보완하지 않는다. 필요한 앱 변경은
  AI owner가 별도 작업으로 요청해야 한다.

이 문서 승인은 live 변경 승인이 아니다. 실제 GitOps 변경은 별도 PR, immutable image digest,
rollback 가능한 이전 revision, dry-run/static 검증 증거를 갖춰야 한다.
