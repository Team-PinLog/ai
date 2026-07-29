# Runtime Secret handoff — AI 값은 Environment 경계에서만 전달한다

`S15P11A705-154`

## 계약

정본 workflow는 `.github/workflows/seal-runtime-secrets.yml` 하나다. 수동
`workflow_dispatch`로만 실행하며 GitHub Environment `pinlog-secrets-dev`의 보호 규칙과
Secret을 사용한다. 일반 `ai-ci` workflow는 이 런타임 Secret을 읽지 않는다.

workflow가 Infra 공용 action에 전달하는 Environment Secret 이름은 다음 네 개로
고정한다.

- `GMS_API_KEY`
- `GMS_BASE_URL`
- `INTERNAL_SHARED_SECRET`
- `PINLOG_INFRA_SECRET_PR_TOKEN`

값, placeholder, base64 표현 또는 평문 파일은 레포와 로그에 남기지 않는다. 현재 Infra의
7-key runtime placeholder를 AI 레포의 source contract로 복제하지 않으며 공개 앱 설정도
이 workflow가 소유하지 않는다.

## 공급망 경계

- checkout은 실행 commit인 `${{ github.sha }}`를 사용하고 credential을 보존하지 않는다.
- workflow 권한은 `contents: read`, `id-token: write`뿐이다.
- Infra action은 commit
  `84458bf35e341b79e91ce21a3667e9d3f7454068`으로 고정한다.
- action 입력은 policy `ai-dev`와 revision `${{ github.sha }}`뿐이다.
- 별도 artifact upload나 `repository_dispatch` handoff를 두지 않는다. Infra PR 생성은
  공용 action 계약에 위임한다.

## 운영과 검증 범위

Environment Secret을 회전한 뒤 workflow를 수동 실행하고, 생성된 Infra Draft PR의 source
revision과 변경 대상을 검토한다. 실패 또는 rollback은 Infra PR을 병합하지 않거나 기존
GitOps revision으로 되돌리는 방식으로 처리한다. 이 변경은 live Secret, GitHub 설정 또는
클러스터 workload를 직접 수정하지 않는다.

레포에서는 static contract test로 경로, trigger, environment, permission, checkout SHA,
action SHA, 입력과 exact key set을 검증한다. 실제 Secret 값과 live sealing 결과는 이
변경에서 조회하거나 실행하지 않는다.
