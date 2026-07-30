# Runtime Secret handoff — AI 값은 Environment 경계에서만 전달한다

`S15P11A705-154`

> **상태: 대체(구현 주체 이관) — 설계 근거는 보존**
>
> 이 문서의 이전 판(`S15P11A705-96`, 레포 자체 workflow `seal-ai-secrets.yml`)이 기록한
> 구현은 `S15P11A705-154`에서 Infra 공용 action
> `Team-PinLog/infra/.github/actions/sealedsecret-infra-pr`으로 **대체됐다.** 아래
> [대체된 구현의 설계 근거](#대체된-구현의-설계-근거-s15p11a705-96--보존)의 판단은
> 폐기된 것이 아니라 **해당 action에 반영돼 있다.**
>
> `docs/implements/README.md`의 보존 원칙("완료된 항목도 삭제하지 않고 상태 표시만
> 갱신합니다")에 따라 이전 판을 삭제하지 않는다. 이 복원 방식은
> [ai#39](https://github.com/Team-PinLog/ai/pull/39) 코멘트에서 Infra와 합의했다.

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
  `16bfae0da4e1091df597fb89f6acf914391e11b9`으로 고정한다. 이는
  [infra#82](https://github.com/Team-PinLog/infra/pull/82) 병합 commit이며 `infra` `main`과
  동일하다. 이전 pin `84458bf3`은 병합 전 브랜치 commit이라 `main`과 diverged 상태였다.
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

## 대체된 구현의 설계 근거 (`S15P11A705-96` · 보존)

이 절은 봉인을 AI 레포 workflow가 직접 수행했을 때의 판단 기록이다. **실행 주체는 Infra
공용 action으로 옮겼고, 아래 근거는 그 action이 이어받았다.** 같은 함정을 다시 밟지 않기
위해 근거를 남긴다.

### 무엇을 풀던 문제인가

**GitHub Actions Secret은 Kubernetes Pod에 자동으로 전달되지 않는다.** 값을 클러스터까지
보내려면 누군가 한 번은 평문을 만져야 하는데, Infra는 *"key/token 값과 앱 호환성 설계를
AI owner 소유로 두고, 값을 열람하지 않은 채 암호화 Secret과 컨테이너만 배포하겠다"*는
경계를 세웠다. SealedSecret이 그 경계를 성립시킨다 — controller의 **공개키로 암호화한
manifest**만 넘기므로 Infra는 평문을 볼 수 없고, 복호화는 클러스터 안의 controller만 할
수 있다.

### 평문 YAML을 만들지 않는다 — `kubeseal --raw`

흔한 경로는 `kubectl create secret generic --dry-run=client -o yaml | kubeseal`인데, 이
파이프의 **중간 산출물이 평문 base64 Secret YAML**이다. 파이프 안에만 있다 해도 한 단계
실수(리다이렉트, `tee`, 디버깅용 `cat`)로 파일이 된다.

`--raw`는 값 하나를 받아 암호문 한 줄을 돌려준다. 평문은 셸 변수와 파이프에만 존재하고
파일·인자·로그 어디에도 남지 않는다. 변수 확장은 셸 내부에서 끝나므로 `ps`로도 보이지
않는다.

대신 SealedSecret manifest를 손으로 조립해야 한다. `echo`로 쌓는다 — **heredoc은 쓸 수
없다.** 이 스크립트는 workflow YAML의 블록 스칼라(`run: |`) 안이라 들여쓰기가 곧 블록의
경계다. heredoc 본문을 왼쪽 끝에 붙이면 workflow YAML 자체가 끊기고, 들여쓴 채 두면 그
공백이 산출 파일에 들어가 SealedSecret이 깨진다. 실제로 둘 다 겪고서 `echo` 조립으로
바꿨다.

### scope는 strict

암호문이 `pinlog-dev/ai-owner-secrets`라는 **이름·네임스페이스 조합에 묶인다.** 다른
이름으로 옮겨 쓰려면 다시 봉인해야 한다. 봉인된 값이 엉뚱한 리소스로 복사되는 것을
controller가 거부하므로, 유출된 manifest 하나가 다른 네임스페이스에서 복호화되는 경로가
없다.

### 배포 전에 실패시킬 수 있는 것은 배포 전에 실패시킨다 (기동 검증)

앱이 기동 시 fail-fast로 거르는 검사를 **봉인 시점에 미리** 돌린다. 그러지 않으면
배포하고 Pod이 죽어야 알게 된다.

```text
GMS_BASE_URL                 /gmsapi/ 포함        (embedding·judge 양쪽이 요구)
PINLOG_EMBEDDING_DIMENSION   정수
PINLOG_EMBEDDING_PROFILE     나머지 셋을 부분 문자열로 포함
                             (app/core/config.py 의 profile 정합 검사와 같은 규칙)
```

profile이 어긋난 채 배포되면 **기존 임베딩이 전부 조회 대상에서 빠진다** — 조용히 검색
결과가 비는 종류의 실패라 배포 후에 알아채기 가장 어렵다.

같은 이유로 인증서를 **SHA-256 지문으로 고정**했고
(`deploy/sealed-secrets/pinlog-dev-cert.pem`, Infra가 controller에서 제공한 공개 인증서),
만료도 함께 검사했다. **엉뚱한 공개키로 봉인하면 controller가 복호화하지 못하고, 그
실패는 배포 시점에야 드러나기 때문**이다.

산출물은 두 방향으로 검증했다. (1) 모든 `encryptedData` 값이 `Ag`로 시작하는 100자 이상
base64인가 — 봉인이 빈 문자열이나 평문을 흘렸다면 여기서 걸린다. (2) 평문이 그대로 새지
않았는가 — 단 **12자 이상만 본다.** `1536`·`cosine` 같은 짧은 값은 base64 암호문에 우연히
포함될 수 있어 거짓 양성을 만들고, 그 길이대는 (1)이 이미 덮는다.

### 봉인 대상이 7키에서 3키로 줄어든 경위

이전 판은 `GMS_API_KEY`·`GMS_BASE_URL`·`INTERNAL_SHARED_SECRET`에
`PINLOG_EMBEDDING_MODEL`·`_DIMENSION`·`_DISTANCE`·`_PROFILE` 넷을 더한 7키를 봉인했다.
**EMBEDDING 넷은 비밀이 아니다** — 모델명·차원·거리함수·프로필 식별자이고 정본은 공개
문서 [P32](../proposals/README.md)에 있다. 당시 Secret 경로로 다룬 것은 Infra가 주입
경로를 하나로 요구했고, 앱이 이 넷에 기본값을 두지 않아 누락되면 Pod이 아예 뜨지 않았기
때문이다.

이후 [ai#36](https://github.com/Team-PinLog/ai/pull/36)과 P45에서 넷을
`app/core/config.py` 기본값으로 옮겨 주입 대상에서 뺐다. 그래서 현재 계약의 이름 넷은
앱 Secret 3개 + Infra PR 토큰 1개이며, 앱이 읽는 런타임 Secret은 3개다. `DATABASE_URL`은
이전 판에서도 여기 없었다 — Infra가 별도 `ai-db-credentials`로 관리하고 `envFrom`으로
함께 연결한다.

### 이전 판이 남긴 미결 — 현재 상태

| 이전 판의 미결 | 현재 |
|---|---|
| `PINLOG_AI_INFRA_PR_TOKEN` — 앱이 읽지 않으며 전달 방식이 artifact가 아니라 PR이어야 함 | 해소. `PINLOG_INFRA_SECRET_PR_TOKEN`으로 확정되고 공용 action이 Infra Draft PR을 연다. artifact·`repository_dispatch` 경로는 제거했다 |
| 회전 절차 — 클러스터의 기존 Secret 교체 시점 | 미해소. Environment Secret 회전 후 수동 `workflow_dispatch` → Infra Draft PR 검토가 현재 절차이고, 클러스터 반영은 Infra 몫이다 |
| prod — 이 인증서는 `pinlog-dev` 전용 | 미해소. prod는 별도 controller·인증서가 필요하다. 공용 action의 policy `back-prod`와 별개로 AI prod policy는 아직 없다 |
